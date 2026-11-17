"""
scripts/setup_hard_repo.py — Creates a realistic multi-tier service in a temporary git repo.
"""
import os
import subprocess
from pathlib import Path

def main():
    repo_dir = Path(r"C:\Users\mdham\Downloads\Aetherion-AI-main\temp_hard_repo")
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "tests").mkdir(exist_ok=True)

    # 1. config.py
    (repo_dir / "config.py").write_text('''"""
config.py — Service and Logging Configuration.
"""
from dataclasses import dataclass, field
import os

@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(message)s"
    include_timestamp: bool = True

@dataclass
class AppConfig:
    service_name: str = "user-service"
    environment: str = "production"
    debug: bool = False
    secret_key: str = "sec_prod_live_key_do_not_leak_9944"
    api_token: str = "tok_internal_svc_token_secret_1122"
    logging: LoggingConfig = field(default_factory=LoggingConfig)
''', encoding="utf-8")

    # 2. repository.py
    (repo_dir / "repository.py").write_text('''"""
repository.py — Data access layer for users.
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("user_service.repository")

class UserRepository:
    def __init__(self):
        self._storage: Dict[str, Dict[str, Any]] = {}
        self._counter: int = 1

    def create(self, name: str, email: str, role: str) -> Dict[str, Any]:
        user_id = f"usr_{self._counter:04d}"
        self._counter += 1
        record = {"id": user_id, "name": name, "email": email, "role": role}
        self._storage[user_id] = record
        logger.info(f"User created in repository: {user_id}")
        return dict(record)

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        record = self._storage.get(user_id)
        if record:
            logger.info(f"User retrieved: {user_id}")
            return dict(record)
        logger.warning(f"User not found: {user_id}")
        return None

    def list_all(self) -> List[Dict[str, Any]]:
        logger.info(f"Listing all users (total={len(self._storage)})")
        return [dict(u) for u in self._storage.values()]
''', encoding="utf-8")

    # 3. service.py
    (repo_dir / "service.py").write_text('''"""
service.py — Business logic layer for users.
"""
import logging
from typing import Dict, Any, List, Optional
from repository import UserRepository

logger = logging.getLogger("user_service.service")

ALLOWED_ROLES = {"admin", "member", "guest"}

class UserService:
    def __init__(self, repo: Optional[UserRepository] = None):
        self.repo = repo or UserRepository()

    def register_user(self, name: str, email: str, role: str = "member") -> Dict[str, Any]:
        logger.info(f"Registering user with email {email}")
        if not name or not name.strip():
            logger.error("Registration failed: empty name")
            raise ValueError("Name cannot be empty")
        if not email or "@" not in email:
            logger.error("Registration failed: invalid email format")
            raise ValueError("Invalid email format")
        if role not in ALLOWED_ROLES:
            logger.error(f"Registration failed: forbidden role {role}")
            raise ValueError(f"Role must be one of {ALLOWED_ROLES}")
        
        user = self.repo.create(name=name.strip(), email=email.strip().lower(), role=role)
        logger.info(f"User registered successfully: {user['id']}")
        return user

    def fetch_user(self, user_id: str) -> Dict[str, Any]:
        logger.info(f"Fetching user profile {user_id}")
        user = self.repo.get_by_id(user_id)
        if not user:
            logger.error(f"User fetch failed: {user_id} not found")
            raise KeyError(f"User {user_id} not found")
        return user

    def list_users(self) -> List[Dict[str, Any]]:
        return self.repo.list_all()
''', encoding="utf-8")

    # 4. api.py
    (repo_dir / "api.py").write_text('''"""
api.py — API Controller and HTTP Dispatcher.
"""
import logging
from typing import Dict, Any, Tuple
from service import UserService

logger = logging.getLogger("user_service.api")

class ApiRouter:
    def __init__(self, service: UserService):
        self.service = service

    def dispatch(self, method: str, path: str, body: Dict[str, Any] = None, headers: Dict[str, str] = None) -> Tuple[int, Dict[str, Any]]:
        headers = headers or {}
        body = body or {}
        logger.info(f"Incoming {method} {path}")

        if path == "/health":
            return 200, {"status": "healthy", "service": "user-service"}

        if path == "/users" and method == "POST":
            try:
                user = self.service.register_user(
                    name=body.get("name", ""),
                    email=body.get("email", ""),
                    role=body.get("role", "member")
                )
                return 201, {"ok": True, "user": user}
            except ValueError as e:
                return 400, {"ok": False, "error": str(e)}

        if path.startswith("/users/") and method == "GET":
            parts = path.split("/")
            if len(parts) >= 3:
                user_id = parts[2]
                try:
                    user = self.service.fetch_user(user_id)
                    return 200, {"ok": True, "user": user}
                except KeyError as e:
                    return 404, {"ok": False, "error": str(e)}

        if path == "/users" and method == "GET":
            users = self.service.list_users()
            return 200, {"ok": True, "users": users}

        return 404, {"ok": False, "error": f"Route not found: {method} {path}"}
''', encoding="utf-8")

    # 5. tests/test_service.py
    (repo_dir / "tests" / "test_service.py").write_text('''"""
Unit tests for UserService and UserRepository.
"""
import pytest
from repository import UserRepository
from service import UserService

def test_user_registration_success():
    svc = UserService()
    user = svc.register_user("Alice Cooper", "alice@example.com", "admin")
    assert user["id"].startswith("usr_")
    assert user["name"] == "Alice Cooper"
    assert user["role"] == "admin"

def test_user_registration_invalid_email():
    svc = UserService()
    with pytest.raises(ValueError, match="Invalid email"):
        svc.register_user("Bob", "not-an-email")

def test_user_fetch_not_found():
    svc = UserService()
    with pytest.raises(KeyError):
        svc.fetch_user("usr_9999")
''', encoding="utf-8")

    # 6. tests/test_api.py
    (repo_dir / "tests" / "test_api.py").write_text('''"""
Integration tests for API dispatcher.
"""
from service import UserService
from api import ApiRouter

def test_api_health():
    router = ApiRouter(UserService())
    code, res = router.dispatch("GET", "/health")
    assert code == 200
    assert res["status"] == "healthy"

def test_api_create_and_fetch_user():
    router = ApiRouter(UserService())
    code, res = router.dispatch("POST", "/users", body={"name": "Charlie", "email": "charlie@example.com"})
    assert code == 201
    user_id = res["user"]["id"]

    code, res_get = router.dispatch("GET", f"/users/{user_id}")
    assert code == 200
    assert res_get["user"]["name"] == "Charlie"
''', encoding="utf-8")

    # 7. README.md
    (repo_dir / "README.md").write_text('''# User Management Service
A multi-tier Python service with API, service, and repository layers.
''', encoding="utf-8")

    # Git setup
    subprocess.run(["git", "init"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Aetherion Test Bot"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "bot@aetherion.ai"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit of multi-tier user service"], cwd=repo_dir, check=True)
    print("Successfully initialized git repository at:", repo_dir)

if __name__ == "__main__":
    main()
