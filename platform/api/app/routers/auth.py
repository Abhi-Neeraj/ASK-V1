from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.errors import ApiError, not_found
from app.models import AuthSession, Company, User
from app.schemas.auth import (
    ChangePasswordIn,
    EmailTokenIn,
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    SessionOut,
    UserOut,
    user_out,
)
from app.security.cookies import clear_session_cookies, set_session_cookies
from app.security.deps import (
    SESSION_COOKIE,
    Authed,
    DbDep,
    LimiterDep,
    SettingsDep,
    check_origin,
    client_ip,
)
from app.security.passwords import hash_password, verify_password
from app.security.ratelimit import enforce
from app.security.tokens import hash_token
from app.services import accounts
from app.services.audit import record_audit
from app.services.email import password_reset_email, verify_email_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _audit_failed_login(db: Session, email: str, ip: str | None) -> None:
    """Show a company's administrators that someone is guessing at one of their members' passwords.

    Addresses that belong to nobody are not recorded: there is no company to show the entry to, and
    anonymous probing must not be able to fill the table. Logins are rate limited, which bounds this.
    """
    account = db.scalar(select(User).where(User.email == email))
    membership = accounts.primary_membership(db, account.id) if account else None
    if account is None or membership is None:
        return
    record_audit(
        db, action="auth.login_failed", company_id=membership.company_id, actor_user_id=account.id, ip=ip
    )
    db.commit()


@router.post("/login", response_model=UserOut)
def login(
    body: LoginIn, request: Request, response: Response, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> UserOut:
    check_origin(request, settings)
    ip = client_ip(request, settings)
    email = accounts.normalize_email(body.email)
    # Two limits: one per address and source (stops guessing one account), one per source (stops spraying).
    enforce(limiter, f"login:{ip}:{hash_token(email)[:16]}", settings.login_attempts_per_minute, 60)
    enforce(limiter, f"login-ip:{ip}", settings.login_attempts_per_minute * 5, 60)

    user = accounts.authenticate(db, email, body.password)
    membership = accounts.primary_membership(db, user.id) if user else None
    if user is None or membership is None:
        _audit_failed_login(db, email, ip)
        raise ApiError(401, "invalid_credentials", "The email or password is incorrect.")

    raw, _ = accounts.start_session(
        db, settings, user, membership.company_id, ip, request.headers.get("user-agent")
    )
    record_audit(db, action="auth.login", company_id=membership.company_id, actor_user_id=user.id, ip=ip)
    db.commit()
    set_session_cookies(response, settings, raw)
    company = db.get(Company, membership.company_id)
    assert company is not None  # noqa: S101  (foreign key guarantees it)
    return user_out(user, membership, company)


@router.post("/logout", status_code=204)
def logout(request: Request, db: DbDep, settings: SettingsDep) -> Response:
    """Always ends in a signed-out browser. If a valid session is presented it is revoked server-side."""
    from app.security.deps import CSRF_HEADER, check_origin
    from app.security.tokens import constant_time_equal, csrf_token

    response = Response(status_code=204)
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
        if session is not None and session.revoked_at is None:
            check_origin(request, settings)
            expected = csrf_token(raw, settings.secret_key.get_secret_value())
            if not constant_time_equal(request.headers.get(CSRF_HEADER, ""), expected):
                raise ApiError(
                    403, "csrf_failed", "The request could not be verified. Reload the page and try again."
                )
            session.revoked_at = timeutil.utcnow()
            record_audit(
                db,
                action="auth.logout",
                company_id=session.company_id,
                actor_user_id=session.user_id,
                ip=client_ip(request, settings),
            )
            db.commit()
    clear_session_cookies(response, settings)
    return response


@router.get("/me", response_model=UserOut)
def me(ctx: Authed, response: Response, db: DbDep, settings: SettingsDep) -> UserOut:
    # Re-issue the CSRF cookie so the page can always read the current token.
    set_session_cookies(response, settings, ctx.session_token)
    company = db.get(Company, ctx.company_id)
    assert company is not None  # noqa: S101
    membership = accounts.primary_membership(db, ctx.user.id)
    assert membership is not None  # noqa: S101
    return user_out(ctx.user, membership, company)


# --- password reset (public) ---------------------------------------------------------------------


@router.post("/password-reset/request", status_code=204)
def request_password_reset(
    body: PasswordResetRequestIn, request: Request, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> Response:
    """Always answers 204, so it cannot be used to find out which addresses have accounts."""
    check_origin(request, settings)
    ip = client_ip(request, settings)
    email = accounts.normalize_email(body.email)
    enforce(limiter, f"reset-ip:{ip}", settings.reset_requests_per_hour, 3600)
    enforce(limiter, f"reset-email:{hash_token(email)[:16]}", settings.reset_requests_per_hour, 3600)

    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.is_active:
        raw = accounts.issue_email_token(
            db, user, "password_reset", timedelta(minutes=settings.reset_ttl_minutes)
        )
        membership = accounts.primary_membership(db, user.id)
        record_audit(
            db,
            action="auth.password_reset_requested",
            company_id=membership.company_id if membership else None,
            actor_user_id=user.id,
            ip=ip,
        )
        db.commit()
        link = f"{settings.web_base_url.rstrip('/')}/reset-password?token={raw}"
        request.app.state.email.send(password_reset_email(user.email, link, settings.reset_ttl_minutes))
    return Response(status_code=204)


@router.post("/password-reset/confirm", status_code=204)
def confirm_password_reset(
    body: PasswordResetConfirmIn, request: Request, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> Response:
    check_origin(request, settings)
    ip = client_ip(request, settings)
    enforce(limiter, f"reset-confirm:{ip}", 20, 3600)
    user = accounts.consume_email_token(db, body.token, "password_reset")
    accounts.require_good_password(body.password, user.email)
    user.password_hash = hash_password(body.password)
    # Whoever had the old password must not stay signed in.
    accounts.revoke_user_sessions(db, user.id)
    membership = accounts.primary_membership(db, user.id)
    record_audit(
        db,
        action="auth.password_reset",
        company_id=membership.company_id if membership else None,
        actor_user_id=user.id,
        ip=ip,
    )
    db.commit()
    return Response(status_code=204)


# --- signed-in account actions -------------------------------------------------------------------


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordIn, ctx: Authed, db: DbDep) -> Response:
    if not verify_password(ctx.user.password_hash, body.current_password):
        raise ApiError(
            422,
            "validation_error",
            "The current password is not correct.",
            {"currentPassword": "The current password is not correct."},
        )
    accounts.require_good_password(body.new_password, ctx.user.email)
    ctx.user.password_hash = hash_password(body.new_password)
    accounts.revoke_user_sessions(db, ctx.user.id, keep=ctx.session.id)
    record_audit(
        db, action="auth.password_changed", company_id=ctx.company_id, actor_user_id=ctx.user.id, ip=ctx.ip
    )
    db.commit()
    return Response(status_code=204)


@router.post("/verify-email/request", status_code=204)
def request_email_verification(
    request: Request, ctx: Authed, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> Response:
    enforce(limiter, f"verify:{ctx.user.id}", 5, 3600)
    if ctx.user.email_verified_at is None:
        raw = accounts.issue_email_token(
            db, ctx.user, "email_verify", timedelta(hours=settings.verify_ttl_hours)
        )
        db.commit()
        link = f"{settings.web_base_url.rstrip('/')}/verify-email?token={raw}"
        request.app.state.email.send(verify_email_email(ctx.user.email, link, settings.verify_ttl_hours))
    return Response(status_code=204)


@router.post("/verify-email/confirm", status_code=204)
def confirm_email_verification(
    body: EmailTokenIn, request: Request, db: DbDep, settings: SettingsDep
) -> Response:
    check_origin(request, settings)
    user = accounts.consume_email_token(db, body.token, "email_verify")
    user.email_verified_at = timeutil.utcnow()
    db.commit()
    return Response(status_code=204)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(ctx: Authed, db: DbDep) -> list[SessionOut]:
    now = timeutil.utcnow()
    rows = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == ctx.user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .order_by(AuthSession.last_seen_at.desc())
    )
    return [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            current=row.id == ctx.session.id,
            user_agent=row.user_agent,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(session_id: uuid.UUID, ctx: Authed, db: DbDep) -> Response:
    session = db.get(AuthSession, session_id)
    # Only your own sessions exist as far as you are concerned.
    if session is None or session.user_id != ctx.user.id:
        raise not_found("session")
    session.revoked_at = timeutil.utcnow()
    record_audit(
        db,
        action="auth.session_revoked",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="session",
        object_id=session.id,
        ip=ctx.ip,
    )
    db.commit()
    return Response(status_code=204)
