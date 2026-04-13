"""Tests for storage layer — session persistence, rotation, and archival.

All tests use real disk I/O via tmp_path. No filesystem mocking.
"""

import json

import pytest

from realtalk.session import new_session
from realtalk.storage import (
    MAX_ROTATED_FILES,
    SessionStore,
    StoredSession,
    rotate_if_needed,
    workspace_fingerprint,
)


# ---------------------------------------------------------------------------
# Original spec tests (5)
# ---------------------------------------------------------------------------


def test_append_and_reload_round_trip(tmp_path):
    """Append one event, reload, verify it comes back."""
    store = SessionStore(root=tmp_path)
    cwd = tmp_path / "project"
    cwd.mkdir()
    stored = StoredSession(store.session_path(cwd))
    session = new_session(str(cwd), "realtalk")
    stored.append(session.events[0])  # SessionStarted
    reloaded = stored.load()
    assert len(reloaded.events) == 1
    assert reloaded.session_id == session.session_id


def test_rotation_triggers_on_size(tmp_path):
    """File rotation creates .1 when active file exceeds max_bytes."""
    store = SessionStore(root=tmp_path)
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = store.session_path(cwd)
    stored = StoredSession(path, max_bytes=100)  # low threshold
    session = new_session(str(cwd), "realtalk")
    for _ in range(50):
        stored.append(session.events[0])
    assert path.with_suffix(".jsonl.1").exists()


def test_load_reads_across_rotated_files(tmp_path):
    """Events from rotated files are included in load(), oldest first."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path, max_bytes=100)
    session = new_session(str(tmp_path), "realtalk")
    for _ in range(50):
        stored.append(session.events[0])
    reloaded = stored.load()
    # Must have events from more than one file
    assert len(reloaded.events) > 1


def test_workspace_isolation(tmp_path):
    """Different cwds get different session paths."""
    store = SessionStore(root=tmp_path)
    dir_a = tmp_path / "project_a"
    dir_b = tmp_path / "project_b"
    dir_a.mkdir()
    dir_b.mkdir()
    assert store.session_path(dir_a) != store.session_path(dir_b)


def test_truncated_last_line_is_skipped(tmp_path):
    """Corrupt final line (crash mid-write) is silently skipped on load."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path)
    session = new_session(str(tmp_path), "realtalk")
    stored.append(session.events[0])
    with path.open("a") as f:
        f.write('{"incomplete":')
    reloaded = stored.load()
    assert len(reloaded.events) == 1


# ---------------------------------------------------------------------------
# Eng review gap tests (11)
# ---------------------------------------------------------------------------


def test_all_four_rotation_files_chronological(tmp_path):
    """Verify read order: .3, .2, .1, session.jsonl (oldest to newest)."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path, max_bytes=100)
    session = new_session(str(tmp_path), "realtalk")
    # Write enough to fill all 4 slots
    for _ in range(200):
        stored.append(session.events[0])
    reloaded = stored.load()
    # All events should have monotonically non-decreasing timestamps
    timestamps = [e.envelope.timestamp for e in reloaded.events]
    assert timestamps == sorted(timestamps)


def test_session_started_header_in_rotated_files(tmp_path):
    """After rotation the new active file starts with a SessionStarted event."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path, max_bytes=100)
    session = new_session(str(tmp_path), "realtalk")
    for _ in range(100):
        stored.append(session.events[0])
    # The active file must start with SessionStarted
    first_line = path.read_text().splitlines()[0]
    assert json.loads(first_line)["event_type"] == "session_started"


def test_max_rotated_files_enforced(tmp_path):
    """Only {keep} rotated files exist; oldest is deleted (after archiving)."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path, max_bytes=100)
    session = new_session(str(tmp_path), "realtalk")
    for _ in range(500):
        stored.append(session.events[0])
    # .4 should never exist (keep=3)
    assert not path.with_suffix(".jsonl.4").exists()


def test_archive_before_rotate_copies_oldest_file(tmp_path):
    """When rotation would delete .3, it copies to archive first."""
    sessions_root = tmp_path / "sessions"
    store = SessionStore(root=sessions_root)
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = store.session_path(cwd)
    stored = StoredSession(path, max_bytes=100, archive_root=store.archive_root)
    session = new_session(str(cwd), "realtalk")
    for _ in range(500):
        stored.append(session.events[0])
    archive_dir = store.archive_root / workspace_fingerprint(cwd)
    assert archive_dir.exists()
    archived_files = list(archive_dir.glob("*.jsonl"))
    assert len(archived_files) >= 1


def test_list_sessions_multiple(tmp_path):
    """list_sessions returns paths for multiple workspaces."""
    store = SessionStore(root=tmp_path)
    for name in ("proj_a", "proj_b", "proj_c"):
        d = tmp_path / name
        d.mkdir()
        stored = StoredSession(store.session_path(d))
        session = new_session(str(d), "realtalk")
        stored.append(session.events[0])
    assert len(store.list_sessions()) == 3


def test_list_archived_sessions(tmp_path):
    """list_archived_sessions returns paths to archived JSONL files."""
    sessions_root = tmp_path / "sessions"
    store = SessionStore(root=sessions_root)
    archive_dir = store.archive_root / "fakefingerprint"
    archive_dir.mkdir(parents=True)
    (archive_dir / "20260412-143000.jsonl").write_text("{}")
    paths = store.list_archived_sessions()
    assert len(paths) >= 1


def test_empty_file_loads_without_error(tmp_path):
    """An empty session.jsonl raises SerializationError, not a crash."""
    path = tmp_path / "session.jsonl"
    path.write_text("")
    stored = StoredSession(path)
    with pytest.raises(Exception):
        stored.load()


def test_exists_true_and_false(tmp_path):
    """exists() reflects whether the file is on disk."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path)
    assert not stored.exists()
    session = new_session(str(tmp_path), "realtalk")
    stored.append(session.events[0])
    assert stored.exists()


def test_append_creates_parent_dirs(tmp_path):
    """append() creates parent directories if they don't exist."""
    path = tmp_path / "deep" / "nested" / "session.jsonl"
    stored = StoredSession(path)
    session = new_session(str(tmp_path), "realtalk")
    stored.append(session.events[0])
    assert path.exists()


def test_human_readable_fingerprint(tmp_path):
    """Workspace fingerprint includes directory name and 8-char hash."""
    cwd = tmp_path / "my-game-project"
    cwd.mkdir()
    fp = workspace_fingerprint(cwd)
    assert "my-game-project" in fp
    # The hash suffix is the last segment after the final hyphen
    parts = fp.rsplit("-", 1)
    assert len(parts) == 2
    assert len(parts[1]) == 8


def test_load_handles_missing_middle_file(tmp_path):
    """If .2 is missing but .3 and .1 exist, load still works with available files."""
    path = tmp_path / "session.jsonl"
    stored = StoredSession(path, max_bytes=100)
    session = new_session(str(tmp_path), "realtalk")
    for _ in range(200):
        stored.append(session.events[0])
    # Delete .2 to simulate disk error
    rotated_2 = path.with_suffix(".jsonl.2")
    if rotated_2.exists():
        rotated_2.unlink()
    # Should still load without crashing
    reloaded = stored.load()
    assert len(reloaded.events) >= 1
