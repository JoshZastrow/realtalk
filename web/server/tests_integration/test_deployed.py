"""Integration tests that hit the deployed realtalk-web Cloud Run services.

Run against a live deploy with:

    REALTALK_API_URL=https://… REALTALK_WS_URL=wss://… \
        pytest tests_integration/ -v

Or, if you've just run `terraform apply`, the Makefile target
`smoke-deployed` pulls the URLs via `terraform output` and exports them.

These tests exercise the full HTTP → WebSocket → PTY → CLI loop. They are
skipped when the env vars aren't set, so they won't interfere with the
unit-test suite.
"""

from __future__ import annotations

import json
import os
import time

import ssl

import certifi
import httpx
import pytest
import websockets


_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

API_URL: str = os.environ.get("REALTALK_API_URL", "")
WS_URL: str = os.environ.get("REALTALK_WS_URL", "")

pytestmark = pytest.mark.skipif(
    not (API_URL and WS_URL),
    reason="REALTALK_API_URL and REALTALK_WS_URL must be set to run integration tests",
)

ORIGIN = os.environ.get("REALTALK_TEST_ORIGIN", "https://conle.ai")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_api_healthz_returns_ok() -> None:
    r = httpx.get(f"{API_URL}/_health", timeout=10.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ws_healthz_returns_ok() -> None:
    # WS service also serves a plain HTTP healthcheck — the Cloud Run
    # startup probe uses it.
    ws_http = WS_URL.replace("wss://", "https://").replace("ws://", "http://")
    r = httpx.get(f"{ws_http}/_health", timeout=10.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /session
# ---------------------------------------------------------------------------


def _mint_session() -> dict[str, object]:
    r = httpx.post(
        f"{API_URL}/session",
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        content="{}",
        timeout=10.0,
    )
    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body: dict[str, object] = r.json()
    return body


def test_session_mint_returns_wellformed_payload() -> None:
    body = _mint_session()
    assert isinstance(body["session_id"], str) and body["session_id"].startswith("s_")
    assert isinstance(body["token"], str) and "." in body["token"]
    assert isinstance(body["ws_url"], str) and body["ws_url"].startswith("wss://")
    assert body["ws_url"].endswith(f"token={body['token']}")
    assert body["expires_in_s"] == 300


def test_session_rejects_disallowed_origin() -> None:
    r = httpx.post(
        f"{API_URL}/session",
        headers={"Origin": "https://evil.example.com", "Content-Type": "application/json"},
        content="{}",
        timeout=10.0,
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "forbidden_origin"


# ---------------------------------------------------------------------------
# WebSocket → PTY loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_full_loop_spawns_cli_and_emits_output() -> None:
    """End-to-end: mint a token, open the socket, receive ready + CLI output."""
    session = _mint_session()
    ws_url = str(session["ws_url"])

    async with websockets.connect(ws_url, open_timeout=15, ssl=_SSL_CTX) as ws:
        # First frame must be `ready`.
        ready_raw = await _recv_with_timeout(ws, 15.0)
        ready = json.loads(ready_raw)
        assert ready["type"] == "ready"
        assert isinstance(ready["cols"], int) and ready["cols"] > 0

        # Send initial resize and wait for any output chunk (the realtalk
        # CLI prints a banner / prompt on startup).
        await ws.send(json.dumps({"type": "resize", "cols": 80, "rows": 24}))

        got_output = False
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            raw = await _recv_with_timeout(ws, max(0.1, deadline - time.monotonic()))
            frame = json.loads(raw)
            if frame["type"] == "output" and frame["data"]:
                got_output = True
                break
            if frame["type"] in ("error", "exit"):
                pytest.fail(f"unexpected server frame before output: {frame}")
        assert got_output, "did not receive any output frame within 20s"


@pytest.mark.asyncio
async def test_ws_rejects_invalid_token() -> None:
    base = WS_URL.rstrip("/")
    with pytest.raises(Exception):
        async with websockets.connect(
            f"{base}/ws?token=not-a-real-token", open_timeout=10, ssl=_SSL_CTX
        ):
            pass


@pytest.mark.asyncio
async def test_ws_frees_capacity_on_close() -> None:
    """Opening + closing a socket should free the slot so we can mint again."""
    s1 = _mint_session()
    async with websockets.connect(str(s1["ws_url"]), open_timeout=15, ssl=_SSL_CTX) as ws:
        await _recv_with_timeout(ws, 15.0)  # drain the `ready` frame
    # After close, the slot must be released — new mint should succeed.
    s2 = _mint_session()
    assert s2["session_id"] != s1["session_id"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _recv_with_timeout(ws: object, timeout: float) -> str:
    import asyncio

    data = await asyncio.wait_for(ws.recv(), timeout=timeout)  # type: ignore[attr-defined]
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return str(data)
