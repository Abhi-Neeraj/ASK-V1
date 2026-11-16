"""Server-sent events: the browser's live view of lockets and recordings.

Authenticated with the same session cookie as the rest of the web API (an EventSource cannot set
headers), tenant-scoped, and role-scoped. The stream never holds a database connection between polls.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.errors import ApiError
from app.security.deps import check_origin, get_ctx
from app.security.ratelimit import enforce
from app.services import events as feed

router = APIRouter(tags=["live"])
logger = logging.getLogger("ask.events")

# How often a long-lived stream confirms the person is still signed in (and still allowed in).
SESSION_CHECK_SECONDS = 5.0
RECONNECT_MS = 2000


class _Slot:
    """One place in the stream registry, released exactly once however the stream ends."""

    def __init__(self, registry: feed.StreamRegistry, user_id: uuid.UUID) -> None:
        self._registry = registry
        self._user_id = user_id
        self._lock = threading.Lock()
        self._held = True

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        self._registry.release(self._user_id)


@router.get("/api/events", response_class=StreamingResponse)
async def stream_events(
    request: Request,
    last_event_id: Annotated[str | None, Header()] = None,
    last_event_id_query: Annotated[str | None, Query(alias="lastEventId")] = None,
) -> StreamingResponse:
    """`text/event-stream`. Send `Last-Event-ID` (browsers do it on reconnect) to replay what was missed.

    Events: `device.online`, `device.offline`, `device.paired`, `device.assigned`, `device.revoked`,
    `device.connected`, `device.disconnected`, `recording.uploaded`, `recording.state`,
    `recording.deleted`. Control events: `stream.renew` (reconnect now, nothing is lost) and
    `session.ended` (sign in again).
    """
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    registry: feed.StreamRegistry = request.app.state.streams

    def open_stream() -> tuple[feed.Viewer, int]:
        # Browsers name the page's origin on cross-origin requests; a stream of company data is not
        # something another site gets to open, even though the browser would also refuse to show it.
        check_origin(request, settings)
        with factory() as db:
            ctx = get_ctx(request, db, settings)
            viewer = feed.Viewer(
                user_id=ctx.user.id,
                company_id=ctx.company_id,
                can_manage=ctx.can_manage,
                session_id=ctx.session.id,
            )
            latest = feed.latest_event_id(db, viewer.company_id)
        requested = feed.parse_event_id(last_event_id)
        if requested is None:
            requested = feed.parse_event_id(last_event_id_query)
        # A fresh connection starts "now". A returning one resumes, unless its ID is from the future
        # (a database that was reset), in which case replaying from there would silently miss everything.
        cursor = latest if requested is None or requested > latest else requested
        return viewer, cursor

    viewer, cursor = await run_in_threadpool(open_stream)
    enforce(request.app.state.limiter, f"events:{viewer.user_id}", 60, 60)
    if not registry.acquire(
        viewer.user_id, per_user=settings.max_event_streams_per_user, total=settings.max_event_streams
    ):
        raise ApiError(
            429,
            "too_many_streams",
            "Too many live connections are open. Close another tab and try again.",
            headers={"Retry-After": "5"},
        )
    slot = _Slot(registry, viewer.user_id)

    def poll(after_id: int) -> tuple[list[feed.FeedEvent], int]:
        with factory() as db:
            return feed.read_new_events(db, viewer, after_id)

    def still_signed_in() -> bool:
        with factory() as db:
            return feed.session_is_open(db, settings, viewer)

    async def generate() -> AsyncIterator[str]:
        position = cursor
        started = last_write = last_check = time.monotonic()
        try:
            yield f"retry: {RECONNECT_MS}\n\n: stream open\n\n"
            while True:
                if time.monotonic() - started >= settings.event_stream_max_seconds:
                    yield "event: stream.renew\ndata: {}\n\n"
                    return
                batch, position = await run_in_threadpool(poll, position)
                for event in batch:
                    yield feed.format_event(event)
                if batch:
                    last_write = time.monotonic()

                now = time.monotonic()
                if now - last_check >= SESSION_CHECK_SECONDS:
                    last_check = now
                    if not await run_in_threadpool(still_signed_in):
                        yield "event: session.ended\ndata: {}\n\n"
                        return
                if now - last_write >= settings.event_keepalive_seconds:
                    # A comment keeps proxies from closing a quiet connection.
                    yield ": keep-alive\n\n"
                    last_write = now
                await asyncio.sleep(settings.event_poll_interval_seconds)
        except Exception:
            logger.exception("live event stream failed")
        finally:
            slot.release()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # no-transform stops compressing proxies from buffering the stream.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
        # Runs when the response ends for any reason, including a client that left before the
        # first chunk, in which case the generator above never started.
        background=BackgroundTask(slot.release),
    )
