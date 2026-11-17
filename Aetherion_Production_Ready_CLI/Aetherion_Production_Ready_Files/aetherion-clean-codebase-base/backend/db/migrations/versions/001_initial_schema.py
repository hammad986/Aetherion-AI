"""Initial schema — all Aetherion domains

Revision ID: 001_initial
Revises: 
Create Date: 2026-06-04

Domains created:
  sessions     — agent execution sessions
  users        — authentication
  learnings    — long-term memory
  executions   — strategy learning
  skills       — reusable recipes
  code_lessons — error/fix pairs
  workspaces   — file sandboxes
  billing_orders — Razorpay billing
  forensic_logs  — audit/crash trail
  chain_tasks    — DAG orchestration
  graph_edges    — agent comm graph
  settings       — platform KV store
  saas_platform  — provider health, blueprints
"""
from alembic import op
import sqlalchemy as sa

revision  = '001_initial'
down_revision = None
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── sessions ────────────────────────────────────────────────────────────
    op.create_table('sessions',
        sa.Column('id',            sa.String(64),  nullable=False, primary_key=True),
        sa.Column('task',          sa.Text,        nullable=True),
        sa.Column('model',         sa.String(128), nullable=True),
        sa.Column('status',        sa.String(32),  nullable=False, server_default='pending'),
        sa.Column('success',       sa.Boolean,     nullable=True),
        sa.Column('mode',          sa.String(32),  nullable=True, server_default='managed'),
        sa.Column('created_at',    sa.DateTime,    nullable=True),
        sa.Column('started_at',    sa.DateTime,    nullable=True),
        sa.Column('finished_at',   sa.DateTime,    nullable=True),
        sa.Column('config_json',   sa.Text,        nullable=True),
        sa.Column('log_tail',      sa.Text,        nullable=True),
        sa.Column('score',         sa.Float,       nullable=True),
        sa.Column('error',         sa.Text,        nullable=True),
        sa.Column('error_category',sa.String(64),  nullable=True),
        sa.Column('exit_code',     sa.Integer,     nullable=True),
        sa.Column('stage',         sa.String(64),  nullable=True),
        sa.Column('usage_json',    sa.Text,        nullable=True),
        sa.Column('files_json',    sa.Text,        nullable=True),
        sa.Column('result',        sa.Text,        nullable=True),
        sa.Column('chain_id',      sa.String(64),  nullable=True),
        sa.Column('chain_task_id', sa.String(64),  nullable=True),
        sa.Column('forked_from',   sa.String(64),  nullable=True),
        sa.Column('user_id',       sa.String(64),  nullable=True),
        sa.Column('views',         sa.Integer,     server_default='0'),
        sa.Column('likes',         sa.Integer,     server_default='0'),
        sa.Column('is_flagged',    sa.Boolean,     server_default='0'),
        sa.Column('is_public',     sa.Boolean,     server_default='0'),
    )
    op.create_index('ix_sessions_status',     'sessions', ['status'])
    op.create_index('ix_sessions_user_id',    'sessions', ['user_id'])
    op.create_index('ix_sessions_created_at', 'sessions', ['created_at'])

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table('users',
        sa.Column('id',            sa.String(64),  nullable=False, primary_key=True),
        sa.Column('email',         sa.String(255), nullable=False, unique=True),
        sa.Column('username',      sa.String(128), nullable=True,  unique=True),
        sa.Column('password_hash', sa.String(256), nullable=True),
        sa.Column('role',          sa.String(32),  server_default='user'),
        sa.Column('is_active',     sa.Boolean,     server_default='1'),
        sa.Column('strikes',       sa.Integer,     server_default='0'),
        sa.Column('is_banned',     sa.Boolean,     server_default='0'),
        sa.Column('created_at',    sa.DateTime,    nullable=True),
        sa.Column('last_login',    sa.DateTime,    nullable=True),
        sa.Column('profile_json',  sa.Text,        nullable=True),
    )
    op.create_index('ix_users_email', 'users', ['email'])

    # ── memory: learnings ────────────────────────────────────────────────────
    op.create_table('learnings',
        sa.Column('id',         sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('category',   sa.String(64), nullable=False),
        sa.Column('insight',    sa.Text,       nullable=False),
        sa.Column('created_at', sa.String(32), nullable=False),
    )
    op.create_index('ix_learnings_category', 'learnings', ['category'])

    # ── memory: executions ───────────────────────────────────────────────────
    op.create_table('executions',
        sa.Column('id',              sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('task',            sa.Text,       nullable=True),
        sa.Column('subtask',         sa.Text,       nullable=True),
        sa.Column('strategy',        sa.String(32), nullable=True),
        sa.Column('success',         sa.Boolean,    nullable=False, server_default='0'),
        sa.Column('error_type',      sa.String(64), nullable=True),
        sa.Column('fix_applied',     sa.Text,       nullable=True),
        sa.Column('elapsed_seconds', sa.Float,      nullable=True, server_default='0'),
        sa.Column('created_at',      sa.String(32), nullable=False),
    )
    op.create_index('ix_executions_strategy', 'executions', ['strategy'])

    # ── memory: skills ───────────────────────────────────────────────────────
    op.create_table('skills',
        sa.Column('id',              sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('name',            sa.String(120),nullable=False),
        sa.Column('task_pattern',    sa.String(256),nullable=False, unique=True),
        sa.Column('concept_pattern', sa.String(256),nullable=True),
        sa.Column('steps_json',      sa.Text,       nullable=True),
        sa.Column('step_count',      sa.Integer,    server_default='0'),
        sa.Column('best_strategy',   sa.String(32), nullable=True),
        sa.Column('success_count',   sa.Integer,    server_default='0'),
        sa.Column('failure_count',   sa.Integer,    server_default='0'),
        sa.Column('success_rate',    sa.Float,      server_default='0'),
        sa.Column('avg_elapsed',     sa.Float,      server_default='0'),
        sa.Column('last_used',       sa.String(32), nullable=True),
        sa.Column('created_at',      sa.String(32), nullable=True),
        sa.Column('strategy_stats',  sa.Text,       nullable=True),
    )

    # ── memory: code_lessons ─────────────────────────────────────────────────
    op.create_table('code_lessons',
        sa.Column('id',            sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('error_type',    sa.String(64), nullable=False),
        sa.Column('error_message', sa.Text,       nullable=True),
        sa.Column('file',          sa.String(512),nullable=True),
        sa.Column('fix_applied',   sa.Text,       nullable=True),
        sa.Column('success',       sa.Boolean,    server_default='0'),
        sa.Column('context',       sa.Text,       nullable=True),
        sa.Column('created_at',    sa.String(32), nullable=False),
        sa.Column('updated_at',    sa.String(32), nullable=False),
    )
    op.create_index('ix_code_lessons_error_type', 'code_lessons', ['error_type'])

    # ── workspaces ───────────────────────────────────────────────────────────
    op.create_table('workspaces',
        sa.Column('id',            sa.String(64), primary_key=True),
        sa.Column('session_id',    sa.String(64), nullable=True),
        sa.Column('path',          sa.Text,       nullable=False),
        sa.Column('created_at',    sa.DateTime,   nullable=True),
        sa.Column('last_accessed', sa.DateTime,   nullable=True),
        sa.Column('size_bytes',    sa.BigInteger, server_default='0'),
        sa.Column('files_json',    sa.Text,       nullable=True),
    )

    # ── billing ──────────────────────────────────────────────────────────────
    op.create_table('billing_orders',
        sa.Column('id',            sa.String(64), primary_key=True),
        sa.Column('user_id',       sa.String(64), nullable=True),
        sa.Column('amount_cents',  sa.Integer,    nullable=False),
        sa.Column('currency',      sa.String(8),  server_default='INR'),
        sa.Column('status',        sa.String(32), server_default='created'),
        sa.Column('plan',          sa.String(64), nullable=True),
        sa.Column('razorpay_json', sa.Text,       nullable=True),
        sa.Column('created_at',    sa.DateTime,   nullable=True),
        sa.Column('completed_at',  sa.DateTime,   nullable=True),
    )

    # ── forensics ────────────────────────────────────────────────────────────
    op.create_table('forensic_logs',
        sa.Column('id',         sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.String(64), nullable=True),
        sa.Column('event_type', sa.String(64), nullable=False),
        sa.Column('payload',    sa.Text,       nullable=True),
        sa.Column('severity',   sa.String(16), server_default='info'),
        sa.Column('created_at', sa.DateTime,   nullable=True),
    )
    op.create_index('ix_forensic_logs_session',    'forensic_logs', ['session_id'])
    op.create_index('ix_forensic_logs_event_type', 'forensic_logs', ['event_type'])

    # ── cognition: chain_tasks ───────────────────────────────────────────────
    op.create_table('chain_tasks',
        sa.Column('id',         sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('chain_id',   sa.Integer,    nullable=False),
        sa.Column('step_index', sa.Integer,    nullable=False),
        sa.Column('task',       sa.Text,       nullable=False),
        sa.Column('status',     sa.String(32), server_default='pending'),
        sa.Column('result',     sa.Text,       nullable=True),
        sa.Column('error',      sa.Text,       nullable=True),
        sa.Column('created_at', sa.String(32), nullable=True),
        sa.Column('updated_at', sa.String(32), nullable=True),
    )
    op.create_index('ix_chain_tasks_chain_id', 'chain_tasks', ['chain_id'])

    # ── graph ────────────────────────────────────────────────────────────────
    op.create_table('graph_edges',
        sa.Column('id',         sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('source',     sa.String(64), nullable=False),
        sa.Column('target',     sa.String(64), nullable=False),
        sa.Column('edge_type',  sa.String(32), nullable=True),
        sa.Column('weight',     sa.Float,      server_default='1.0'),
        sa.Column('payload',    sa.Text,       nullable=True),
        sa.Column('created_at', sa.DateTime,   nullable=True),
    )
    op.create_index('ix_graph_edges_source', 'graph_edges', ['source'])
    op.create_index('ix_graph_edges_target', 'graph_edges', ['target'])

    # ── settings ─────────────────────────────────────────────────────────────
    op.create_table('settings',
        sa.Column('key',   sa.String(256), primary_key=True),
        sa.Column('value', sa.Text,        nullable=True),
    )

    # ── saas: provider_health ────────────────────────────────────────────────
    op.create_table('provider_health',
        sa.Column('id',                sa.Integer,    primary_key=True, autoincrement=True),
        sa.Column('provider',          sa.String(64), nullable=False),
        sa.Column('model',             sa.String(128),nullable=True),
        sa.Column('latency_ms',        sa.Integer,    nullable=True),
        sa.Column('is_error',          sa.Boolean,    server_default='0'),
        sa.Column('hallucination_count',sa.Integer,   server_default='0'),
        sa.Column('confidence_score',  sa.Float,      nullable=True),
        sa.Column('consensus_match',   sa.Boolean,    server_default='0'),
        sa.Column('created_at',        sa.DateTime,   nullable=True),
    )
    op.create_index('ix_provider_health_provider', 'provider_health', ['provider'])

    # ── saas: blueprints ─────────────────────────────────────────────────────
    op.create_table('blueprints',
        sa.Column('id',           sa.String(64), primary_key=True),
        sa.Column('original_sid', sa.String(64), nullable=True),
        sa.Column('author_id',    sa.String(64), nullable=True),
        sa.Column('task',         sa.Text,       nullable=True),
        sa.Column('category',     sa.String(64), nullable=True),
        sa.Column('installs',     sa.Integer,    server_default='0'),
        sa.Column('likes',        sa.Integer,    server_default='0'),
        sa.Column('created_at',   sa.DateTime,   nullable=True),
    )

    # ── saas: causal_telemetry ────────────────────────────────────────────────
    op.create_table('causal_telemetry',
        sa.Column('id',               sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('session_id',       sa.String(64), nullable=True),
        sa.Column('drift_detected',   sa.Boolean, server_default='0'),
        sa.Column('recovery_success', sa.Boolean, server_default='0'),
        sa.Column('created_at',       sa.DateTime, nullable=True),
    )

    # ── saas: security_state ─────────────────────────────────────────────────
    op.create_table('security_state',
        sa.Column('id',                 sa.Integer, primary_key=True),
        sa.Column('kill_switch_active', sa.Integer, server_default='0'),
    )


def downgrade() -> None:
    tables = [
        'security_state', 'causal_telemetry', 'blueprints', 'provider_health',
        'settings', 'graph_edges', 'chain_tasks', 'forensic_logs', 'billing_orders',
        'workspaces', 'code_lessons', 'skills', 'executions', 'learnings',
        'users', 'sessions',
    ]
    for t in tables:
        op.drop_table(t)
