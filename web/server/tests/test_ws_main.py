"""Tests for realtalk-ws WebSocket endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from realtalk_web.auth import mint_token
from realtalk_web.capacity import CapacityCounter, InMemoryBackend
from realtalk_web.ws_main import WSConfig, build_ws_app


@pytest.fixture
def config() -> WSConfig:
    return WSConfig(
        allowed_origins=("https://conle.ai",),
        signing_key=b"test-signing-key",
        max_sessions=2,
        # cheap command so tests are fast
        command=("/bin/cat",),
        idle_timeout_s=5.0,
        hard_timeout_s=10.0,
    )


@pytest.fixture
def counter() -> CapacityCounter:
    return CapacityCounter(backend=InMemoryBackend(), max_sessions=2)


@pytest.fixture
def client(config: WSConfig, counter: CapacityCounter) -> Iterator[TestClient]:
    app = build_ws_app(config=config, counter=counter)
    with TestClient(app) as c:
        yield c


def test_ws_rejects_missing_token(client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_ws_rejects_invalid_token(client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=not-a-token"):
            pass


def test_ws_rejects_expired_token(client: TestClient) -> None:
    token = mint_token("s_expired", key=b"test-signing-key", now=lambda: 1000)
    # with our real signing key but 10h in the past → expired
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws?token={token}"):
            pass


def test_ws_happy_path_echo(
    client: TestClient, config: WSConfig, counter: CapacityCounter
) -> None:
    counter.acquire("s_test")  # simulate api having acquired the slot
    token = mint_token("s_test", key=config.signing_key)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready"
        assert ready["cols"] == 80
        assert ready["rows"] == 24

        ws.send_text(json.dumps({"type": "input", "data": "hello-rt\n"}))
        saw_echo = False
        for _ in range(20):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "output" and "hello-rt" in msg["data"]:
                saw_echo = True
                break
        assert saw_echo


def test_ws_resize_accepted(
    client: TestClient, config: WSConfig, counter: CapacityCounter
) -> None:
    counter.acquire("s_resize")
    token = mint_token("s_resize", key=config.signing_key)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        # consume ready
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
        # no assertion that the server acks — SIGWINCH is silent — but it
        # must not drop the socket. Send input afterward and verify flow.
        ws.send_text(json.dumps({"type": "input", "data": "pong\n"}))
        saw = False
        for _ in range(20):
            m = json.loads(ws.receive_text())
            if m["type"] == "output" and "pong" in m["data"]:
                saw = True
                break
        assert saw


def test_ws_rejects_malformed_frame(
    client: TestClient, config: WSConfig, counter: CapacityCounter
) -> None:
    counter.acquire("s_bad")
    token = mint_token("s_bad", key=config.signing_key)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        json.loads(ws.receive_text())  # ready
        ws.send_text("not json {")
        # Server should emit an error frame and close.
        seen_error = False
        for _ in range(5):
            try:
                m = json.loads(ws.receive_text())
            except Exception:
                break
            if m["type"] == "error" and m["code"] == "invalid_frame":
                seen_error = True
                break
        assert seen_error


def test_ws_releases_capacity_on_close(
    client: TestClient, config: WSConfig, counter: CapacityCounter
) -> None:
    counter.acquire("s_release")
    token = mint_token("s_release", key=config.signing_key)
    assert counter.active() == 1
    with client.websocket_connect(f"/ws?token={token}") as ws:
        json.loads(ws.receive_text())
    # give the server a tick to release
    import time

    for _ in range(100):
        if counter.active() == 0:
            break
        time.sleep(0.05)
    assert counter.active() == 0


def test_healthz_on_ws_service(client: TestClient) -> None:
    resp = client.get("/_health")
    assert resp.status_code == 200
