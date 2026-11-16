"""Workspace-level endpoints: dashboard summary, retention settings and the public contact form."""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.models import Company, ContactMessage, Device
from app.schemas.people import ContactIn, SettingsOut, SettingsPatchIn, SummaryOut
from app.security.deps import Admin, Authed, Ctx, DbDep, LimiterDep, SettingsDep, check_origin, client_ip
from app.security.ratelimit import enforce
from app.services import devices as device_service
from app.services import recordings as recording_service
from app.services.audit import record_audit
from app.services.email import OutgoingEmail

router = APIRouter(tags=["workspace"])
logger = logging.getLogger("ask.contact")


@router.get("/api/summary", response_model=SummaryOut)
def summary(ctx: Authed, db: DbDep, settings: SettingsDep) -> SummaryOut:
    now = timeutil.utcnow()
    devices = db.scalars(device_service.visible_devices(ctx).where(Device.status != "revoked")).all()
    online = sum(1 for d in devices if device_service.presence(d, settings, now) == "online")

    visible = recording_service.visible_recordings(ctx).subquery()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def count(*conditions: ColumnElement[bool]) -> int:
        return db.scalar(select(func.count()).select_from(visible).where(*conditions)) or 0

    return SummaryOut(
        devices_online=online,
        devices_total=len(devices),
        recordings_today=count(visible.c.uploaded_at >= today),
        in_progress=count(visible.c.state.in_(recording_service.ACTIVE_STATES)),
        needs_review=count(visible.c.state == "needs_review"),
        failed=count(visible.c.state == "failed"),
    )


def _company(db: Session, ctx: Ctx) -> Company:
    company = db.get(Company, ctx.company_id)
    assert company is not None  # noqa: S101
    return company


@router.get("/api/settings", response_model=SettingsOut)
def get_workspace_settings(ctx: Authed, db: DbDep) -> SettingsOut:
    company = _company(db, ctx)
    return SettingsOut(retention_days=company.retention_days, purge_grace_days=company.purge_grace_days)


@router.patch("/api/settings", response_model=SettingsOut)
def update_workspace_settings(body: SettingsPatchIn, ctx: Admin, db: DbDep) -> SettingsOut:
    company = _company(db, ctx)
    fields = body.model_fields_set
    changes: dict[str, int | None] = {}
    if "retention_days" in fields:
        company.retention_days = body.retention_days
        changes["retention_days"] = body.retention_days
    if "purge_grace_days" in fields and body.purge_grace_days is not None:
        company.purge_grace_days = body.purge_grace_days
        changes["purge_grace_days"] = body.purge_grace_days
    record_audit(
        db,
        action="settings.updated",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="company",
        object_id=ctx.company_id,
        detail=changes,
        ip=ctx.ip,
    )
    db.commit()
    return SettingsOut(retention_days=company.retention_days, purge_grace_days=company.purge_grace_days)


@router.post("/api/contact", status_code=204)
def contact(
    body: ContactIn, request: Request, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> Response:
    """Public contact form. Validated here regardless of what the browser checked."""
    check_origin(request, settings)
    ip = client_ip(request, settings)
    enforce(limiter, f"contact:{ip}", settings.contact_per_hour, 3600)

    # Bots fill the hidden field and submit instantly. Answer exactly as for a real message,
    # so they learn nothing, but keep nothing.
    if body.website or (body.elapsed_ms is not None and body.elapsed_ms < 1500):
        return Response(status_code=204)

    ip_hash = (
        hashlib.sha256(f"{settings.secret_key.get_secret_value()}|{ip}".encode()).hexdigest() if ip else None
    )
    db.add(
        ContactMessage(
            name=body.name.strip(),
            email=body.email.strip().lower(),
            company=(body.company or "").strip() or None,
            message=body.message.strip(),
            ip_hash=ip_hash,
        )
    )
    db.commit()

    if settings.contact_recipient:
        try:
            request.app.state.email.send(
                OutgoingEmail(
                    to=settings.contact_recipient,
                    subject=f"ASK contact form: {body.name.strip()}",
                    body=(
                        f"From: {body.name} <{body.email}>\nCompany: {body.company or '-'}\n\n{body.message}"
                    ),
                )
            )
        except Exception:  # the message is already saved; a mail outage must not lose it
            logger.exception("could not forward contact message")
    return Response(status_code=204)
