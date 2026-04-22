"""Shared concurrent-session counter used by realtalk-api and realtalk-ws.

Why a shared counter: realtalk-api (concurrency=80) mints tokens and
must know whether realtalk-ws (concurrency=1, max=10) has room. Both
services talk to the same Firestore document.

Design:
- `FirestoreBackend`: single document at `capacity/global` with field
  `active`, updated via transactional +1/-1.
- `InMemoryBackend`: used in tests and for local single-process dev.
- A `CapacityCounter.slot(session_id)` context manager is the preferred
  entry point for realtalk-ws: acquire on enter, release on exit
  (including on exception).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class Backend(Protocol):
    def add(self, session_id: str) -> int: ...
    def remove(self, session_id: str) -> int: ...
    def count(self) -> int: ...


class InMemoryBackend:
    """Thread-safe in-memory implementation — tests + local dev."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def add(self, session_id: str) -> int:
        with self._lock:
            self._active.add(session_id)
            return len(self._active)

    def remove(self, session_id: str) -> int:
        with self._lock:
            self._active.discard(session_id)
            return len(self._active)

    def count(self) -> int:
        with self._lock:
            return len(self._active)


class FirestoreBackend:
    """Production backend: Firestore collection `capacity`, doc `global`.

    Schema:
        capacity/global {
            active: array<string>     # session ids currently holding a slot
        }

    Array semantics give us an idempotent add/remove even across retries
    and concurrent writers (transactional ArrayUnion/ArrayRemove).
    """

    COLLECTION = "capacity"
    DOC_ID = "global"

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client()
        self._client: Any = client

    def _doc(self) -> Any:
        return self._client.collection(self.COLLECTION).document(self.DOC_ID)

    def add(self, session_id: str) -> int:
        from google.cloud import firestore

        doc = self._doc()
        doc.set({"active": firestore.ArrayUnion([session_id])}, merge=True)
        snap = doc.get()
        return len(snap.to_dict().get("active", []) if snap.exists else [])

    def remove(self, session_id: str) -> int:
        from google.cloud import firestore

        doc = self._doc()
        doc.set({"active": firestore.ArrayRemove([session_id])}, merge=True)
        snap = doc.get()
        return len(snap.to_dict().get("active", []) if snap.exists else [])

    def count(self) -> int:
        snap = self._doc().get()
        if not snap.exists:
            return 0
        return len(snap.to_dict().get("active", []))


class CapacityCounter:
    class AtCapacity(Exception):
        """Raised by acquire() when the global limit is reached."""

    def __init__(self, *, backend: Backend, max_sessions: int) -> None:
        self._backend = backend
        self._max = max_sessions

    def active(self) -> int:
        return self._backend.count()

    def has_room(self) -> bool:
        return self._backend.count() < self._max

    def acquire(self, session_id: str) -> None:
        # Firestore ArrayUnion is idempotent — adding a present id is a no-op.
        # We check count first, then add; a race can over-admit by a small
        # amount, which is acceptable (see spec §Capacity counter).
        if not self.has_room():
            raise CapacityCounter.AtCapacity(
                f"capacity full: {self.active()}/{self._max}"
            )
        self._backend.add(session_id)

    def release(self, session_id: str) -> None:
        self._backend.remove(session_id)

    @contextmanager
    def slot(self, session_id: str) -> Iterator[None]:
        self.acquire(session_id)
        try:
            yield
        finally:
            try:
                self.release(session_id)
            except Exception:
                logger.exception("capacity release failed for %s", session_id)
