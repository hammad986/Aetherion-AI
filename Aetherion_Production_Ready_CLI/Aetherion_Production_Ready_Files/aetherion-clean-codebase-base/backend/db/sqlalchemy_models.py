"""
db/sqlalchemy_models.py — SQLAlchemy ORM Models
================================================
Declarative models for all Aetherion domains.
Used by Alembic migrations and the PostgreSQL store.

Domains:
  sessions     — agent execution sessions
  memory       — learnings, skills, reflections, executions
  workspaces   — file workspaces per session
  users        — user accounts and auth
  billing      — Razorpay orders and invoices
  forensics    — audit trail and crash records
  cognition    — chain tasks and DAG orchestration
  graph        — agent communication graph edges
"""
from datetime import datetime, timezone
from typing import Optional


def _utc_now():
    """Callable returning timezone-aware UTC datetime for SQLAlchemy column defaults."""
    return datetime.now(timezone.utc)

try:
    from sqlalchemy import (
        BigInteger, Boolean, Column, DateTime, Float, ForeignKey,
        Integer, JSON, String, Text, UniqueConstraint, Index,
        create_engine, text,
    )
    from sqlalchemy.orm import DeclarativeBase, relationship
    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False
    # Stub so the file is importable even without sqlalchemy installed
    class DeclarativeBase:  # type: ignore
        pass

import logging
logger = logging.getLogger("aetherion.db.models")


if _SA_AVAILABLE:
    class Base(DeclarativeBase):
        pass

    # ── Sessions ─────────────────────────────────────────────────────────────

    class Session(Base):
        __tablename__ = "sessions"
        id            = Column(String(64),  primary_key=True)
        task          = Column(Text,        nullable=True)
        model         = Column(String(128), nullable=True)
        status        = Column(String(32),  nullable=False, default="pending")
        success       = Column(Boolean,     nullable=True)
        mode          = Column(String(32),  nullable=True, default="managed")
        created_at    = Column(DateTime,    default=_utc_now)
        started_at    = Column(DateTime,    nullable=True)
        finished_at   = Column(DateTime,    nullable=True)
        config_json   = Column(JSON,        nullable=True)
        log_tail      = Column(Text,        nullable=True)
        score         = Column(Float,       nullable=True)
        error         = Column(Text,        nullable=True)
        error_category= Column(String(64),  nullable=True)
        exit_code     = Column(Integer,     nullable=True)
        stage         = Column(String(64),  nullable=True)
        usage_json    = Column(JSON,        nullable=True)
        files_json    = Column(JSON,        nullable=True)
        result        = Column(Text,        nullable=True)
        chain_id      = Column(String(64),  nullable=True)
        chain_task_id = Column(String(64),  nullable=True)
        forked_from   = Column(String(64),  nullable=True)
        user_id       = Column(String(64),  nullable=True)
        views         = Column(Integer,     default=0)
        likes         = Column(Integer,     default=0)
        is_flagged    = Column(Boolean,     default=False)
        __table_args__ = (
            Index("ix_sessions_status",     "status"),
            Index("ix_sessions_user_id",    "user_id"),
            Index("ix_sessions_created_at", "created_at"),
        )

    # ── Users ─────────────────────────────────────────────────────────────────

    class User(Base):
        __tablename__ = "users"
        id            = Column(String(64),  primary_key=True)
        email         = Column(String(255), unique=True, nullable=False)
        username      = Column(String(128), unique=True, nullable=True)
        password_hash = Column(String(256), nullable=True)
        role          = Column(String(32),  default="user")
        is_active     = Column(Boolean,     default=True)
        created_at    = Column(DateTime,    default=_utc_now)
        last_login    = Column(DateTime,    nullable=True)
        profile_json  = Column(JSON,        nullable=True)
        __table_args__ = (Index("ix_users_email", "email"),)

    # ── API Keys ──────────────────────────────────────────────────────────────

    class ApiKey(Base):
        __tablename__ = "api_keys"
        id            = Column(String(64),  primary_key=True)
        user_id       = Column(Integer,     nullable=False)
        name          = Column(String(128), nullable=False)
        key_hash      = Column(String(128), unique=True, nullable=False)
        prefix        = Column(String(32),  nullable=False)
        created_at    = Column(DateTime,    default=_utc_now)
        revoked_at    = Column(Float,       nullable=True)
        last_used_at  = Column(Float,       nullable=True)
        __table_args__ = (
            Index("ix_api_keys_hash",    "key_hash"),
            Index("ix_api_keys_user_id", "user_id"),
        )

    # ── Memory ────────────────────────────────────────────────────────────────

    class Learning(Base):
        __tablename__ = "learnings"
        id         = Column(Integer,    primary_key=True, autoincrement=True)
        category   = Column(String(64), nullable=False)
        insight    = Column(Text,       nullable=False)
        created_at = Column(String(32), nullable=False)
        __table_args__ = (Index("ix_learnings_category", "category"),)

    class Execution(Base):
        __tablename__ = "executions"
        id               = Column(Integer, primary_key=True, autoincrement=True)
        task             = Column(Text,       nullable=True)
        subtask          = Column(Text,       nullable=True)
        strategy         = Column(String(32), nullable=True)
        success          = Column(Boolean,    nullable=False, default=False)
        error_type       = Column(String(64), nullable=True)
        fix_applied      = Column(Text,       nullable=True)
        elapsed_seconds  = Column(Float,      nullable=True, default=0.0)
        created_at       = Column(String(32), nullable=False)
        __table_args__ = (Index("ix_executions_strategy", "strategy"),)

    class Skill(Base):
        __tablename__    = "skills"
        id               = Column(Integer,    primary_key=True, autoincrement=True)
        name             = Column(String(120), nullable=False)
        task_pattern     = Column(String(256), nullable=False, unique=True)
        concept_pattern  = Column(String(256), nullable=True)
        steps_json       = Column(Text,        nullable=True)
        step_count       = Column(Integer,     default=0)
        best_strategy    = Column(String(32),  nullable=True)
        success_count    = Column(Integer,     default=0)
        failure_count    = Column(Integer,     default=0)
        success_rate     = Column(Float,       default=0.0)
        avg_elapsed      = Column(Float,       default=0.0)
        last_used        = Column(String(32),  nullable=True)
        created_at       = Column(String(32),  nullable=True)
        strategy_stats   = Column(JSON,        nullable=True)

    class CodeLesson(Base):
        __tablename__ = "code_lessons"
        id            = Column(Integer,    primary_key=True, autoincrement=True)
        error_type    = Column(String(64), nullable=False)
        error_message = Column(Text,       nullable=True)
        file          = Column(String(512),nullable=True)
        fix_applied   = Column(Text,       nullable=True)
        success       = Column(Boolean,    default=False)
        context       = Column(Text,       nullable=True)
        created_at    = Column(String(32), nullable=False)
        updated_at    = Column(String(32), nullable=False)
        __table_args__ = (Index("ix_code_lessons_error_type", "error_type"),)

    # ── Workspaces ────────────────────────────────────────────────────────────

    class Workspace(Base):
        __tablename__ = "workspaces"
        id            = Column(String(64), primary_key=True)
        session_id    = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"))
        path          = Column(Text,       nullable=False)
        created_at    = Column(DateTime,   default=_utc_now)
        last_accessed = Column(DateTime,   nullable=True)
        size_bytes    = Column(BigInteger,  default=0)
        files_json    = Column(JSON,        nullable=True)

    # ── Billing ───────────────────────────────────────────────────────────────

    class BillingOrder(Base):
        __tablename__ = "billing_orders"
        id              = Column(String(64), primary_key=True)   # Razorpay order_id
        user_id         = Column(String(64), nullable=True)
        amount_cents    = Column(Integer,    nullable=False)
        currency        = Column(String(8),  default="INR")
        status          = Column(String(32), default="created")
        plan            = Column(String(64), nullable=True)
        razorpay_json   = Column(JSON,       nullable=True)
        created_at      = Column(DateTime,   default=_utc_now)
        completed_at    = Column(DateTime,   nullable=True)

    # ── Forensics ─────────────────────────────────────────────────────────────

    class ForensicLog(Base):
        __tablename__ = "forensic_logs"
        id          = Column(Integer,    primary_key=True, autoincrement=True)
        session_id  = Column(String(64), nullable=True)
        event_type  = Column(String(64), nullable=False)
        payload     = Column(JSON,       nullable=True)
        severity    = Column(String(16), default="info")
        created_at  = Column(DateTime,   default=_utc_now)
        __table_args__ = (
            Index("ix_forensic_logs_session",    "session_id"),
            Index("ix_forensic_logs_event_type", "event_type"),
        )

    # ── Cognition (Chain Tasks / DAG) ─────────────────────────────────────────

    class ChainTask(Base):
        __tablename__ = "chain_tasks"
        id          = Column(Integer,     primary_key=True, autoincrement=True)
        chain_id    = Column(Integer,     nullable=False)
        step_index  = Column(Integer,     nullable=False)
        task        = Column(Text,        nullable=False)
        status      = Column(String(32),  default="pending")
        result      = Column(Text,        nullable=True)
        error       = Column(Text,        nullable=True)
        created_at  = Column(String(32),  nullable=True)
        updated_at  = Column(String(32),  nullable=True)
        __table_args__ = (Index("ix_chain_tasks_chain_id", "chain_id"),)

    # ── Graph (Agent Communication) ───────────────────────────────────────────

    class GraphEdge(Base):
        __tablename__ = "graph_edges"
        id           = Column(Integer,    primary_key=True, autoincrement=True)
        source       = Column(String(64), nullable=False)
        target       = Column(String(64), nullable=False)
        edge_type    = Column(String(32), nullable=True)
        weight       = Column(Float,      default=1.0)
        payload      = Column(JSON,       nullable=True)
        created_at   = Column(DateTime,   default=_utc_now)
        __table_args__ = (
            Index("ix_graph_edges_source", "source"),
            Index("ix_graph_edges_target", "target"),
        )

else:
    logger.warning("[Models] sqlalchemy not installed — ORM models unavailable. pip install sqlalchemy")
    Base = object  # type: ignore
