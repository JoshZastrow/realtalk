"""Origin allowlist, signed session tokens, and in-process rate limiting.

Security posture (see spec §Security & abuse):
- Origin header is a first layer, not a boundary (trivially spoofable).
- Tokens are HMAC-SHA256 over `{session_id, issued_at, nonce}`, TTL 5 min.
- Rate limiting is per source-IP, in-process; for the api service this is
  fine because there's typically 1 api instance under normal load. If we
  scale api past 1 instance, move this to Firestore too.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

TOKEN_TTL_S = 300  # 5 minutes
_NONCE_BYTES = 8


class TokenError(ValueError):
    """Raised when a token is missing, expired, malformed, or tampered."""


@dataclass(frozen=True)
class TokenClaims:
    session_id: str
    issued_at: int
    nonce: str


def is_origin_allowed(origin: str | None, allowed: Sequence[str]) -> bool:
    if not origin:
        return False
    return origin in allowed


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def mint_token(
    session_id: str,
    *,
    key: bytes,
    now: Callable[[], float] = time.time,
) -> str:
    """Mint a signed short-lived token for opening a WebSocket."""
    payload = {
        "sid": session_id,
        "iat": int(now()),
        "nonce": secrets.token_hex(_NONCE_BYTES),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(key, payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_token(
    token: str,
    *,
    key: bytes,
    now: Callable[[], float] = time.time,
    ttl_s: int = TOKEN_TTL_S,
) -> TokenClaims:
    """Verify signature + TTL, return parsed claims. Raises TokenError on failure."""
    if not token or token.count(".") != 1:
        raise TokenError("malformed token")
    payload_b64, sig_b64 = token.split(".", 1)
    try:
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise TokenError(f"malformed token: {exc}") from exc

    expected = hmac.new(key, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise TokenError("bad signature")

    try:
        payload = json.loads(payload_bytes)
        sid = str(payload["sid"])
        iat = int(payload["iat"])
        nonce = str(payload["nonce"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise TokenError(f"bad claims: {exc}") from exc

    if int(now()) - iat > ttl_s:
        raise TokenError("expired")

    return TokenClaims(session_id=sid, issued_at=iat, nonce=nonce)


class RateLimiter:
    """Per-IP sliding-window rate limiter. In-memory; single-instance scope."""

    def __init__(
        self,
        *,
        max_per_hour: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_per_hour
        self._clock = clock
        self._window_s = 3600.0
        self._hits: dict[str, deque[float]] = {}

    def check(self, ip: str) -> bool:
        """Record a hit for `ip`. Return True if allowed, False if over quota."""
        now = self._clock()
        cutoff = now - self._window_s
        q = self._hits.setdefault(ip, deque())
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        return True

    def snapshot(self) -> dict[str, int]:
        """For debugging only — current hit counts per IP."""
        return {ip: len(q) for ip, q in self._hits.items()}


def iter_allowed_origins(env_value: str | None) -> Iterable[str]:
    """Parse a comma-separated origin list from env."""
    if not env_value:
        return ()
    return tuple(s.strip() for s in env_value.split(",") if s.strip())
