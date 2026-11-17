# AETHERION PRODUCTION DEPLOYMENT CHECKLIST & OPERATIONAL MANUAL

This authoritative checklist governs the production release of the Aetherion AI Platform and its autonomous worker fleet. All steps must be verified prior to routing live user traffic.

---

## 1. PRE-DEPLOYMENT ENVIRONMENT HARDENING

### A. Cryptographic Secret Generation
Do not use human-typed passwords or shared keys. Generate independent 256-bit hexadecimal secrets:
```bash
# Generate SECRET_KEY
python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"

# Generate JWT_SECRET
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))"

# Generate Database Password
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_hex(24))"
```
* **Security Guard:** `SECRET_KEY` and `JWT_SECRET` must be completely independent values. Preflight checks will reject startup if they match or if either is under 32 characters.

### B. Configure `deploy/production.env`
Copy `deploy/production.env.example` to `deploy/production.env` on the host:
```bash
cp deploy/production.env.example deploy/production.env
chmod 600 deploy/production.env
```
Ensure the following variables are strictly set:
* `ENVIRONMENT=production`
* `SERVER_URL=https://api.yourdomain.com` (Must use `https://`; `http://` or `localhost` will trigger a critical preflight failure)
* `DATABASE_URL=postgresql://user:pass@host:5432/aetherion` (SQLite is blocked in production)
* `REDIS_URL=redis://host:6379/0`
* `REQUIRE_SANDBOX=true` (Enforces container sandboxing for untrusted plugins)

> [!IMPORTANT]
> **PostgreSQL Role Password Rotation Semantics:** Changing `POSTGRES_PASSWORD` in `.env` or Compose configuration does **not** automatically rotate the password of a database role in an already-initialized PostgreSQL persistent volume (`postgres_data`). When rotating credentials on an existing database, an operator must execute an explicit SQL rotation (`ALTER ROLE aetherion_user WITH PASSWORD 'new_password';`) against the running database cluster in addition to updating the environment configuration. Never commit populated `.env` files to source control.

---

## 2. HOST CONTAINER ENGINE PROVISIONING (FOR UNTRUSTED PLUGINS)

When untrusted third-party plugins are executed, Aetherion enforces container isolation:

1. **Verify Docker Engine Daemon is Running on Host:**
   ```bash
   docker info
   docker ps
   ```
2. **Build or Pull the Sandbox Runner Image:**
   ```bash
   docker build -t aetherion-sandbox-runner:latest -f deploy/docker/Dockerfile.worker .
   ```
3. **Verify Fail-Closed Protection:**
   If Docker is unreachable, untrusted plugin calls fail closed with `SANDBOX_UNAVAILABLE` and emit a security event. Uncontained host execution is strictly impossible.

---

## 3. THIRD-PARTY OAUTH CONFIGURATION

### A. Google Cloud Console
1. Navigate to **Google Cloud Console** > **APIs & Services** > **Credentials**.
2. Create or configure an **OAuth 2.0 Client ID** (Web application).
3. Under **Authorized redirect URIs**, add the exact deterministic callback URL:
   ```
   https://api.yourdomain.com/api/auth/google/callback
   ```
4. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `deploy/production.env`.

### B. GitHub Developer Settings
1. Navigate to **GitHub** > **Settings** > **Developer settings** > **OAuth Apps**.
2. Create a new OAuth App:
   * **Homepage URL:** `https://app.yourdomain.com`
   * **Authorization callback URL:**
     ```
     https://api.yourdomain.com/api/auth/github/callback
     ```
3. Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` in `deploy/production.env`.

---

## 4. PREFLIGHT HEALTH VERIFICATION

Execute the authoritative preflight validation script before starting the server:
```bash
python -c "from backend.core.preflight import run_preflight; r = run_preflight(probe_live=True); print('Preflight Ready:', r.ready); assert r.ready, [f.detail for f in r.checks if not f.ok]"
```
* If any critical failure is reported (e.g. database unreachable, weak secret, missing `SERVER_URL`), the script will exit with an error. **Do not bypass preflight failures.**

---

## 5. PRODUCTION CONTAINER STACK LAUNCH

Launch the full stack using Docker Compose:
```bash
docker compose -f deploy/compose/docker-compose.prod.yml --env-file deploy/production.env up -d --build
```
Verify container health:
```bash
docker compose -f deploy/compose/docker-compose.prod.yml ps
```

---

## 6. POST-DEPLOYMENT SMOKE & SANITY VERIFICATION

1. **Health Endpoint:**
   ```bash
   curl -fsSL https://api.yourdomain.com/api/health
   # Expected response: {"ok": true, "status": "ok", "service": "aetherion-backend", ...}
   ```
2. **Interactive TUI / CLI Connectivity:**
   ```bash
   aetherion config set server_url https://api.yourdomain.com
   aetherion doctor
   ```
3. **Authentication Verification:**
   ```bash
   aetherion login
   aetherion whoami
   aetherion logout
   ```
4. **Zero-Secret Logging Audit:**
   Inspect container logs:
   ```bash
   docker logs aetherion-backend 2>&1 | grep -iE "(sk-|token|jwt_secret|password)"
   # Output must be completely clean of plaintext secrets.
   ```
