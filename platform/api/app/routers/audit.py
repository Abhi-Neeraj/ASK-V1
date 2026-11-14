from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.schemas.audit import AuditEventOut
from app.schemas.common import Page, UtcDatetime
from app.security.deps import Admin, DbDep
from app.services import audit_log as svc

router = APIRouter(tags=["audit"])

Category = Literal[
    "auth", "company", "device", "invitation", "member", "person", "recording", "settings", "transcript"
]


@router.get("/api/audit", response_model=Page[AuditEventOut])
def list_audit_events(
    ctx: Admin,
    db: DbDep,
    category: Category | None = None,
    action: Annotated[str | None, Query(max_length=60, pattern=r"^[a-z_]+\.[a-z_]+$")] = None,
    actor_id: Annotated[uuid.UUID | None, Query(alias="actorId")] = None,
    object_type: Annotated[str | None, Query(alias="objectType", max_length=40, pattern=r"^[a-z_]+$")] = None,
    date_from: Annotated[UtcDatetime | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[UtcDatetime | None, Query(alias="dateTo")] = None,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 25,
) -> Page[AuditEventOut]:
    """Who did what, newest first. Administrators only, and only for their own company."""
    filters = svc.AuditFilters(
        category=category,
        action=action,
        actor_user_id=actor_id,
        object_type=object_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    rows, total = svc.search(db, ctx, filters)
    return Page[AuditEventOut](
        items=svc.audit_outs(db, ctx, rows), total=total, page=page, page_size=page_size
    )
