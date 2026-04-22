"""Capacity counter (Firestore-backed, but tested with in-memory fake)."""

from __future__ import annotations

import pytest

from realtalk_web.capacity import CapacityCounter, InMemoryBackend


@pytest.fixture
def counter() -> CapacityCounter:
    return CapacityCounter(backend=InMemoryBackend(), max_sessions=3)


def test_read_starts_at_zero(counter: CapacityCounter) -> None:
    assert counter.active() == 0


def test_has_room_initially(counter: CapacityCounter) -> None:
    assert counter.has_room()


def test_acquire_increments(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    assert counter.active() == 1


def test_release_decrements(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    counter.release("s1")
    assert counter.active() == 0


def test_at_capacity_blocks_acquire(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    counter.acquire("s2")
    counter.acquire("s3")
    assert not counter.has_room()
    with pytest.raises(CapacityCounter.AtCapacity):
        counter.acquire("s4")


def test_release_after_full_allows_new(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    counter.acquire("s2")
    counter.acquire("s3")
    counter.release("s2")
    assert counter.has_room()
    counter.acquire("s4")
    assert counter.active() == 3


def test_idempotent_release_is_safe(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    counter.release("s1")
    counter.release("s1")  # second release should not underflow
    assert counter.active() == 0


def test_duplicate_acquire_is_idempotent(counter: CapacityCounter) -> None:
    counter.acquire("s1")
    counter.acquire("s1")  # same session id — should not double-count
    assert counter.active() == 1


def test_slot_guard_releases_on_exit() -> None:
    counter = CapacityCounter(backend=InMemoryBackend(), max_sessions=1)
    with counter.slot("s1"):
        assert counter.active() == 1
    assert counter.active() == 0


def test_slot_guard_releases_on_exception() -> None:
    counter = CapacityCounter(backend=InMemoryBackend(), max_sessions=1)
    with pytest.raises(RuntimeError):
        with counter.slot("s1"):
            raise RuntimeError("boom")
    assert counter.active() == 0
