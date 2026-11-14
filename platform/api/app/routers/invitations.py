from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app import timeutil
from app.errors import not_found
from app.models import Company, Invitation, Membership, User
from app.schemas.auth import (
    AcceptInvitationIn,
    InvitationCreateIn,
    InvitationInfo,
    InvitationOut,
    MemberOut,
    UserOut,
    user_out,
)
from app.security.cookies import set_session_cookies
from app.security.deps import Authed, DbDep, LimiterDep, Manager, SettingsDep, check_origin, client_ip
from app.security.ratelimit import enforce
from app.services import accounts
from app.services.audit import record_audit
from app.services.email import invitation_email

router = APIRouter(tags=["invitations"])


def _accept_url(settings: SettingsDep, raw: str) -> str:
    return f"{settings.web_base_url.rstrip('/')}/register?token={raw}"


# --- public: the person who received an invitation -----------------------------------------------


@router.get("/api/invitations/{token}", response_model=InvitationInfo)
def look_up_invitation(
    token: str, db: DbDep, limiter: LimiterDep, request: Request, settings: SettingsDep
) -> InvitationInfo:
    enforce(limiter, f"invite-lookup:{client_ip(request, settings)}", 30, 60)
    invitation = accounts.find_open_invitation(db, token)
    company = db.get(Company, invitation.company_id)
    assert company is not None  # noqa: S101
    return InvitationInfo(
        email=invitation.email,
        company_name=company.name,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )


@router.post("/api/invitations/{token}/accept", response_model=UserOut)
def accept_invitation(
    token: str,
    body: AcceptInvitationIn,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> UserOut:
    check_origin(request, settings)
    ip = client_ip(request, settings)
    enforce(limiter, f"invite-accept:{ip}", 10, 60)
    user, membership, raw_session = accounts.accept_invitation(
        db,
        settings,
        token,
        name=body.name,
        password=body.password,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    set_session_cookies(response, settings, raw_session)
    company = db.get(Company, membership.company_id)
    assert company is not None  # noqa: S101
    return user_out(user, membership, company)


# --- signed-in: managing invitations and members -------------------------------------------------


def _invitation_out(invitation: Invitation, accept_url: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted=invitation.accepted_at is not None,
        accept_url=accept_url,
    )


@router.post("/api/invitations", response_model=InvitationOut, status_code=201)
def create_invitation(
    body: InvitationCreateIn,
    request: Request,
    ctx: Manager,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> InvitationOut:
    enforce(limiter, f"invite:{ctx.user.id}", settings.invites_per_hour, 3600)
    invitation, raw = accounts.create_invitation(db, settings, ctx, body.email, body.role)
    company = db.get(Company, ctx.company_id)
    assert company is not None  # noqa: S101
    db.commit()
    link = _accept_url(settings, raw)
    request.app.state.email.send(
        invitation_email(invitation.email, company.name, link, settings.invite_ttl_hours)
    )
    # When mail really goes out, the link reaches the invited person and nobody else. Handing it to whoever
    # invites would let them accept it themselves, with a password of their choosing, as an account verified
    # as belonging to an address they do not own. Only the development backends show it.
    return _invitation_out(invitation, accept_url=None if settings.email_backend == "smtp" else link)


@router.get("/api/invitations", response_model=list[InvitationOut])
def list_invitations(ctx: Manager, db: DbDep) -> list[InvitationOut]:
    now = timeutil.utcnow()
    rows = db.scalars(
        select(Invitation)
        .where(
            Invitation.company_id == ctx.company_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
    )
    return [_invitation_out(row) for row in rows]


@router.delete("/api/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: uuid.UUID, ctx: Manager, db: DbDep) -> Response:
    invitation = db.get(Invitation, invitation_id)
    if invitation is None or invitation.company_id != ctx.company_id:
        raise not_found("invitation")
    invitation.revoked_at = timeutil.utcnow()
    record_audit(
        db,
        action="invitation.revoked",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="invitation",
        object_id=invitation.id,
        ip=ctx.ip,
    )
    db.commit()
    return Response(status_code=204)


@router.get("/api/members", response_model=list[MemberOut])
def list_members(ctx: Authed, db: DbDep) -> list[MemberOut]:
    """People in the caller's company, for assigning devices. Everyone gets names and roles; email addresses
    only go to managers and administrators, who invite people and assign lockets."""
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.company_id == ctx.company_id)
        .order_by(User.name)
    )
    return [
        MemberOut(
            id=user.id,
            name=user.name,
            email=user.email if ctx.can_manage else None,
            role=m.role,
            is_active=user.is_active,
        )
        for user, m in rows
        # Someone who has been switched off is shown to managers, who may need to switch them back on.
        if user.is_active or ctx.can_manage
    ]
