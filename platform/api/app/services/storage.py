"""Durable audio storage on the local filesystem.

Files are written to a temporary name, flushed to disk, then renamed into place, so a reader (or a
crash) never sees a half-written recording. Keys are relative paths; callers never build paths from
user input, and every resolved path is checked to stay inside the storage root.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from datetime import datetime
from pathlib import Path


class StorageError(Exception):
    pass


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.tmp = self.root / ".tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)

    # --- keys and paths ----------------------------------------------------------------------

    @staticmethod
    def key_for(company_id: uuid.UUID, recording_id: uuid.UUID, when: datetime) -> str:
        return f"{company_id}/{when.year:04d}/{when.month:02d}/{recording_id}.wav"

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("storage key escapes the storage root")
        return path

    def new_temp_path(self) -> Path:
        return self.tmp / f"{uuid.uuid4().hex}.part"

    # --- writes ------------------------------------------------------------------------------

    def commit(self, temp_path: Path, key: str) -> Path:
        """Move a finished, already-fsynced temp file into its final place."""
        final = self.path_for(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final)
        _fsync_directory(final.parent)
        return final

    def delete(self, key: str) -> bool:
        path = self.path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    # --- reads -------------------------------------------------------------------------------

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    # --- housekeeping ------------------------------------------------------------------------

    def remove_stale_temp_files(self, max_age_seconds: float = 3600.0) -> int:
        """Clear partial uploads left by crashes or dropped connections."""
        removed = 0
        cutoff = time.time() - max_age_seconds
        for path in self.tmp.glob("*.part"):
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
        return removed

    def is_writable(self) -> bool:
        probe = self.tmp / f".probe-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError:
            return False
        return True


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable. Not supported on Windows, where it is skipped."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
