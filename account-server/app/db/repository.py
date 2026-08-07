"""Transaction boundary shared by account domain repositories."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker


class AccountRepository:
    """Provides short, explicit commit/rollback scopes.

    Domain-specific query methods are added beside their endpoint checkpoints;
    callers never receive a process-global session.
    """

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
