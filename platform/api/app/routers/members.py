from __future__ import annotations

import uuid

from fastapi import APIRouter, Response

from app.schemas.auth import MemberOut, MemberPatchIn
from app.security.deps import Admin, DbDep
from app.services import accounts

router = APIRouter(tags=["members"])


@router.patch("/api/members/{user_id}", response_model=MemberOut)
def change_member(user_id: uuid.UUID, body: MemberPatchIn, ctx: Admin, db: DbDep) -> MemberOut:
    """Change a person's role, or switch them off or back on. Administrators only, never for themselves.

    Switching someone off signs them out everywhere at once and stops them signing in. Their recordings,
    transcripts and the lockets assigned to them stay as they were; reassign the lockets from the Devices
    page."""
    user, membership = accounts.change_member(db, ctx, user_id, role=body.role, is_active=body.is_active)
    db.commit()
    return MemberOut(
        id=user.id, name=user.name, email=user.email, role=membership.role, is_active=user.is_active
    )


@router.delete("/api/members/{user_id}/sessions", status_code=204)
def end_member_sessions(user_id: uuid.UUID, ctx: Admin, db: DbDep) -> Response:
    """Sign someone out everywhere. They are not switched off and can sign in again."""
    accounts.end_member_sessions(db, ctx, user_id)
    db.commit()
    return Response(status_code=204)
