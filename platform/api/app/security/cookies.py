from __future__ import annotations

from fastapi import Response

from app.config import Settings
from app.security.deps import CSRF_COOKIE, SESSION_COOKIE
from app.security.tokens import csrf_token


def set_session_cookies(response: Response, settings: Settings, session_token: str) -> None:
    """The session cookie is HttpOnly (script cannot read it). The CSRF cookie is readable on purpose:
    the web app copies it into a header, and the server checks it against the session."""
    max_age = settings.session_ttl_hours * 3600
    common = {"max_age": max_age, "secure": settings.secure_cookies, "samesite": "lax", "path": "/"}
    response.set_cookie(SESSION_COOKIE, session_token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token(session_token, settings.secret_key.get_secret_value()),
        httponly=False,
        **common,  # type: ignore[arg-type]
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="lax")
