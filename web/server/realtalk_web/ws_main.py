"""FastAPI app for realtalk-ws: PTY streaming over WebSocket.

One session per instance (Cloud Run concurrency=1). On upgrade:
  1. validate the signed token → session_id
  2. confirm the capacity slot is held (or re-acquire if we're running
     without a paired api service)
  3. spawn `realtalk` in a PTY
  4. pump PTY stdout → output frames, input/resize frames → PTY
  5. on PTY exit / error / timeout, send final frame and close
  6. always release the capacity slot on exit

JSON frames only in v1 (see spec). Binary framing is a future opt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from realtalk_web.auth import TokenError, verify_token
from realtalk_web.capacity import CapacityCounter, FirestoreBackend, InMemoryBackend
from realtalk_web.protocol import (
    CLOSE_INTERNAL,
    CLOSE_NORMAL,
    CLOSE_POLICY,
    CLOSE_SESSION_TIMEOUT,
    ERROR_INTERNAL,
    ERROR_INVALID_FRAME,
    ERROR_INVALID_TOKEN,
    ERROR_PTY_SPAWN_FAILED,
    ERROR_SESSION_TIMEOUT,
    ErrorFrame,
    ExitFrame,
    InputFrame,
    OutputFrame,
    ReadyFrame,
    ResizeFrame,
    parse_client_frame,
)
from realtalk_web.session import (
    DEFAULT_HARD_TIMEOUT_S,
    DEFAULT_IDLE_TIMEOUT_S,
    PTYSession,
    SessionTimeout,
)

logger = logging.getLogger(__name__)


@dataclass
class WSConfig:
    allowed_origins: Sequence[str]
    signing_key: bytes
    max_sessions: int = 10
    command: Sequence[str] = ("realtalk",)
    initial_cols: int = 80
    initial_rows: int = 24
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S
    hard_timeout_s: float = DEFAULT_HARD_TIMEOUT_S
    use_in_memory_capacity: bool = False

    @classmethod
    def from_env(cls) -> WSConfig:
        origins = os.environ.get("REALTALK_ALLOWED_ORIGINS", "https://conle.ai")
        signing = os.environ.get("REALTALK_SIGNING_KEY")
        if not signing:
            raise RuntimeError("REALTALK_SIGNING_KEY not set")
        command_env = os.environ.get("REALTALK_COMMAND", "realtalk")
        command = tuple(command_env.split())
        return cls(
            allowed_origins=tuple(s.strip() for s in origins.split(",") if s.strip()),
            signing_key=signing.encode("utf-8"),
            max_sessions=int(os.environ.get("REALTALK_MAX_SESSIONS", "10")),
            command=command,
            initial_cols=int(os.environ.get("REALTALK_INITIAL_COLS", "80")),
            initial_rows=int(os.environ.get("REALTALK_INITIAL_ROWS", "24")),
            idle_timeout_s=float(
                os.environ.get("REALTALK_IDLE_TIMEOUT_S", str(DEFAULT_IDLE_TIMEOUT_S))
            ),
            hard_timeout_s=float(
                os.environ.get("REALTALK_HARD_TIMEOUT_S", str(DEFAULT_HARD_TIMEOUT_S))
            ),
            use_in_memory_capacity=(
                os.environ.get("REALTALK_CAPACITY_BACKEND", "firestore") == "memory"
            ),
        )


async def _send_frame(ws: WebSocket, frame: object) -> None:
    try:
        # pydantic BaseModel: use model_dump_json
        payload = (
            frame.model_dump_json()
            if hasattr(frame, "model_dump_json")
            else json.dumps(frame)
        )
        await ws.send_text(payload)
    except Exception:
        logger.debug("send_frame failed (socket likely closed)", exc_info=True)


async def _pump_output(ws: WebSocket, session: PTYSession) -> None:
    try:
        async for chunk in session.iter_output():
            text = chunk.decode("utf-8", errors="replace")
            await _send_frame(ws, OutputFrame(data=text))
    except Exception:
        logger.exception("output pump failed")


async def _pump_input(ws: WebSocket, session: PTYSession) -> str:
    """Read frames from ws until the client disconnects or sends a fatal frame.

    Returns a close reason: "client", "invalid_frame", or "error".
    """
    while True:
        try:
            raw = await ws.receive_text()
        except WebSocketDisconnect:
            return "client"
        try:
            frame = parse_client_frame(raw)
        except ValueError:
            await _send_frame(
                ws, ErrorFrame(code=ERROR_INVALID_FRAME, message="could not parse frame")
            )
            return "invalid_frame"
        if isinstance(frame, InputFrame):
            try:
                await session.send_input(frame.data)
            except Exception:
                logger.exception("send_input failed")
                return "error"
        elif isinstance(frame, ResizeFrame):
            try:
                await session.resize(frame.cols, frame.rows)
            except ValueError:
                await _send_frame(
                    ws,
                    ErrorFrame(code=ERROR_INVALID_FRAME, message="resize out of range"),
                )
                return "invalid_frame"


def build_ws_app(
    *,
    config: WSConfig,
    counter: CapacityCounter | None = None,
) -> FastAPI:
    if counter is None:
        backend = InMemoryBackend() if config.use_in_memory_capacity else FirestoreBackend()
        counter = CapacityCounter(backend=backend, max_sessions=config.max_sessions)

    app = FastAPI(title="realtalk-ws", version="0.1.0")
    app.state.config = config
    app.state.counter = counter

    @app.get("/_health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = Query(default="")) -> None:
        # Validate BEFORE accepting so an invalid client gets an HTTP 403/1008
        # rather than seeing a successful upgrade and an immediate error frame.
        try:
            claims = verify_token(token, key=config.signing_key)
        except TokenError:
            await websocket.close(code=CLOSE_POLICY, reason=ERROR_INVALID_TOKEN)
            return

        # Origin is a first-layer check; trivially spoofable.
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in config.allowed_origins:
            await websocket.close(code=CLOSE_POLICY, reason="forbidden_origin")
            return

        session_id = claims.session_id
        await websocket.accept()

        # Ensure the capacity slot is held. Normal flow: api-side acquired it
        # already. Idempotent add is safe.
        try:
            counter.acquire(session_id)
        except CapacityCounter.AtCapacity:
            await _send_frame(
                websocket,
                ErrorFrame(code="at_capacity", message="no slot available"),
            )
            await websocket.close(code=CLOSE_POLICY, reason="at_capacity")
            return

        session = PTYSession(
            command=config.command,
            cols=config.initial_cols,
            rows=config.initial_rows,
            idle_timeout_s=config.idle_timeout_s,
            hard_timeout_s=config.hard_timeout_s,
        )

        close_code = CLOSE_NORMAL
        try:
            try:
                await session.start()
            except Exception:
                logger.exception("pty spawn failed")
                await _send_frame(
                    websocket,
                    ErrorFrame(code=ERROR_PTY_SPAWN_FAILED, message="could not start game"),
                )
                close_code = CLOSE_INTERNAL
                return

            await _send_frame(websocket, ReadyFrame(cols=session.cols, rows=session.rows))

            output_task = asyncio.create_task(_pump_output(websocket, session))
            input_task = asyncio.create_task(_pump_input(websocket, session))
            exit_task = asyncio.create_task(session.wait_exit())

            done, pending = await asyncio.wait(
                {output_task, input_task, exit_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Determine outcome.
            if exit_task in done:
                try:
                    code = exit_task.result()
                    await _send_frame(websocket, ExitFrame(code=code))
                except SessionTimeout as exc:
                    await _send_frame(
                        websocket,
                        ErrorFrame(code=ERROR_SESSION_TIMEOUT, message=str(exc)),
                    )
                    close_code = CLOSE_SESSION_TIMEOUT
                except Exception:
                    logger.exception("wait_exit raised")
                    close_code = CLOSE_INTERNAL

            # Cancel whatever is still running.
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        except Exception:
            logger.exception("ws session crashed")
            await _send_frame(
                websocket,
                ErrorFrame(code=ERROR_INTERNAL, message="server error"),
            )
            close_code = CLOSE_INTERNAL
        finally:
            await session.close()
            # Only attempt close if the client hasn't already disconnected;
            # closing a disconnected socket in starlette can block forever.
            try:
                from starlette.websockets import WebSocketState

                if websocket.client_state == WebSocketState.CONNECTED:
                    await asyncio.wait_for(
                        websocket.close(code=close_code), timeout=1.0
                    )
            except Exception:
                logger.debug("ws close err", exc_info=True)
            counter.release(session_id)

    return app


def create_app() -> FastAPI:
    return build_ws_app(config=WSConfig.from_env())
