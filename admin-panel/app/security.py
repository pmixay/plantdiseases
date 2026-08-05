from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class LocalSession:
    session_id: str
    csrf_token: str
    created_at: float
    last_seen_at: float


class LocalSessionStore:
    """Small in-memory session store for the loopback-only web UI."""

    def __init__(self, *, ttl_seconds: int = 12 * 60 * 60, max_sessions: int = 8):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.bootstrap_token = secrets.token_urlsafe(32)
        self._sessions: dict[str, LocalSession] = {}
        self._lock = threading.RLock()

    def valid_bootstrap_token(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, self.bootstrap_token)

    def consume_bootstrap_token(self, candidate: str | None) -> bool:
        """Accept the printed launcher token once, then rotate it immediately."""
        with self._lock:
            if not self.valid_bootstrap_token(candidate):
                return False
            self.bootstrap_token = secrets.token_urlsafe(32)
            return True

    def create(self) -> LocalSession:
        now = time.time()
        with self._lock:
            self._prune(now)
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda item: item.last_seen_at)
                self._sessions.pop(oldest.session_id, None)
            session = LocalSession(
                session_id=secrets.token_urlsafe(32),
                csrf_token=secrets.token_urlsafe(32),
                created_at=now,
                last_seen_at=now,
            )
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str | None) -> LocalSession | None:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(session_id)
            if session:
                session.last_seen_at = now
            return session

    def remove(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def check_csrf(self, session: LocalSession, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, session.csrf_token)

    def _prune(self, now: float) -> None:
        expired = [
            key for key, value in self._sessions.items()
            if now - value.last_seen_at > self.ttl_seconds
        ]
        for key in expired:
            self._sessions.pop(key, None)
