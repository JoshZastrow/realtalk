"""Wire protocol for the Realtalk web embed.

This module is the single source of truth for every message flowing
between the browser (xterm.js) and the Cloud Run servers (realtalk-api,
realtalk-ws). TypeScript types for the client are generated from these
Pydantic models via `make protocol`.

Design note: frames are narrow (one field set per `type`) and use a
discriminated union so parsing errors point at the offending field, not
a vague "invalid frame."
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError


class InputFrame(BaseModel):
    type: Literal["input"] = "input"
    data: str


class ResizeFrame(BaseModel):
    type: Literal["resize"] = "resize"
    cols: int = Field(ge=20, le=300)
    rows: int = Field(ge=10, le=100)


ClientFrame = Annotated[
    InputFrame | ResizeFrame,
    Field(discriminator="type"),
]


class ReadyFrame(BaseModel):
    type: Literal["ready"] = "ready"
    cols: int
    rows: int


class OutputFrame(BaseModel):
    type: Literal["output"] = "output"
    data: str


class ExitFrame(BaseModel):
    type: Literal["exit"] = "exit"
    code: int


class ErrorFrame(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


ServerFrame = Annotated[
    ReadyFrame | OutputFrame | ExitFrame | ErrorFrame,
    Field(discriminator="type"),
]


_client_adapter: TypeAdapter[ClientFrame] = TypeAdapter(ClientFrame)
_server_adapter: TypeAdapter[ServerFrame] = TypeAdapter(ServerFrame)


def parse_client_frame(raw: str | bytes) -> ClientFrame:
    """Parse a wire frame sent by the browser. Raises ValueError on malformed input."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("frame must be a JSON object")
    try:
        return _client_adapter.validate_python(obj)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def parse_server_frame(raw: str | bytes) -> ServerFrame:
    """Parse a wire frame emitted by the server. Used by tests and clients."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("frame must be a JSON object")
    try:
        return _server_adapter.validate_python(obj)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


ERROR_INVALID_TOKEN = "invalid_token"
ERROR_INVALID_FRAME = "invalid_frame"
ERROR_SESSION_TIMEOUT = "session_timeout"
ERROR_PTY_SPAWN_FAILED = "pty_spawn_failed"
ERROR_INTERNAL = "internal"

CLOSE_NORMAL = 1000
CLOSE_POLICY = 1008
CLOSE_INTERNAL = 1011
CLOSE_SESSION_TIMEOUT = 4000
