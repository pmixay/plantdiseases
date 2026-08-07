"""Small in-memory limiter for the single-replica team beta."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from app.api.errors import AccountAPIError


class InMemoryRateLimiter:
    """Sliding-window limiter which never persists addresses or subjects."""

    def __init__(
        self,
        *,
        requests: int,
        window_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
        max_buckets: int = 10_000,
    ):
        self._requests = requests
        self._window_seconds = window_seconds
        self._monotonic = monotonic
        self._max_buckets = max_buckets
        self._buckets: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str) -> None:
        now = self._monotonic()
        cutoff = now - self._window_seconds
        bucket_key = (bucket, key)
        with self._lock:
            if bucket_key not in self._buckets and len(self._buckets) >= self._max_buckets:
                stale = [
                    candidate
                    for candidate, timestamps in self._buckets.items()
                    if not timestamps or timestamps[-1] <= cutoff
                ]
                for candidate in stale:
                    del self._buckets[candidate]
                if len(self._buckets) >= self._max_buckets:
                    raise AccountAPIError(
                        "rate_limited",
                        status_code=429,
                        headers={"Retry-After": str(self._window_seconds)},
                    )
            timestamps = self._buckets.setdefault(bucket_key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self._requests:
                raise AccountAPIError(
                    "rate_limited",
                    status_code=429,
                    headers={"Retry-After": str(self._window_seconds)},
                )
            timestamps.append(now)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
