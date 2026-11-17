"""
backend/execution/extensions/policy.py — Extension trust levels, resource policies, and sandboxing rules.
"""
from __future__ import annotations

import dataclasses
import enum
from typing import Any, Dict, List, Optional

from .models import ExtensionTrustLevel, PermissionEnum


@dataclasses.dataclass
class ExtensionSecurityPolicy:
    """Resource constraints and runtime boundaries for isolated extension execution."""
    mem_limit: str = "256m"              # 256MB RAM limit
    nano_cpus: int = 1_000_000_000       # 1.0 CPU quota
    network_mode: str = "none"           # Isolated network stack (no outbound or inbound)
    pids_limit: int = 64                 # Anti-forkbomb process limit
    default_timeout_seconds: float = 30.0
    max_timeout_seconds: float = 60.0
    allow_workspace_write: bool = False
    scrubbed_env: Dict[str, str] = dataclasses.field(default_factory=lambda: {
        "PYTHONPATH": "/plugin",
        "PYTHONUNBUFFERED": "1",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
    })

    @classmethod
    def from_permissions(
        cls,
        permissions: List[str],
        trust_level: str = ExtensionTrustLevel.UNTRUSTED.value,
        timeout_seconds: Optional[float] = None,
    ) -> ExtensionSecurityPolicy:
        perms = set(permissions)
        allow_network = PermissionEnum.NETWORK_ACCESS.value in perms
        allow_write = PermissionEnum.FILESYSTEM_WRITE.value in perms

        # Network is disabled ("none") unless explicitly granted AND supported
        network_mode = "bridge" if allow_network else "none"

        chosen_timeout = timeout_seconds if timeout_seconds is not None else 30.0
        chosen_timeout = min(max(1.0, chosen_timeout), 60.0)

        return cls(
            mem_limit="256m",
            nano_cpus=1_000_000_000,
            network_mode=network_mode,
            pids_limit=64,
            default_timeout_seconds=chosen_timeout,
            allow_workspace_write=allow_write,
        )
