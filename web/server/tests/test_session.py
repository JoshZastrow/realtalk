"""PTY lifecycle tests — spawn, stdin/stdout, resize, timeouts, cleanup."""

from __future__ import annotations

import asyncio
import os

import pytest

from realtalk_web.session import PTYSession, SessionTimeout

pytestmark = pytest.mark.asyncio


async def _drain_until(session: PTYSession, needle: bytes, timeout: float = 3.0) -> bytes:
    """Read output frames until `needle` appears, or timeout."""
    collected = bytearray()

    async def _loop() -> None:
        async for chunk in session.iter_output():
            collected.extend(chunk)
            if needle in collected:
                return

    await asyncio.wait_for(_loop(), timeout=timeout)
    return bytes(collected)


async def test_spawn_and_exit() -> None:
    session = PTYSession(command=["/bin/sh", "-c", "echo hello-from-pty; exit 0"], cols=80, rows=24)
    await session.start()
    output = await _drain_until(session, b"hello-from-pty", timeout=5.0)
    assert b"hello-from-pty" in output
    code = await asyncio.wait_for(session.wait_exit(), timeout=5.0)
    assert code == 0
    await session.close()


async def test_stdin_echoes_to_stdout() -> None:
    session = PTYSession(command=["/bin/cat"], cols=80, rows=24)
    await session.start()
    await session.send_input("ping\n")
    output = await _drain_until(session, b"ping", timeout=3.0)
    assert b"ping" in output
    await session.close()


async def test_resize_updates_dimensions() -> None:
    session = PTYSession(command=["/bin/cat"], cols=80, rows=24)
    await session.start()
    await session.resize(120, 40)
    assert session.cols == 120
    assert session.rows == 40
    await session.close()


async def test_resize_rejects_out_of_range() -> None:
    session = PTYSession(command=["/bin/cat"], cols=80, rows=24)
    await session.start()
    with pytest.raises(ValueError):
        await session.resize(5, 40)
    with pytest.raises(ValueError):
        await session.resize(80, 500)
    await session.close()


async def test_close_reaps_process() -> None:
    session = PTYSession(command=["/bin/cat"], cols=80, rows=24)
    await session.start()
    pid = session.pid
    assert pid is not None
    await session.close()
    # after close, process must be reaped. os.kill(pid, 0) → ProcessLookupError
    # when gone; but kernel may take a tick. Retry briefly.
    for _ in range(20):
        try:
            os.kill(pid, 0)
            await asyncio.sleep(0.05)
        except ProcessLookupError:
            return
    pytest.fail(f"pid {pid} not reaped after close()")


async def test_hard_timeout_closes_session() -> None:
    session = PTYSession(
        command=["/bin/cat"],
        cols=80,
        rows=24,
        hard_timeout_s=0.2,
        idle_timeout_s=10.0,
    )
    await session.start()
    with pytest.raises(SessionTimeout):
        await asyncio.wait_for(session.wait_exit(), timeout=3.0)
    await session.close()


async def test_idle_timeout_closes_session() -> None:
    session = PTYSession(
        command=["/bin/cat"],
        cols=80,
        rows=24,
        hard_timeout_s=10.0,
        idle_timeout_s=0.2,
    )
    await session.start()
    with pytest.raises(SessionTimeout):
        await asyncio.wait_for(session.wait_exit(), timeout=3.0)
    await session.close()


async def test_input_resets_idle_timer() -> None:
    session = PTYSession(
        command=["/bin/cat"],
        cols=80,
        rows=24,
        hard_timeout_s=10.0,
        idle_timeout_s=0.3,
    )
    await session.start()
    # Send input every 100ms for 600ms; idle timer (300ms) must NOT fire.
    for _ in range(6):
        await asyncio.sleep(0.1)
        await session.send_input("x")
    # Still alive
    assert session.pid is not None
    await session.close()


async def test_iter_output_ends_on_close() -> None:
    session = PTYSession(command=["/bin/cat"], cols=80, rows=24)
    await session.start()

    async def consume() -> int:
        n = 0
        async for _ in session.iter_output():
            n += 1
        return n

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    await session.close()
    n = await asyncio.wait_for(task, timeout=2.0)
    assert isinstance(n, int)
