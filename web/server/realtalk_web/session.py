"""PTY session lifecycle.

Wraps `ptyprocess.PtyProcess` in an asyncio-friendly shell: spawn,
bidirectional I/O, resize, idle + hard timeouts, clean reap on close.

Design: blocking PTY reads run on a dedicated thread (not the default
executor, so a hung read can't wedge other awaitables). Writes go through
the default executor. Output is pushed onto an asyncio.Queue that
`iter_output()` drains. Closing the PTY from the asyncio side is done
by sending SIGKILL to the child, which unblocks the reader thread via
EOFError on the pty master.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from collections.abc import AsyncIterator, Sequence

import ptyprocess  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


COLS_MIN, COLS_MAX = 20, 300
ROWS_MIN, ROWS_MAX = 10, 100

DEFAULT_IDLE_TIMEOUT_S = 300.0  # 5 min
DEFAULT_HARD_TIMEOUT_S = 1800.0  # 30 min


class SessionTimeout(Exception):
    """Raised (via wait_exit) when idle or hard timeout elapses."""


class PTYSession:
    def __init__(
        self,
        *,
        command: Sequence[str],
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        hard_timeout_s: float = DEFAULT_HARD_TIMEOUT_S,
        read_chunk: int = 4096,
    ) -> None:
        self._command = list(command)
        self._cols = cols
        self._rows = rows
        self._env = env
        self._idle_timeout_s = idle_timeout_s
        self._hard_timeout_s = hard_timeout_s
        self._read_chunk = read_chunk

        self._proc: ptyprocess.PtyProcess | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        self._exit_code: asyncio.Future[int] | None = None
        self._reader_thread: threading.Thread | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._closed = False
        self._last_activity = time.monotonic()
        self._started_at = time.monotonic()

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    async def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("already started")
        self._loop = asyncio.get_running_loop()
        env = dict(os.environ)
        if self._env:
            env.update(self._env)
        env.setdefault("TERM", "xterm-256color")

        self._proc = await self._loop.run_in_executor(
            None,
            lambda: ptyprocess.PtyProcess.spawn(
                self._command,
                dimensions=(self._rows, self._cols),
                env=env,
            ),
        )
        self._exit_code = self._loop.create_future()
        self._started_at = time.monotonic()
        self._last_activity = self._started_at

        # Reader runs on a dedicated daemon thread so a slow/hung read
        # never blocks other asyncio work. The thread ends when the pty
        # returns EOF (which happens when the child process exits or we
        # kill it in close()).
        self._reader_thread = threading.Thread(
            target=self._reader_thread_fn, name="pty-reader", daemon=True
        )
        self._reader_thread.start()
        self._watchdog_task = self._loop.create_task(self._watchdog_loop())

    async def send_input(self, data: str) -> None:
        self._require_started()
        assert self._proc is not None and self._loop is not None
        self._last_activity = time.monotonic()
        await self._loop.run_in_executor(None, self._proc.write, data.encode("utf-8"))

    async def resize(self, cols: int, rows: int) -> None:
        self._require_started()
        assert self._proc is not None and self._loop is not None
        if not (COLS_MIN <= cols <= COLS_MAX):
            raise ValueError(f"cols {cols} out of [{COLS_MIN}, {COLS_MAX}]")
        if not (ROWS_MIN <= rows <= ROWS_MAX):
            raise ValueError(f"rows {rows} out of [{ROWS_MIN}, {ROWS_MAX}]")
        self._cols = cols
        self._rows = rows
        await self._loop.run_in_executor(None, self._proc.setwinsize, rows, cols)

    async def iter_output(self) -> AsyncIterator[bytes]:
        """Yield PTY output chunks until the session ends."""
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk

    async def wait_exit(self) -> int:
        """Resolve with the exit code, or raise SessionTimeout on timeout."""
        assert self._exit_code is not None
        return await self._exit_code

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except (asyncio.CancelledError, Exception):
                pass

        proc = self._proc
        if proc is not None:
            try:
                if proc.isalive():
                    try:
                        proc.kill(signal.SIGKILL)
                    except Exception:
                        try:
                            os.kill(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                # Close the pty master fd so reader's blocking read returns
                # immediately even if SIGKILL delivery lags.
                try:
                    proc.close(force=True)
                except Exception:
                    logger.debug("proc.close err", exc_info=True)
            except Exception:
                logger.debug("error terminating pty", exc_info=True)

        # Wait for the reader thread to exit. Off-loop so the thread's
        # run_coroutine_threadsafe(put) can still be scheduled if needed.
        if (
            self._reader_thread is not None
            and self._reader_thread.is_alive()
            and self._loop is not None
        ):
            thread = self._reader_thread
            await self._loop.run_in_executor(
                None, lambda: thread.join(timeout=2.0)
            )

        # Queue sentinel for any lingering iter_output consumers.
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Resolve exit_code if the reader thread didn't already.
        if self._exit_code is not None and not self._exit_code.done():
            code = proc.exitstatus if proc is not None and proc.exitstatus is not None else -1
            self._exit_code.set_result(code)

    def _require_started(self) -> None:
        if self._proc is None:
            raise RuntimeError("session not started")

    def _reader_thread_fn(self) -> None:
        """Blocking reader thread — pushes chunks into the asyncio queue."""
        assert self._proc is not None and self._loop is not None
        proc = self._proc
        loop = self._loop
        try:
            while True:
                try:
                    data = proc.read(self._read_chunk)
                except EOFError:
                    break
                except Exception:
                    logger.debug("pty read error", exc_info=True)
                    break
                if not data:
                    break
                fut = asyncio.run_coroutine_threadsafe(self._queue.put(data), loop)
                try:
                    fut.result(timeout=5.0)
                except Exception:
                    break
        finally:
            # Reap the child so exitstatus is known, then resolve wait_exit.
            try:
                if proc.isalive():
                    proc.wait()
            except Exception:
                pass
            # Sentinel and exit resolution on the event loop thread.
            def _finalize() -> None:
                try:
                    self._queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                if self._exit_code is not None and not self._exit_code.done():
                    code = proc.exitstatus if proc.exitstatus is not None else -1
                    self._exit_code.set_result(code)

            try:
                loop.call_soon_threadsafe(_finalize)
            except RuntimeError:
                # Loop closed — nothing to do.
                pass

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.1)
                now = time.monotonic()
                if now - self._started_at >= self._hard_timeout_s:
                    self._fire_timeout("hard")
                    return
                if now - self._last_activity >= self._idle_timeout_s:
                    self._fire_timeout("idle")
                    return
        except asyncio.CancelledError:
            return

    def _fire_timeout(self, kind: str) -> None:
        if self._exit_code is not None and not self._exit_code.done():
            self._exit_code.set_exception(SessionTimeout(f"{kind} timeout"))
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        proc = self._proc
        if proc is not None and proc.isalive():
            try:
                proc.kill(signal.SIGKILL)
            except Exception:
                pass
