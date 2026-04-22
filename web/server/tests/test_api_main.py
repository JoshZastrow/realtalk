"""Tests for realtalk-api: POST /session, GET /_health."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from realtalk_web.api_main import AppConfig, build_app
from realtalk_web.capacity import CapacityCounter, InMemoryBackend


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        allowed_origins=("https://conle.ai",),
        signing_key=b"test-signing-key",
        max_sessions=2,
        rate_limit_per_hour=5,
        ws_base_url="wss://ws.example.test",
    )


@pytest.fixture
def counter() -> CapacityCounter:
    return CapacityCounter(backend=InMemoryBackend(), max_sessions=2)


@pytest.fixture
def client(config: AppConfig, counter: CapacityCounter) -> Iterator[TestClient]:
    app = build_app(config=config, counter=counter)
    with TestClient(app) as c:
        yield c


def test_healthz(client: TestClient) -> None:
    resp = client.get("/_health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_session_mint_success(client: TestClient) -> None:
    resp = client.post("/session", headers={"Origin": "https://conle.ai"}, json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "token" in body
    assert "session_id" in body
    assert body["ws_url"].startswith("wss://ws.example.test/ws?token=")
    assert body["expires_in_s"] == 300


def test_session_disallowed_origin(client: TestClient) -> None:
    resp = client.post("/session", headers={"Origin": "https://evil.example"}, json={})
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden_origin"


def test_session_missing_origin(client: TestClient) -> None:
    resp = client.post("/session", json={})
    assert resp.status_code == 403


def test_session_rate_limit(client: TestClient, config: AppConfig) -> None:
    headers = {"Origin": "https://conle.ai"}
    for i in range(config.rate_limit_per_hour):
        r = client.post("/session", headers=headers, json={})
        assert r.status_code == 200, f"iter {i}: {r.text}"
    resp = client.post("/session", headers=headers, json={})
    assert resp.status_code == 429
    assert resp.json()["error"] == "rate_limited"


def test_session_at_capacity(client: TestClient, counter: CapacityCounter) -> None:
    headers = {"Origin": "https://conle.ai"}
    # Simulate WS having filled all slots: API mint should then return 503.
    counter.acquire("s_held_1")
    counter.acquire("s_held_2")
    r = client.post("/session", headers=headers, json={})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "at_capacity"
    assert body["retry_after_s"] == 30


def test_session_id_format(client: TestClient) -> None:
    resp = client.post("/session", headers={"Origin": "https://conle.ai"}, json={})
    sid = resp.json()["session_id"]
    assert sid.startswith("s_")
    assert len(sid) > 5


def test_minted_token_can_be_verified(client: TestClient, config: AppConfig) -> None:
    from realtalk_web.auth import verify_token

    resp = client.post("/session", headers={"Origin": "https://conle.ai"}, json={})
    token = resp.json()["token"]
    claims = verify_token(token, key=config.signing_key)
    assert claims.session_id == resp.json()["session_id"]
