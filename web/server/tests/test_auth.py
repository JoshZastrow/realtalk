"""Origin check, HMAC token mint/verify, rate limit."""

from __future__ import annotations

import time

import pytest

from realtalk_web.auth import (
    RateLimiter,
    TokenError,
    is_origin_allowed,
    mint_token,
    verify_token,
)

KEY = b"test-signing-key-do-not-use-in-prod"
ALLOWED = ("https://conle.ai", "http://localhost:5173")


class TestOrigin:
    def test_allowed_origin(self) -> None:
        assert is_origin_allowed("https://conle.ai", ALLOWED)

    def test_disallowed_origin(self) -> None:
        assert not is_origin_allowed("https://evil.example", ALLOWED)

    def test_missing_origin(self) -> None:
        assert not is_origin_allowed(None, ALLOWED)

    def test_origin_case_sensitive_host(self) -> None:
        # exact match only; case-insensitive only for scheme/host if needed later
        assert not is_origin_allowed("https://CONLE.AI", ALLOWED)


class TestToken:
    def test_mint_and_verify(self) -> None:
        token = mint_token("s_abc", key=KEY)
        claims = verify_token(token, key=KEY)
        assert claims.session_id == "s_abc"

    def test_expired_token_rejected(self) -> None:
        token = mint_token("s_abc", key=KEY, now=lambda: 1_000)
        with pytest.raises(TokenError, match="expired"):
            verify_token(token, key=KEY, now=lambda: 1_000 + 10_000)

    def test_tampered_token_rejected(self) -> None:
        token = mint_token("s_abc", key=KEY)
        bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        with pytest.raises(TokenError):
            verify_token(bad, key=KEY)

    def test_wrong_key_rejected(self) -> None:
        token = mint_token("s_abc", key=KEY)
        with pytest.raises(TokenError):
            verify_token(token, key=b"different-key")

    def test_malformed_token_rejected(self) -> None:
        with pytest.raises(TokenError):
            verify_token("not.a.token", key=KEY)

    def test_token_issued_at_included(self) -> None:
        token = mint_token("s_abc", key=KEY, now=lambda: 42)
        claims = verify_token(token, key=KEY, now=lambda: 42)
        assert claims.issued_at == 42
        assert claims.session_id == "s_abc"
        assert isinstance(claims.nonce, str) and len(claims.nonce) >= 8


class TestRateLimiter:
    def test_allows_under_limit(self) -> None:
        rl = RateLimiter(max_per_hour=5, clock=lambda: 0.0)
        for _ in range(5):
            assert rl.check("1.2.3.4")

    def test_blocks_over_limit(self) -> None:
        rl = RateLimiter(max_per_hour=3, clock=lambda: 0.0)
        for _ in range(3):
            assert rl.check("1.2.3.4")
        assert not rl.check("1.2.3.4")

    def test_window_expires(self) -> None:
        t = [0.0]
        rl = RateLimiter(max_per_hour=2, clock=lambda: t[0])
        assert rl.check("1.2.3.4")
        assert rl.check("1.2.3.4")
        assert not rl.check("1.2.3.4")
        t[0] = 3601.0
        assert rl.check("1.2.3.4")

    def test_independent_ips(self) -> None:
        rl = RateLimiter(max_per_hour=1, clock=lambda: 0.0)
        assert rl.check("a")
        assert rl.check("b")
        assert not rl.check("a")


class TestRealtimeClock:
    def test_mint_uses_time_time_by_default(self) -> None:
        before = int(time.time())
        token = mint_token("s_abc", key=KEY)
        claims = verify_token(token, key=KEY)
        after = int(time.time())
        assert before <= claims.issued_at <= after
