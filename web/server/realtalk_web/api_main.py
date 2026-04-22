"""FastAPI app for realtalk-api: mint session tokens.

This service stays cheap and highly concurrent (concurrency=80) so the
mint endpoint is always reachable — even when realtalk-ws (concurrency=1,
max=10) is fully saturated. On saturation we return `503 at_capacity`
with a `retry_after_s` hint, so the frontend can display a clean "game
full" message instead of a socket timeout.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from realtalk_web.auth import RateLimiter, is_origin_allowed, mint_token
from realtalk_web.capacity import Backend, CapacityCounter, FirestoreBackend, InMemoryBackend

SESSION_TOKEN_TTL_S = 300
DEFAULT_RETRY_AFTER_S = 30


@dataclass
class AppConfig:
    allowed_origins: Sequence[str]
    signing_key: bytes
    max_sessions: int = 10
    rate_limit_per_hour: int = 5
    ws_base_url: str = "wss://ws.realtalk.conle.ai"
    firestore_project: str | None = None
    use_in_memory_capacity: bool = False

    @classmethod
    def from_env(cls) -> AppConfig:
        origins = os.environ.get("REALTALK_ALLOWED_ORIGINS", "https://conle.ai")
        signing = os.environ.get("REALTALK_SIGNING_KEY")
        if not signing:
            raise RuntimeError("REALTALK_SIGNING_KEY not set")
        return cls(
            allowed_origins=tuple(s.strip() for s in origins.split(",") if s.strip()),
            signing_key=signing.encode("utf-8"),
            max_sessions=int(os.environ.get("REALTALK_MAX_SESSIONS", "10")),
            rate_limit_per_hour=int(os.environ.get("REALTALK_RATE_LIMIT_PER_HOUR", "5")),
            ws_base_url=os.environ.get("REALTALK_WS_BASE_URL", "wss://ws.realtalk.conle.ai"),
            firestore_project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            use_in_memory_capacity=(
                os.environ.get("REALTALK_CAPACITY_BACKEND", "firestore") == "memory"
            ),
        )


def _error(code: str, message: str, status: int, **extra: object) -> JSONResponse:
    body: dict[str, object] = {"error": code, "message": message}
    body.update(extra)
    return JSONResponse(body, status_code=status)


def _client_ip(request: Request) -> str:
    # X-Forwarded-For is trustworthy on Cloud Run (single proxy hop).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def build_app(
    *,
    config: AppConfig,
    counter: CapacityCounter | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    if counter is None:
        backend: Backend = (
            InMemoryBackend() if config.use_in_memory_capacity else FirestoreBackend()
        )
        counter = CapacityCounter(backend=backend, max_sessions=config.max_sessions)
    if rate_limiter is None:
        rate_limiter = RateLimiter(max_per_hour=config.rate_limit_per_hour)

    app = FastAPI(title="realtalk-api", version="0.1.0")
    # Stash on app.state so tests can poke it.
    app.state.config = config
    app.state.counter = counter
    app.state.rate_limiter = rate_limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.get("/_health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/session")
    async def create_session(request: Request) -> JSONResponse:
        origin = request.headers.get("origin")
        if not is_origin_allowed(origin, config.allowed_origins):
            return _error("forbidden_origin", "origin not allowed", 403)

        ip = _client_ip(request)
        if not rate_limiter.check(ip):
            return _error("rate_limited", "too many sessions", 429)

        if not counter.has_room():
            return _error(
                "at_capacity",
                "all game slots in use",
                503,
                retry_after_s=DEFAULT_RETRY_AFTER_S,
            )

        session_id = f"s_{secrets.token_hex(12)}"
        token = mint_token(session_id, key=config.signing_key)
        return JSONResponse(
            {
                "session_id": session_id,
                "token": token,
                "ws_url": f"{config.ws_base_url}/ws?token={token}",
                "expires_in_s": SESSION_TOKEN_TTL_S,
            }
        )

    return app


def create_app() -> FastAPI:
    """Production factory — reads env, talks to real Firestore."""
    return build_app(config=AppConfig.from_env())
