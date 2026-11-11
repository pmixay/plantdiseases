from __future__ import annotations

import pytest
from app.api.errors import AccountAPIError
from app.rate_limit import InMemoryRateLimiter


def test_limiter_is_scoped_and_expires_without_persistence():
    now = [100.0]
    limiter = InMemoryRateLimiter(
        requests=2,
        window_seconds=10,
        monotonic=lambda: now[0],
    )

    limiter.check("exchange", "127.0.0.1")
    limiter.check("exchange", "127.0.0.1")
    limiter.check("refresh", "127.0.0.1")
    with pytest.raises(AccountAPIError) as exc_info:
        limiter.check("exchange", "127.0.0.1")
    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "10"}

    now[0] = 111.0
    limiter.check("exchange", "127.0.0.1")


def test_limiter_caps_distinct_in_memory_keys_and_reclaims_stale_buckets():
    now = [100.0]
    limiter = InMemoryRateLimiter(
        requests=2,
        window_seconds=10,
        monotonic=lambda: now[0],
        max_buckets=2,
    )
    limiter.check("exchange", "address-one")
    limiter.check("exchange", "address-two")
    with pytest.raises(AccountAPIError, match="rate_limited"):
        limiter.check("exchange", "address-three")

    now[0] = 111.0
    limiter.check("exchange", "address-three")
