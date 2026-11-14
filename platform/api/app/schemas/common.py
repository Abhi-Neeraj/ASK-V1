from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """All JSON is camelCase on the wire and snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


T = TypeVar("T")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# A date or time sent by a client. A value with no timezone ("2026-10-01") is read as UTC; the database
# only accepts timezone-aware values, so without this such a filter would crash the request.
UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


# Text typed by a person. React already escapes it when it is shown, so this is not about script in a page: it
# is about what the text can do elsewhere. A line break in a name would break a mail header (the mail library
# refuses it, so the message would silently not be forwarded), and right-to-left override characters make one
# name look like another in a list or in the audit log. Both are removed here, before any length limit is
# applied.
_BIDI_CONTROLS = dict.fromkeys((*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
_LINE_BREAKS = chr(0x2028) + chr(0x2029)  # line and paragraph separators


def clean_line(value: object) -> object:
    """One line of text: control characters and line breaks become spaces, runs of space become one."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFC", value).translate(_BIDI_CONTROLS)
    text = "".join(" " if unicodedata.category(c) == "Cc" or c in _LINE_BREAKS else c for c in text)
    return " ".join(text.split())


def clean_text(value: object) -> object:
    """Several lines of text: line endings become a plain newline, other control characters are dropped."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFC", value).translate(_BIDI_CONTROLS)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for separator in _LINE_BREAKS:
        text = text.replace(separator, "\n")
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc").strip()


CleanLine = Annotated[str, BeforeValidator(clean_line)]
CleanText = Annotated[str, BeforeValidator(clean_text)]


class Page(CamelModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
