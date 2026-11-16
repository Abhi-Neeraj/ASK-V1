"""Request authentication and authorization.

Every route that needs a signed-in user depends on `Authed`, `Manager` or `Admin`. CSRF and the
Origin check are enforced inside `get_ctx` for every state-changing method, so a new route cannot
forget them. Device routes use `get_device_ctx` (a bearer credential) and are exempt from CSRF
because browsers never send that header on their own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import get_db
from app.errors import ApiError, forbidden
from app.models import AuthSession, Device, DeviceCredential, Membership, User
from app.security.ratelimit import RateLimiter
from app.security.tokens import constant_time_equal, csrf_token, hash_token

SESSION_COOKIE = "ask_session"
CSRF_COOKIE = "ask_csrf"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MANAGE_ROLES = ("admin", "manager")

# How often the "last seen" timestamps are written, to avoid a database write on every request.
_TOUCH_INTERVAL = timedelta(seconds=60)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.limiter
    return limiter


SettingsDep = Annotated[Settings, Depends(get_settings)]
LimiterDep = Annotated[RateLimiter, Depends(get_limiter)]
DbDep = Annotated[Session, Depends(get_db)]


def client_ip(request: Request, settings: Settings) -> str | None:
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [part.strip() for part in forwarded.split(",") if part.strip()]
            if hops:
                # Each proxy we control appends the address it saw. Whatever a client sent itself is at the
                # front,
                # so counting from the end cannot be spoofed; the first entry can.
                return hops[max(len(hops) - settings.trusted_proxy_hops, 0)][:64]
    return request.client.host[:64] if request.client else None


def check_origin(request: Request, settings: Settings) -> None:
    """Browsers always send Origin on cross-site writes. If it is present it must be ours."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.web_origins:
        raise ApiError(403, "bad_origin", "This request came from an unexpected origin.")


@dataclass(frozen=True)
class Ctx:
    """The authenticated user, the company they are acting in, and their role there."""

    user: User
    company_id: uuid.UUID
    role: str
    session: AuthSession
    session_token: str
    ip: str | None

    @property
    def can_manage(self) -> bool:
        return self.role in MANAGE_ROLES


def get_ctx(request: Request, db: DbDep, settings: SettingsDep) -> Ctx:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")

    now = timeutil.utcnow()
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
    idle_limit = timedelta(hours=settings.session_idle_hours)
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or now - session.last_seen_at > idle_limit
    ):
        raise ApiError(401, "unauthenticated", "Your session has ended. Sign in again.")

    user = db.get(User, session.user_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == session.user_id, Membership.company_id == session.company_id
        )
    )
    if user is None or not user.is_active or membership is None:
        raise ApiError(401, "unauthenticated", "Your session has ended. Sign in again.")

    if request.method in UNSAFE_METHODS:
        check_origin(request, settings)
        supplied = request.headers.get(CSRF_HEADER, "")
        expected = csrf_token(raw, settings.secret_key.get_secret_value())
        if not supplied or not constant_time_equal(supplied, expected):
            raise ApiError(
                403, "csrf_failed", "The request could not be verified. Reload the page and try again."
            )

    if now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()

    return Ctx(
        user=user,
        company_id=session.company_id,
        role=membership.role,
        session=session,
        session_token=raw,
        ip=client_ip(request, settings),
    )


def require_roles(*roles: str):  # type: ignore[no-untyped-def]
    def dependency(ctx: Annotated[Ctx, Depends(get_ctx)]) -> Ctx:
        if ctx.role not in roles:
            raise forbidden()
        return ctx

    return dependency


Authed = Annotated[Ctx, Depends(get_ctx)]
Manager = Annotated[Ctx, Depends(require_roles(*MANAGE_ROLES))]
Admin = Annotated[Ctx, Depends(require_roles("admin"))]


@dataclass(frozen=True)
class DeviceCtx:
    device: Device
    company_id: uuid.UUID
    credential_id: uuid.UUID
    ip: str | None


_BAD_DEVICE = ApiError(401, "invalid_device_credential", "The device credential was not accepted.")


def get_device_ctx(request: Request, db: DbDep, settings: SettingsDep, limiter: LimiterDep) -> DeviceCtx:
    """Authenticate `Authorization: Bearer <device public id>.<secret>`."""
    ip = client_ip(request, settings)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    public_id, _, secret = token.partition(".")
    if scheme.lower() != "bearer" or not public_id or not secret:
        raise _BAD_DEVICE

    device = db.scalar(select(Device).where(Device.public_id == public_id))
    presented = hash_token(secret)
    matched: DeviceCredential | None = None
    if device is not None and device.status == "active":
        for credential in db.scalars(
            select(DeviceCredential).where(
                DeviceCredential.device_id == device.id, DeviceCredential.revoked_at.is_(None)
            )
        ):
            if constant_time_equal(credential.secret_hash, presented):
                matched = credential
    if device is None or matched is None:
        # Slow down guessing without revealing whether the device exists.
        from app.security.ratelimit import enforce

        enforce(limiter, f"device-auth:{ip}", 30, 60)
        raise _BAD_DEVICE

    now = timeutil.utcnow()
    if matched.last_used_at is None or now - matched.last_used_at > _TOUCH_INTERVAL:
        matched.last_used_at = now
        db.commit()
    return DeviceCtx(device=device, company_id=device.company_id, credential_id=matched.id, ip=ip)


DeviceAuthed = Annotated[DeviceCtx, Depends(get_device_ctx)]
