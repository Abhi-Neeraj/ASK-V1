"""Password hashing (Argon2id via argon2-cffi) and the password policy."""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 128

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def use_cheap_parameters_for_tests() -> None:
    """Make hashing fast so the test suite runs quickly. Never called by the application itself."""
    global _hasher
    _hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    _dummy_hash.cache_clear()


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password-used-only-for-timing")


def burn_password_time(password: str) -> None:
    """Spend the same time as a real check, so an unknown email takes as long as a wrong password."""
    verify_password(_dummy_hash(), password)


def password_problem(password: str, *, email: str | None = None) -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use {MAX_LENGTH} characters or fewer."
    if len(set(password)) < 5:
        return "Use a more varied password."
    if email and password.lower() == email.lower():
        return "The password cannot be your email address."
    return None
