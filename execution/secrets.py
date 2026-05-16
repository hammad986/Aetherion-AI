import os
import logging
from typing import Dict, Optional

logger = logging.getLogger("nexora.secrets")

class SecretAccessViolation(Exception):
    pass

class ExecutionSecretManager:
    """
    Handles secure runtime injection of provider credentials and scoped API keys.
    Audits credential access and enforces rotation awareness without exposing 
    keys directly to the orchestration graph.
    """
    def __init__(self):
        # Maps logical key names to env vars or encrypted store references
        self._provider_key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY"
        }

    def inject_provider_credentials(self, provider_name: str, execution_id: str) -> str:
        """
        Retrieves the requested provider credential specifically for the given execution context.
        Generates an audit trail event.
        """
        if provider_name not in self._provider_key_map:
            logger.warning(f"[Secrets] Execution {execution_id} attempted access to unknown provider credentials: {provider_name}")
            raise SecretAccessViolation(f"Provider {provider_name} credentials not configured.")
            
        env_key = self._provider_key_map[provider_name]
        secret_value = os.getenv(env_key)
        
        if not secret_value:
            logger.error(f"[Secrets] Missing credential for {provider_name}. Execution {execution_id} will fail.")
            raise SecretAccessViolation(f"Missing credential for {provider_name}")
            
        # Audit log the access (masked)
        logger.info(f"[Secrets] Injected {provider_name} credentials for execution {execution_id}")
        
        return secret_value

    def mask_secret(self, secret: str) -> str:
        """Utility for masking secrets before logging or display."""
        if not secret or len(secret) < 8:
            return "****"
        return f"{secret[:4]}****{secret[-4:]}"

global_secret_manager = ExecutionSecretManager()
