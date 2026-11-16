"""Companies, users, sessions, invitations and one-time email tokens.

Membership of a company is created in exactly two places: `create_company_with_admin` (a company's
first administrator, run by an operator) and `accept_invitation` (a person who holds a valid invitation
sent to their address). A matching email domain never grants access.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import rows_affected
from app.errors import ApiError
from app.models import AuthSession, Company, EmailToken, Invitation, Membership, User
from app.security.deps import Ctx
from app.security.passwords import (
    burn_password_time,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from app.security.tokens import hash_token, new_token
from app.services.audit import record_audit

INVALID_INVITATION = ApiError(404, "invitation_not_found", "This invitation link is not valid.")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def require_good_password(password: str, email: str | None = None) -> None:
    problem = password_problem(password, email=email)
    if problem:
        raise ApiError(422, "validation_error", "Choose a stronger password.", {"password": problem})


def create_company_with_admin(
    db: Session, *, company_name: str, email: str, name: str, password: str, verified: bool = True
) -> tuple[Company, User]:
    email = normalize_email(email)
    require_good_password(password, email)
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise ApiError(409, "account_exists", "An account with that email already exists.")
    now = timeutil.utcnow()
    company = Company(name=company_name.strip())
    user = User(
        email=email,
        name=name.strip(),
        password_hash=hash_password(password),
        email_verified_at=now if verified else None,
    )
    db.add_all([company, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=company.id, role="admin"))
    record_audit(
        db,
        action="company.created",
        company_id=company.id,
        actor_user_id=user.id,
        object_type="company",
        object_id=company.id,
    )
    db.commit()
    return company, user


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Check credentials in constant-ish time whether or not the account exists."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        burn_password_time(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def primary_membership(db: Session, user_id: uuid.UUID) -> Membership | None:
    """Version 1 puts each person in one company; if that ever changes this picks the oldest."""
    return db.scalar(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at).limit(1)
    )


def start_session(
    db: Session, settings: Settings, user: User, company_id: uuid.UUID, ip: str | None, user_agent: str | None
) -> tuple[str, AuthSession]:
    now = timeutil.utcnow()
    raw = new_token()
    session = AuthSession(
        user_id=user.id,
        company_id=company_id,
        token_hash=hash_token(raw),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
    )
    user.last_login_at = now
    db.add(session)
    db.flush()
    return raw, session


def revoke_user_sessions(db: Session, user_id: uuid.UUID, *, keep: uuid.UUID | None = None) -> None:
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=timeutil.utcnow())
    )
    if keep is not None:
        statement = statement.where(AuthSession.id != keep)
    db.execute(statement)


# --- members ------------------------------------------------------------------------------------


def change_member(
    db: Session, ctx: Ctx, user_id: uuid.UUID, *, role: str | None, is_active: bool | None
) -> tuple[User, Membership]:
    """An administrator changes someone's role, or switches them off or back on.

    Nobody changes themselves, so there is always an administrator left to undo a mistake. Switching someone
    off ends every sign-in they have; their role is read from the database on each request, so a role change
    needs no sign-out. Everything is recorded."""
    membership = db.scalar(
        select(Membership).where(Membership.company_id == ctx.company_id, Membership.user_id == user_id)
    )
    user = db.get(User, user_id) if membership is not None else None
    if membership is None or user is None:
        raise ApiError(404, "not_found", "That person was not found.")
    if user.id == ctx.user.id:
        raise ApiError(
            409,
            "cannot_change_self",
            "You cannot change your own role or access. Ask another administrator.",
        )

    if role is not None and role != membership.role:
        before, membership.role = membership.role, role
        record_audit(
            db,
            action="member.role_changed",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="user",
            object_id=user.id,
            detail={"from": before, "to": role},
            ip=ctx.ip,
        )

    if is_active is not None and is_active != user.is_active:
        if not is_active:
            elsewhere = db.scalar(
                select(Membership.id).where(
                    Membership.user_id == user.id, Membership.company_id != ctx.company_id
                )
            )
            if elsewhere is not None:
                raise ApiError(
                    409,
                    "shared_account",
                    "This account also belongs to another company, so it cannot be switched off from here.",
                )
            user.is_active = False
            revoke_user_sessions(db, user.id)
        else:
            user.is_active = True
        record_audit(
            db,
            action="member.reactivated" if is_active else "member.deactivated",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="user",
            object_id=user.id,
            ip=ctx.ip,
        )
    return user, membership


def end_member_sessions(db: Session, ctx: Ctx, user_id: uuid.UUID) -> None:
    """Sign someone out everywhere without switching them off (they can sign in again)."""
    membership = db.scalar(
        select(Membership).where(Membership.company_id == ctx.company_id, Membership.user_id == user_id)
    )
    if membership is None:
        raise ApiError(404, "not_found", "That person was not found.")
    if user_id == ctx.user.id:
        raise ApiError(409, "cannot_change_self", "Use Sign out to end your own sessions.")
    revoke_user_sessions(db, user_id)
    record_audit(
        db,
        action="member.sessions_ended",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="user",
        object_id=user_id,
        ip=ctx.ip,
    )


# --- invitations --------------------------------------------------------------------------------


def create_invitation(
    db: Session, settings: Settings, ctx: Ctx, email: str, role: str
) -> tuple[Invitation, str]:
    email = normalize_email(email)
    if role not in {"admin", "manager", "employee"}:
        raise ApiError(422, "validation_error", "Choose a valid role.", {"role": "Choose a valid role."})
    if ctx.role == "manager" and role != "employee":
        raise ApiError(403, "forbidden", "Managers can invite employees only.")

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ApiError(409, "account_exists", "That person already has an account.")

    now = timeutil.utcnow()
    # A newer invitation replaces any older one still pending for the same address.
    db.execute(
        update(Invitation)
        .where(
            Invitation.company_id == ctx.company_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    raw = new_token()
    invitation = Invitation(
        company_id=ctx.company_id,
        email=email,
        role=role,
        token_hash=hash_token(raw),
        invited_by=ctx.user.id,
        expires_at=now + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(invitation)
    db.flush()
    record_audit(
        db,
        action="invitation.created",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="invitation",
        object_id=invitation.id,
        detail={"role": role},
        ip=ctx.ip,
    )
    return invitation, raw


def find_open_invitation(db: Session, raw_token: str) -> Invitation:
    """Any reason an invitation cannot be used looks the same from outside."""
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(raw_token)))
    now = timeutil.utcnow()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise INVALID_INVITATION
    return invitation


def accept_invitation(
    db: Session,
    settings: Settings,
    raw_token: str,
    *,
    name: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, Membership, str]:
    invitation = find_open_invitation(db, raw_token)
    require_good_password(password, invitation.email)
    now = timeutil.utcnow()

    user = User(
        email=invitation.email,
        name=name.strip(),
        password_hash=hash_password(password),
        # The link was delivered to this address, so using it proves ownership.
        email_verified_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "account_exists", "An account with that email already exists.") from None

    membership = Membership(user_id=user.id, company_id=invitation.company_id, role=invitation.role)
    invitation.accepted_at = now
    db.add(membership)
    record_audit(
        db,
        action="invitation.accepted",
        company_id=invitation.company_id,
        actor_user_id=user.id,
        object_type="invitation",
        object_id=invitation.id,
        ip=ip,
    )
    raw_session, _ = start_session(db, settings, user, invitation.company_id, ip, user_agent)
    return user, membership, raw_session


# --- one-time email tokens ----------------------------------------------------------------------


def issue_email_token(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    now = timeutil.utcnow()
    # Only the newest link for a purpose works.
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = new_token()
    db.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=hash_token(raw), expires_at=now + ttl))
    db.flush()
    return raw


def consume_email_token(db: Session, raw_token: str, purpose: str) -> User:
    token = db.scalar(
        select(EmailToken).where(
            EmailToken.token_hash == hash_token(raw_token), EmailToken.purpose == purpose
        )
    )
    now = timeutil.utcnow()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise ApiError(400, "invalid_token", "This link is invalid or has expired.")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise ApiError(400, "invalid_token", "This link is invalid or has expired.")
    # Claim the link with one statement that only succeeds while it is still unused. Checking and then setting
    # would let two requests that arrive together both pass the check.
    claimed = rows_affected(
        db.execute(
            update(EmailToken)
            .where(EmailToken.id == token.id, EmailToken.used_at.is_(None))
            .values(used_at=now)
        )
    )
    if claimed != 1:
        raise ApiError(400, "invalid_token", "This link is invalid or has expired.")
    return user
