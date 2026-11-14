"""Single source of "now", so tests can control time by patching `app.timeutil.utcnow`."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware current time in UTC. Everything stored or compared in the API is UTC."""
    return datetime.now(UTC)
