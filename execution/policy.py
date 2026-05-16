import logging

logger = logging.getLogger("nexora.policy")

class PolicyViolationException(Exception):
    pass

class RuntimePolicyEnforcer:
    """
    Centralized governance for token budgets, quota limitations, and 
    escalation policies for dangerous execution behaviors.
    """
    def __init__(self):
        # In a real environment, these would map to database configs per tenant
        self.max_tokens_per_task = 50000
        self.max_subprocesses = 3
        self.allowed_providers = ["openai", "anthropic", "ollama"]

    def validate_provider(self, provider_name: str) -> bool:
        if provider_name.lower() not in self.allowed_providers:
            raise PolicyViolationException(f"Provider {provider_name} is restricted by tenant policy.")
        return True

    def check_token_budget(self, session_id: str, current_usage: int) -> bool:
        """
        Validates if the current execution is within its allocated token budget.
        Triggers HITL escalation if exceeded.
        """
        if current_usage > self.max_tokens_per_task:
            logger.warning(f"[Policy] Session {session_id} exceeded token budget: {current_usage} > {self.max_tokens_per_task}")
            # Abstract integration with HITL could happen here
            raise PolicyViolationException("Token budget exceeded. Task suspended pending operator approval.")
        return True

    def validate_command_escalation(self, command: str) -> bool:
        """
        Checks for commands that require explicit operator approval.
        E.g., network operations, package installations.
        """
        escalation_keywords = ["curl", "wget", "pip install", "npm install", "chmod", "chown"]
        cmd_lower = command.lower()
        
        for keyword in escalation_keywords:
            if keyword in cmd_lower:
                logger.info(f"[Policy] Dangerous command detected requiring HITL approval: {command}")
                return False # Means requires escalation, not strictly blocked
        return True

global_policy_enforcer = RuntimePolicyEnforcer()
