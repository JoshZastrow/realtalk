"""
realtalk.storage — Layer 1: physical session persistence.

Owns disk I/O, file path management, and log rotation.
session.py owns the in-memory data model; storage.py owns how it hits disk.

Key design decisions:
- Workspace fingerprint: sha256(realpath(cwd))[:16] → session subdirectory.
  Two games in different directories never share a session file.
- Append-only writes: each append opens in 'a' mode, writes one JSON line + newline,
  and flushes. A crash mid-write corrupts at most the final incomplete line,
  which load() skips silently.
- Log rotation: 256 KB active file, max 3 rotated files.
  session.jsonl → session.jsonl.1 → session.jsonl.2 → session.jsonl.3
- load() reads rotated files in order (oldest first), making rotation transparent.

Dependencies: session.py (Layer 0) only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from realtalk.session import Session, SessionEvent, event_from_dict, event_to_dict, session_from_jsonl


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
    """Return a 16-char hex fingerprint for *cwd*.

    Uses sha256 of the resolved POSIX path so two distinct directories always
    produce distinct fingerprints.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     fp = workspace_fingerprint(pathlib.Path(d))
    ...     len(fp) == 16 and fp.isalnum()
    True
    """
    resolved = cwd.resolve().as_posix().encode()
    return hashlib.sha256(resolved).hexdigest()[:16]


# ---------------------------------------------------------------------------
# SessionStore — directory manager
# ---------------------------------------------------------------------------


class SessionStore:
    """Manage session file paths under a root directory, keyed by workspace fingerprint.

    Args:
        root: Base directory for all session subdirectories.
              Defaults to ~/.realtalk/sessions/.
    """

    def __init__(self, root: Path = DEFAULT_SESSION_ROOT) -> None:
        self.root = root

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


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def rotate_if_needed(
    path: Path,
    max_bytes: int = ROTATION_THRESHOLD_BYTES,
    keep: int = MAX_ROTATED_FILES,
) -> None:
    """Rotate *path* → *path*.1 → … → *path*.*keep* if *path* exceeds *max_bytes*.

    Rotation policy:
      1. Delete path.{keep} if it exists.
      2. Rename path.{keep-1} → path.{keep}, …, path.1 → path.2, path → path.1.
      3. path is left absent; the next append will create it fresh.

    Does nothing if path does not exist or is smaller than max_bytes.
    """
    if not path.exists() or path.stat().st_size < max_bytes:
        return

    # Delete the oldest rotated file to make room
    oldest = path.with_suffix(f"{path.suffix}.{keep}")
    if oldest.exists():
        oldest.unlink()

    # Shift existing rotated files: N → N+1
    for n in range(keep - 1, 0, -1):
        src = path.with_suffix(f"{path.suffix}.{n}")
        dst = path.with_suffix(f"{path.suffix}.{n + 1}")
        if src.exists():
            src.rename(dst)

    # Rotate the active file to .1
    path.rename(path.with_suffix(f"{path.suffix}.1"))


# ---------------------------------------------------------------------------
# StoredSession — live handle to a session file on disk
# ---------------------------------------------------------------------------


class StoredSession:
    """Live handle to a session file on disk.

    Owns append and load operations. Does NOT hold an in-memory Session;
    the caller maintains that separately.

    Args:
        path: Path to the active session.jsonl file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(
        self,
        event: SessionEvent,
        _max_bytes: int = ROTATION_THRESHOLD_BYTES,
    ) -> None:
        """Append one event as a JSONL line. Rotates the file first if needed.

        The *_max_bytes* parameter is a test escape hatch — callers should not
        set it in production.
        """
        rotate_if_needed(self.path, max_bytes=_max_bytes)
        line = event_to_dict(event)
        with self.path.open("a", encoding="utf-8") as fh:
            import json as _json
            fh.write(_json.dumps(line) + "\n")
            fh.flush()

    def load(self) -> Session:
        """Replay all JSONL lines (across rotated files, oldest first) into a Session.

        Incomplete (non-JSON) lines are silently skipped — crash-safe.
        """
        import json as _json

        good_lines: list[str] = []

        # Collect rotated files oldest-first: .3, .2, .1
        for n in range(MAX_ROTATED_FILES, 0, -1):
            rotated = self.path.with_suffix(f"{self.path.suffix}.{n}")
            if rotated.exists():
                for raw in rotated.read_text(encoding="utf-8").splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        _json.loads(raw)  # validate only
                        good_lines.append(raw)
                    except _json.JSONDecodeError:
                        pass

        # Active file
        if self.path.exists():
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    _json.loads(raw)  # validate only
                    good_lines.append(raw)
                except _json.JSONDecodeError:
                    pass

        return session_from_jsonl(good_lines)

    def exists(self) -> bool:
        """Return True if the active session file exists on disk."""
        return self.path.exists()
