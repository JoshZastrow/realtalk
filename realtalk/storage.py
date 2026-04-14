"""
realtalk.storage -- Layer 1: physical session persistence.

Owns disk I/O, file path management, log rotation, and archival.
session.py owns the in-memory data model; storage.py owns how it hits disk.

Key design decisions:
- Workspace fingerprint: {dirname}-{hash[:8]} for human-readable directories.
  Two games in different directories never share a session file.
- Append-only writes: each append opens in 'a' mode, writes one JSON line + newline,
  and flushes. A crash mid-write corrupts at most the final incomplete line,
  which load() skips via session_from_jsonl(skip_errors=True).
- Log rotation: 256 KB active file, max 3 rotated files.
  session.jsonl -> session.jsonl.1 -> session.jsonl.2 -> session.jsonl.3
- SessionStarted header written to every new file after rotation so each file
  is self-describing and usable for training export.
- Archive-before-rotate: before deleting .{keep}, copy it to the archive
  directory so training data is never lost.

Dependencies: session.py (Layer 0) only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from realtalk.session import (
    SessionEvent,
    event_to_dict,
    session_from_jsonl,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SESSION_ROOT = Path.home() / ".realtalk" / "sessions"
ROTATION_THRESHOLD_BYTES = 256 * 1024  # 256 KB
MAX_ROTATED_FILES = 3


# ---------------------------------------------------------------------------
# Workspace fingerprinting
# ---------------------------------------------------------------------------


def workspace_fingerprint(cwd: Path) -> str:
    """Return a '{dirname}-{hash[:8]}' fingerprint for *cwd*.

    Human-readable directory name with an 8-char hash suffix for uniqueness.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     fp = workspace_fingerprint(pathlib.Path(d))
    ...     "-" in fp and len(fp.rsplit("-", 1)[1]) == 8
    True
    """
    resolved = cwd.resolve()
    dirname = resolved.name or "root"
    # Sanitize: lowercase, non-alnum chars become hyphens, strip leading/trailing hyphens
    safe = "".join(c if c.isalnum() else "-" for c in dirname.lower()).strip("-")
    safe = safe[:32]  # cap length
    hash_prefix = hashlib.sha256(resolved.as_posix().encode()).hexdigest()[:8]
    return f"{safe}-{hash_prefix}"


# ---------------------------------------------------------------------------
# SessionStore -- directory manager
# ---------------------------------------------------------------------------


class SessionStore:
    """Manage session file paths under a root directory, keyed by workspace fingerprint.

    Args:
        root: Base directory for all session subdirectories.
              Defaults to ~/.realtalk/sessions/.
    """

    def __init__(self, root: Path = DEFAULT_SESSION_ROOT) -> None:
        self.root = root

    @property
    def archive_root(self) -> Path:
        """Return the archive directory (sibling of sessions root).

        If root is ~/.realtalk/sessions/, archive_root is ~/.realtalk/archive/.
        """
        return self.root.parent / "archive"

    def session_dir(self, cwd: Path) -> Path:
        """Return (and create if needed) the session directory for *cwd*.

        >>> import tempfile, pathlib
        >>> with tempfile.TemporaryDirectory() as d:
        ...     root = pathlib.Path(d) / "sessions"
        ...     store = SessionStore(root=root)
        ...     sd = store.session_dir(pathlib.Path(d) / "proj")
        ...     sd.exists()
        True
        """
        directory = self.root / workspace_fingerprint(cwd)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def session_path(self, cwd: Path) -> Path:
        """Return the path to the active session.jsonl for *cwd*."""
        return self.session_dir(cwd) / "session.jsonl"

    def list_sessions(self) -> list[Path]:
        """Return all active session.jsonl paths under this store root."""
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*/session.jsonl"))

    def list_archived_sessions(self) -> list[Path]:
        """Return all archived JSONL files under the archive root."""
        if not self.archive_root.exists():
            return []
        return sorted(self.archive_root.glob("*/*.jsonl"))


# ---------------------------------------------------------------------------
# Rotation + archival
# ---------------------------------------------------------------------------


def _read_session_started_line(path: Path) -> str | None:
    """Read the first line from *path* if it is a SessionStarted event."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
            if first_line:
                data = json.loads(first_line)
                if data.get("event_type") == "session_started":
                    return first_line
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _find_session_started_header(path: Path, keep: int) -> str | None:
    """Find the SessionStarted header from the oldest available file."""
    # Check rotated files from oldest to newest
    for n in range(keep, 0, -1):
        rotated = path.with_suffix(f"{path.suffix}.{n}")
        header = _read_session_started_line(rotated)
        if header is not None:
            return header
    # Check the active file
    return _read_session_started_line(path)


def rotate_if_needed(
    path: Path,
    max_bytes: int = ROTATION_THRESHOLD_BYTES,
    keep: int = MAX_ROTATED_FILES,
    archive_root: Path | None = None,
) -> None:
    """Rotate *path* -> *path*.1 -> ... -> *path*.*keep* if *path* exceeds *max_bytes*.

    Before deleting the oldest rotated file, archives it if *archive_root* is set.
    After rotation, writes the SessionStarted header to the new active file so
    every file on disk is self-describing.

    Does nothing if path does not exist or is smaller than max_bytes.
    """
    if not path.exists() or path.stat().st_size < max_bytes:
        return

    # Read the SessionStarted header before we start moving files
    header_line = _find_session_started_header(path, keep)

    # Archive the oldest rotated file before deleting
    oldest = path.with_suffix(f"{path.suffix}.{keep}")
    if oldest.exists():
        if archive_root is not None:
            # Derive fingerprint directory from the session path's parent name
            fingerprint = path.parent.name
            archive_dir = archive_root / fingerprint
            archive_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            archive_path = archive_dir / f"{timestamp}.jsonl"
            # Avoid collisions
            counter = 0
            while archive_path.exists():
                counter += 1
                archive_path = archive_dir / f"{timestamp}-{counter}.jsonl"
            shutil.copy2(oldest, archive_path)
        oldest.unlink()

    # Shift existing rotated files: N -> N+1
    for n in range(keep - 1, 0, -1):
        src = path.with_suffix(f"{path.suffix}.{n}")
        dst = path.with_suffix(f"{path.suffix}.{n + 1}")
        if src.exists():
            src.rename(dst)

    # Rotate the active file to .1
    path.rename(path.with_suffix(f"{path.suffix}.1"))

    # Write SessionStarted header to a temp file, then atomic rename to path
    if header_line is not None:
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=".session_", suffix=".tmp"
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(header_line + "\n")
                fh.flush()
            Path(tmp_name).rename(path)
        except BaseException:
            # Clean up on failure
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# StoredSession -- live handle to a session file on disk
# ---------------------------------------------------------------------------


class StoredSession:
    """Live handle to a session file on disk.

    Owns append and load operations. Does NOT hold an in-memory Session;
    the caller maintains that separately.

    Args:
        path: Path to the active session.jsonl file.
        max_bytes: Rotation threshold in bytes. Default 256 KB.
        archive_root: Where to copy files before rotation deletes them.
                      Set by SessionStore integration. None disables archival.
    """

    def __init__(
        self,
        path: Path,
        max_bytes: int = ROTATION_THRESHOLD_BYTES,
        archive_root: Path | None = None,
    ) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.archive_root = archive_root

    def append(self, event: SessionEvent) -> None:
        """Append one event as a JSONL line. Rotates the file first if needed.

        Creates parent directories if they don't exist.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(
            self.path,
            max_bytes=self.max_bytes,
            archive_root=self.archive_root,
        )
        line = json.dumps(event_to_dict(event))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def append_events(self, events: list[SessionEvent] | tuple[SessionEvent, ...]) -> None:
        for event in events:
            self.append(event)

    def load(self):
        """Replay all JSONL lines (across rotated files, oldest first) into a Session.

        Corrupt lines are silently skipped (crash-safe).
        """
        all_lines: list[str] = []

        # Collect rotated files oldest-first: .3, .2, .1
        for n in range(MAX_ROTATED_FILES, 0, -1):
            rotated = self.path.with_suffix(f"{self.path.suffix}.{n}")
            if rotated.exists():
                all_lines.extend(rotated.read_text(encoding="utf-8").splitlines())

        # Active file
        if self.path.exists():
            all_lines.extend(self.path.read_text(encoding="utf-8").splitlines())

        return session_from_jsonl(all_lines, skip_errors=True)

    def exists(self) -> bool:
        """Return True if the active session file exists on disk."""
        return self.path.exists()
