"""A small in-process sliding-window rate limiter.

It protects sign-in, password reset, pairing and the contact form from guessing and spam. State
lives in memory, so limits are per API process: behind several workers or instances, enforce
the same limits at the gateway as well (documented in the deployment notes).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.errors import ApiError

# Look for forgotten keys once this many have piled up (and then not again until the count has doubled), and
# in any case at most once a minute, so a burst of one-off names is cleared away once traffic goes quiet.
_SWEEP_FROM = 1024
_SWEEP_EVERY_SECONDS = 60.0


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._windows: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sweep_at = _SWEEP_FROM
        self._last_sweep: float | None = None

    def check(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> int | None:
        """Record an attempt. Returns None if allowed, or the seconds until another is allowed."""
        current = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            self._windows[key] = window_seconds
            while hits and current - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(int(window_seconds - (current - hits[0])) + 1, 1)
            hits.append(current)
            if self._last_sweep is None:
                self._last_sweep = current
            if len(self._hits) >= self._sweep_at or current - self._last_sweep >= _SWEEP_EVERY_SECONDS:
                self._forget_expired(current)
            return None

    def _forget_expired(self, current: float) -> None:
        """Drop every key whose newest attempt is older than its own window. Names such as a login email are
        chosen by whoever is guessing, so without this each new one would stay in memory for ever."""
        for key in [
            k for k, hits in self._hits.items() if not hits or current - hits[-1] >= self._windows[k]
        ]:
            del self._hits[key]
            self._windows.pop(key, None)
        self._sweep_at = max(_SWEEP_FROM, len(self._hits) * 2)
        self._last_sweep = current

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._windows.clear()
            self._sweep_at = _SWEEP_FROM
            self._last_sweep = None


def enforce(limiter: RateLimiter, key: str, limit: int, window_seconds: float) -> None:
    retry_after = limiter.check(key, limit, window_seconds)
    if retry_after is not None:
        raise ApiError(
            429,
            "rate_limited",
            "Too many attempts. Wait a little, then try again.",
            headers={"Retry-After": str(retry_after)},
        )
