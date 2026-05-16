import os
from dataclasses import dataclass
from typing import List

@dataclass
class DeploymentProfile:
    name: str
    max_tokens_per_task: int
    max_subprocesses: int
    sandbox_enabled: bool
    allowed_providers: List[str]
    enforce_hitl: bool

class Profiles:
    LOCAL_DEV = DeploymentProfile(
        name="local_dev",
        max_tokens_per_task=100000,
        max_subprocesses=10,
        sandbox_enabled=False,
        allowed_providers=["openai", "anthropic", "ollama", "mock"],
        enforce_hitl=False
    )
    
    WORKSTATION = DeploymentProfile(
        name="workstation",
        max_tokens_per_task=50000,
        max_subprocesses=5,
        sandbox_enabled=True,
        allowed_providers=["openai", "anthropic", "ollama"],
        enforce_hitl=False
    )
    
    MANAGED_VPS = DeploymentProfile(
        name="managed_vps",
        max_tokens_per_task=20000,
        max_subprocesses=3,
        sandbox_enabled=True,
        allowed_providers=["openai", "anthropic"],
        enforce_hitl=True
    )
    
    ENTERPRISE = DeploymentProfile(
        name="enterprise",
        max_tokens_per_task=10000,
        max_subprocesses=1,
        sandbox_enabled=True,
        allowed_providers=["azure_openai", "anthropic_vpc"],
        enforce_hitl=True
    )

def get_active_profile() -> DeploymentProfile:
    """Returns the active deployment profile based on the environment."""
    profile_name = os.getenv("NEXORA_DEPLOYMENT_PROFILE", "workstation").upper()
    return getattr(Profiles, profile_name, Profiles.WORKSTATION)
