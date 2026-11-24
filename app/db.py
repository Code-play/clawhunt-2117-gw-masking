"""Database engine and session helpers.

``GW_DATABASE_URL`` is the only configuration source.  PostgreSQL 16 is the
deployment target (``postgresql+psycopg://...``); tests may point at SQLite,
which the models support through portable SQLAlchemy types.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import load_settings


def make_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    url = database_url or load_settings().database_url
    options: dict[str, object] = {"echo": echo, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **options)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
