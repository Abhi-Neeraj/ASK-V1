"""A size limit for ordinary request bodies.

JSON endpoints read their whole body into memory before they check any field, so without a limit one anonymous
request with a very large body can use as much memory as the sender cares to send. Uploads of audio are
streamed
to disk under their own, larger, limits and are not covered here.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOO_LARGE = "That request is larger than this server accepts."
_REFUSAL = json.dumps({"error": {"code": "body_too_large", "message": _TOO_LARGE}}).encode()

Exempt = Callable[[str, str], bool]


def is_streamed_upload(method: str, path: str) -> bool:
    """Routes that stream their body to disk and enforce their own limit."""
    if method == "PUT" and path.startswith("/api/device/v1/recordings/"):
        return True
    # The phone uploads a locket's audio here, about a minute (1.9 MB) at a time. The route spools the body to
    # disk and enforces `max_upload_bytes` itself (after checking who is asking); under the 1 MB limit for
    # ordinary bodies, anything over about 30 seconds of audio was refused as "body_too_large".
    if method == "PUT" and path.startswith("/api/lockets/") and "/recordings/" in path:
        return True
    return method == "POST" and path.startswith("/api/people/") and path.endswith("/enroll")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limit: int, exempt: Exempt = is_streamed_upload) -> None:
        self.app = app
        self.limit = limit
        self.exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in _SAFE_METHODS
            or self.exempt(scope["method"], scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _refuse(send)
            return

        # A body with no declared length (sent in pieces) is counted as it arrives. Past the limit the route
        # is
        # told the client went away, so it never acts on half a request, and whatever it answers is replaced
        # by
        # the refusal below.
        seen = 0
        overflowed = False
        answered = False

        async def counted() -> Message:
            nonlocal seen, overflowed
            if overflowed:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    overflowed = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded(message: Message) -> None:
            nonlocal answered
            if not overflowed:
                if message["type"] == "http.response.start":
                    answered = True
                await send(message)
                return
            if message["type"] == "http.response.start" and not answered:
                answered = True
                await _refuse(send)
            # Anything else the route wanted to say about a request that was too big is dropped.

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not overflowed or answered:
                raise
            answered = True
            await _refuse(send)
            return
        if overflowed and not answered:
            await _refuse(send)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


async def _refuse(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_REFUSAL)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _REFUSAL})
