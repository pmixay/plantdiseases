"""Database engine lifecycle for the private account store."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseHandle(Protocol):
    session_factory: sessionmaker[Session]

    def ping(self) -> None: ...

    def close(self) -> None: ...


class Database:
    """Owns the SQLAlchemy engine without ever rendering bound values."""

    def __init__(self, url: str, *, engine: Engine | None = None):
        self.engine = engine or create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 3,
                "options": "-c statement_timeout=3000",
            },
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def close(self) -> None:
        self.engine.dispose()
