"""Wire-protocol round-trip and validation tests."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from realtalk_web.protocol import (
    ClientFrame,
    ErrorFrame,
    ExitFrame,
    InputFrame,
    OutputFrame,
    ReadyFrame,
    ResizeFrame,
    ServerFrame,
    parse_client_frame,
    parse_server_frame,
)

client_adapter: TypeAdapter[ClientFrame] = TypeAdapter(ClientFrame)
server_adapter: TypeAdapter[ServerFrame] = TypeAdapter(ServerFrame)


class TestClientFrames:
    def test_input_frame_round_trip(self) -> None:
        raw = {"type": "input", "data": "hello"}
        frame = client_adapter.validate_python(raw)
        assert isinstance(frame, InputFrame)
        assert frame.data == "hello"
        assert frame.model_dump() == raw

    def test_resize_frame_round_trip(self) -> None:
        raw = {"type": "resize", "cols": 100, "rows": 30}
        frame = client_adapter.validate_python(raw)
        assert isinstance(frame, ResizeFrame)
        assert frame.cols == 100
        assert frame.rows == 30

    def test_resize_cols_out_of_range_low(self) -> None:
        with pytest.raises(ValidationError):
            client_adapter.validate_python({"type": "resize", "cols": 10, "rows": 30})

    def test_resize_cols_out_of_range_high(self) -> None:
        with pytest.raises(ValidationError):
            client_adapter.validate_python({"type": "resize", "cols": 500, "rows": 30})

    def test_resize_rows_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            client_adapter.validate_python({"type": "resize", "cols": 80, "rows": 5})

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            client_adapter.validate_python({"type": "nope", "data": "x"})

    def test_missing_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            client_adapter.validate_python({"data": "x"})

    def test_parse_client_frame_from_json(self) -> None:
        frame = parse_client_frame('{"type": "input", "data": "abc"}')
        assert isinstance(frame, InputFrame)
        assert frame.data == "abc"

    def test_parse_client_frame_rejects_malformed_json(self) -> None:
        with pytest.raises(ValueError):
            parse_client_frame("not json {")

    def test_parse_client_frame_rejects_non_object(self) -> None:
        with pytest.raises(ValueError):
            parse_client_frame('"just a string"')


class TestServerFrames:
    def test_ready_frame_round_trip(self) -> None:
        raw = {"type": "ready", "cols": 80, "rows": 24}
        frame = server_adapter.validate_python(raw)
        assert isinstance(frame, ReadyFrame)
        assert frame.model_dump() == raw

    def test_output_frame_round_trip(self) -> None:
        raw = {"type": "output", "data": "\x1b[31mred\x1b[0m"}
        frame = server_adapter.validate_python(raw)
        assert isinstance(frame, OutputFrame)
        assert frame.data == "\x1b[31mred\x1b[0m"

    def test_exit_frame_round_trip(self) -> None:
        raw = {"type": "exit", "code": 0}
        frame = server_adapter.validate_python(raw)
        assert isinstance(frame, ExitFrame)
        assert frame.code == 0

    def test_error_frame_round_trip(self) -> None:
        raw = {"type": "error", "code": "invalid_token", "message": "nope"}
        frame = server_adapter.validate_python(raw)
        assert isinstance(frame, ErrorFrame)
        assert frame.code == "invalid_token"

    def test_parse_server_frame(self) -> None:
        frame = parse_server_frame('{"type": "output", "data": "hi"}')
        assert isinstance(frame, OutputFrame)


class TestSerialization:
    def test_frames_serialize_to_compact_json(self) -> None:
        frame = OutputFrame(data="x")
        # model_dump_json gives the on-wire shape; must include discriminator
        payload = frame.model_dump_json()
        assert '"type":"output"' in payload
        assert '"data":"x"' in payload
