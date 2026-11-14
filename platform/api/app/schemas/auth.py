from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field, model_validator

from app.models import Company, Membership, User
from app.schemas.common import CamelModel, CleanLine


class LoginIn(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class CompanyOut(CamelModel):
    id: uuid.UUID
    name: str


class UserOut(CamelModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    email_verified: bool
    company: CompanyOut


def user_out(user: User, membership: Membership, company: Company) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        email_verified=user.email_verified_at is not None,
        company=CompanyOut(id=company.id, name=company.name),
    )


class PasswordResetRequestIn(CamelModel):
    email: EmailStr


class PasswordResetConfirmIn(CamelModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(CamelModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class EmailTokenIn(CamelModel):
    token: str = Field(min_length=10, max_length=200)


class SessionOut(CamelModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    current: bool
    user_agent: str | None


class InvitationInfo(CamelModel):
    email: str
    company_name: str
    role: str
    expires_at: datetime


class AcceptInvitationIn(CamelModel):
    name: CleanLine = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class InvitationCreateIn(CamelModel):
    email: EmailStr
    role: str = Field(pattern="^(admin|manager|employee)$")


class InvitationOut(CamelModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime
    accepted: bool
    # Returned once, only when the invitation is created, so an administrator can share it
    # when outgoing email is not configured.
    accept_url: str | None = None


class MemberOut(CamelModel):
    id: uuid.UUID
    name: str
    # Only managers and administrators are shown addresses.
    email: str | None
    role: str
    # False for someone who has been switched off. Managers and administrators see them; nobody else does.
    is_active: bool = True


class MemberPatchIn(CamelModel):
    """What an administrator can change about a person. At least one field."""

    role: Literal["admin", "manager", "employee"] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> MemberPatchIn:
        if self.role is None and self.is_active is None:
            raise ValueError("Say what to change: role, isActive, or both.")
        return self
