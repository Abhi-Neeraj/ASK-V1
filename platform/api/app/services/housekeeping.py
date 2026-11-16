"""Periodic upkeep. Runs inside the API process, or from `python -m app.cli housekeeping`."""

from __future__ import annotations

import logging

from app.config import Settings
from app.db import SessionFactory
from app.services import devices as device_service
from app.services import events as event_service
from app.services import recordings as recording_service
from app.services import workers as worker_service
from app.services.storage import Storage

logger = logging.getLogger("ask.housekeeping")


def run_housekeeping(
    factory: SessionFactory, settings: Settings, storage: Storage | None = None
) -> dict[str, int]:
    """Each step commits on its own and one failing step never blocks the others."""
    results: dict[str, int] = {}
    with factory() as db:
        try:
            results["devices_marked_offline"] = device_service.expire_stale_presence(db, settings)
        except Exception:
            logger.exception("presence sweep failed")
            db.rollback()
        try:
            results["recordings_expired_by_retention"] = recording_service.enforce_retention(db)
        except Exception:
            logger.exception("retention sweep failed")
            db.rollback()
        try:
            results["live_events_pruned"] = event_service.prune_events(db, settings)
        except Exception:
            logger.exception("live event pruning failed")
            db.rollback()
        try:
            results["workers_forgotten"] = worker_service.prune(db)
        except Exception:
            logger.exception("worker pruning failed")
            db.rollback()
        if storage is not None:
            try:
                results["recordings_purged"] = recording_service.purge_deleted(db, storage)
            except Exception:
                logger.exception("purge failed")
                db.rollback()
            try:
                # A cancelled request can leave half an upload behind; one in progress is minutes old at most.
                results["partial_uploads_removed"] = storage.remove_stale_temp_files(max_age_seconds=3600)
            except Exception:
                logger.exception("partial upload cleanup failed")
    return results
