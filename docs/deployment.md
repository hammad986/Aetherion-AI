# Nexora AI: Deployment & Operations Guide

## Overview
Nexora AI is designed as a deterministic, event-sourced orchestration runtime. It deliberately avoids premature distributed infrastructure (no Kafka, no Redis requirement for basic scale) to maximize operational resilience and maintainability.

## Deployment Profiles
Nexora operates under distinct Profiles, configured via the `NEXORA_DEPLOYMENT_PROFILE` environment variable:

1. **`local_dev`**: Disables sandboxing. Unrestricted providers. High concurrency limits.
2. **`workstation`**: Single-user deployment. Enables basic sandboxing.
3. **`managed_vps`**: Low resource ceilings. Enforces HITL (Human-in-the-Loop) pauses for critical actions.
4. **`enterprise`**: Strict isolation. Subprocess limit bounded tightly. Restricted to VPC/Enterprise LLM endpoints.

## Docker Deployment

### Bootstrapping
```bash
docker-compose up -d --build
```

### Health Probes
The container exposes `/api/v2/health` and implements an internal `deploy/healthcheck.py` probe. Ensure your load balancer or K8s ingress points to this.

## Storage & Backup
Nexora uses an append-only SQLite Event Log (`data/execution_store.db`).
*   **Backup**: Since the database uses WAL mode, use `.backup` or a file-level copy.
*   **Recovery**: Restoring the DB file instantly recovers all Replay Timelines and DAG ancestries.

## Operator Controls (HITL)
Operators should use the DevTools UI (Control Center) to:
1. **Quarantine**: Halt runaway tasks.
2. **Policy Override**: Adjust Token limits dynamically.
3. **Audit Export**: Export deterministic Replay timelines for compliance auditing.

## Security Governance
*   Never expose the container to the public internet without the accompanying frontend authentication layer.
*   Credentials injected via `ExecutionSecretManager` are automatically masked in standard logs but actively used in the isolated worker threads.
