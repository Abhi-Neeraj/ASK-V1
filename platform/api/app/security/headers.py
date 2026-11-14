"""Security headers on every API response.

In production the reverse proxy sends `/api/*` straight to this service, so the headers the web app adds
(next.config.ts) never reach an API response. These are added here so they hold however the API is reached.

Headers a route sets itself are never replaced (audio sets its own `Cache-Control`, the live stream its own
`no-cache, no-transform`).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Applied to every response.
_ALWAYS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "same-origin",
    # Everything under /api/ is about one signed-in person or one locket: no shared cache may keep it, and
    # nothing is stored for a later visitor of the same browser. Audio and the live stream set their own.
    "cache-control": "no-store",
}

# Only for data responses. A page of API documentation (development only) needs scripts, and a browser
# that opens an audio file on its own needs to play it, so those are left alone.
_DATA_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_DATA_TYPES = ("application/json", "text/event-stream")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _ALWAYS.items():
                    if name not in headers:
                        headers[name] = value
                content_type = headers.get("content-type", "").lower()
                if content_type.startswith(_DATA_TYPES) and "content-security-policy" not in headers:
                    headers["content-security-policy"] = _DATA_POLICY
            await send(message)

        await self.app(scope, receive, with_headers)
