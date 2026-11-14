"""The API reference says its route list matches the running application. This keeps it honest."""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
DEVICE_PREFIX = "/api/device/v1"
# Served by the framework itself, and switched off in production.
FRAMEWORK = {"/api/docs", "/api/openapi.json"}


TAILED_ROW = re.compile(
    r"`((?:GET|POST|PUT|PATCH|DELETE) /api/[^`\s]*/)\w[\w-]*`"  # the first path
    r", `\.\.\./([\w-]+)`"  # and the second, given as ".../tail"
)


def shape(path: str) -> str:
    """`/api/people/{person_id}/consent` and `/api/people/{id}/consent` are the same route."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def documented(text: str) -> set[str]:
    """Every `METHOD /path` in backticks, with the locket API's paths expanded from their short form."""
    found: set[str] = set()
    for method, path in re.findall(r"`(GET|POST|PUT|PATCH|DELETE) (/[^`\s]*)`", text):
        # The locket API's rows give paths relative to /api/device/v1.
        full = path if path.startswith("/api/") else DEVICE_PREFIX + path
        found.add(f"{method} {shape(full)}")
    return found


def real_routes(app) -> set[str]:  # type: ignore[no-untyped-def]
    # The generated OpenAPI schema lists every route the application serves, nested routers included.
    routes: set[str] = set()
    for path, operations in app.openapi()["paths"].items():
        if path in FRAMEWORK or not path.startswith("/api/"):
            continue
        for method in operations:
            if method.upper() not in {"HEAD", "OPTIONS"}:
                routes.add(f"{method.upper()} {shape(path)}")
    return routes


def test_every_route_the_application_serves_is_in_the_api_reference(app) -> None:  # type: ignore[no-untyped-def]
    text = (DOCS / "API.md").read_text(encoding="utf-8")
    # A row like "`POST /api/auth/verify-email/request`, `.../confirm`" documents a second path by its tail.
    expanded = text
    for head, tail in TAILED_ROW.findall(text):
        expanded += f" `{head}{tail}`"
    missing = sorted(real_routes(app) - documented(expanded))
    assert missing == [], "routes missing from platform/docs/API.md:\n  " + "\n  ".join(missing)


def test_the_reference_does_not_describe_routes_that_are_gone(app) -> None:  # type: ignore[no-untyped-def]
    text = (DOCS / "API.md").read_text(encoding="utf-8")
    extra = sorted(documented(text) - real_routes(app))
    # `/api/events` and friends are real routes; anything left is a leftover from an earlier design.
    assert extra == [], "documented but not served:\n  " + "\n  ".join(extra)
