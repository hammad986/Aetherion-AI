# Aetherion DB Migrations

Managed by Alembic.

## Setup

```bash
pip install alembic sqlalchemy psycopg2-binary
export DATABASE_URL=postgresql://user:pass@localhost:5432/aetherion
alembic upgrade head
```

## Create a new migration

```bash
alembic revision --autogenerate -m "add_new_column"
alembic upgrade head
```

## Domain map

| Table | Domain |
|---|---|
| sessions | Execution sessions |
| users | Authentication |
| learnings | Long-term memory |
| executions | Strategy learning |
| skills | Reusable recipes |
| code_lessons | Error/fix pairs |
| workspaces | File sandboxes |
| billing_orders | Razorpay billing |
| forensic_logs | Audit/crash trail |
| chain_tasks | DAG orchestration |
| graph_edges | Agent comm graph |
