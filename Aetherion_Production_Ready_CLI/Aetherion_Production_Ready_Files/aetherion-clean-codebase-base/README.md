# Aetherion AI — CLI/TUI-First Platform (v1.0.0)

Aetherion AI is an autonomous agentic engineering platform built for command-line developers. It combines a rich terminal client (CLI + interactive TUI), a dedicated lightweight identity & device authorization portal, and a fail-closed production backend.

---

## Architecture Overview

```
├── cli/                 # Python hatchling wheel: CLI (`aetherion`) + Terminal UI (`aetherion tui`)
├── backend/             # Hardened Flask backend: JWT, RFC 8628 device auth, durable state, tasks
├── frontend-auth/       # Minimal standalone Vite + React auth & device confirmation portal
├── deploy/              # Production deployment: Multi-stage Dockerfiles, Docker Compose, Kubernetes
├── scripts/             # Production preflight, database migration, backup & restore verification
└── audit/               # Release ledger and integrity manifest
```

---

## 1. Quick Start (Local Development)

### Prerequisites
- Python 3.10+
- Node.js 18+ (for auth portal)
- Optional: Docker & Docker Compose

### 1.1 Start the Backend
```bash
cd backend
python -m pip install -r requirements.txt
python app.py
```
*The backend boots on `http://localhost:5000` in `local-development` mode with automatic fallback SQLite store.*

### 1.2 Start the Auth Portal
```bash
cd frontend-auth
npm install
npm run dev
```
*The portal runs on `http://localhost:3000`.*

### 1.3 Install and Run the CLI
```bash
cd cli
pip install -e .

# Run diagnostics
aetherion doctor

# View runtime and build version
aetherion version

# Start the interactive terminal workspace
aetherion tui
```

---

## 2. Terminal Authentication & Device Flow (RFC 8628)

Authenticate your local terminal using the browser authorization flow:
```bash
aetherion auth login
# OR
aetherion login
```
The CLI displays an 8-character user code (e.g. `ABCD-1234`) and opens the activation URL:
`http://localhost:3000/activate?code=ABCD-1234`

Once confirmed in the browser, tokens are persisted directly to your operating system's secure vault (Windows Credential Manager, macOS Keychain, or Linux Secret Service).

---

## 3. Production Deployment

### Docker Compose
```bash
cd deploy/compose
docker-compose up -d --build
```

### Kubernetes
```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/secrets.yaml
kubectl apply -f deploy/k8s/backend.yaml
kubectl apply -f deploy/k8s/frontend-auth.yaml
kubectl apply -f deploy/k8s/ingress.yaml
```

---

## 4. Production Preflight & Integrity Verification

Run the automated production preflight verification suite:
```bash
python scripts/production_preflight.py
```

Check database migration, backup, and restore integrity:
```bash
python scripts/db_migrate.py --dry-run
python scripts/db_backup.py
python scripts/db_restore_verify.py
```

---

## 5. Security Posture
In `production` and `staging` modes, Aetherion enforces a **strict fail-closed boundary**:
- SQLite fallbacks are strictly prohibited; PostgreSQL is required.
- In-process queues are prohibited; Redis multi-worker coordination is required.
- Default or weak `JWT_SECRET` and `SECRET_KEY` abort process startup immediately.
- Container/microVM runner sandboxing is required for code execution.
