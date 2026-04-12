from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


# ── Models ────────────────────────────────────────────────────────


class Company(Base):
    __tablename__ = "companies"

    domain = Column(String, primary_key=True)
    company_name = Column(String)
    hq_country = Column(String)
    hq_country_code = Column(String)
    sector = Column(String)
    service_category = Column(String)
    is_seed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_companies_seed", "is_seed"),
        Index("idx_companies_category", "service_category"),
    )


class IndexConstituent(Base):
    __tablename__ = "index_constituents"

    domain = Column(String, ForeignKey("companies.domain"), primary_key=True)
    index_name = Column(String, primary_key=True)
    ticker = Column(String)
    market_cap_eur = Column(Float)
    employees = Column(Integer)
    sector = Column(String)


class Edge(Base):
    __tablename__ = "edges"

    parent_domain = Column(String, ForeignKey("companies.domain"), primary_key=True)
    child_domain = Column(String, ForeignKey("companies.domain"), primary_key=True)
    depth = Column(Integer)
    purposes = Column(Text)  # JSON text
    data_categories = Column(Text)  # JSON text
    transfer_basis = Column(String)
    confirmed = Column(Boolean, default=False)
    source = Column(String)
    discovered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_edges_child", "child_domain"),
        Index("idx_edges_depth", "depth"),
    )


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String, ForeignKey("companies.domain"))
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    source_url = Column(String)
    fetch_status = Column(String)
    error_message = Column(Text)
    sp_count = Column(Integer)

    __table_args__ = (
        Index("idx_fetch_log_domain", "domain"),
        Index("idx_fetch_log_status", "fetch_status"),
    )


class AnalyticsCache(Base):
    __tablename__ = "analytics_cache"

    key = Column(String, primary_key=True)
    value = Column(Text)  # JSON text
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Engine & session helpers ──────────────────────────────────────


def get_engine(db_path: str) -> Engine:
    """Create a SQLAlchemy engine for the given SQLite path with foreign keys enabled."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables defined on Base."""
    Base.metadata.create_all(engine)


@contextmanager
def get_session(engine: Engine) -> Generator[Session, None, None]:
    """Yield a session that commits on success and rolls back on error."""
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
