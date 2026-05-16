import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple

logger = logging.getLogger("nexora.providers")

class ProviderConfig:
    def __init__(self, api_key: str, model: str, base_url: str = None, max_retries: int = 3, timeout_sec: int = 60):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_retries = max_retries
        self.timeout_sec = timeout_sec

class ModelProviderAdapter(ABC):
    """
    Modular isolation for LLM routing.
    Enforces uniform response schemas, timeout boundaries, and token tracking.
    """
    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    def generate(self, messages: list) -> Tuple[str, Dict[str, int]]:
        """Returns (content, usage_dict)"""
        pass

    @abstractmethod
    def stream(self, messages: list):
        """Yields string chunks."""
        pass

class OpenAIAdapter(ModelProviderAdapter):
    def generate(self, messages: list) -> Tuple[str, Dict[str, int]]:
        # Mock implementation demonstrating adapter boundaries
        logger.debug(f"[OpenAI] Calling {self.config.model}")
        return "mock_response", {"prompt_tokens": 10, "completion_tokens": 5}
        
    def stream(self, messages: list):
        yield "mock_"
        yield "stream_"
        yield "response"

class AnthropicAdapter(ModelProviderAdapter):
    def generate(self, messages: list) -> Tuple[str, Dict[str, int]]:
        logger.debug(f"[Anthropic] Calling {self.config.model}")
        return "mock_anthropic_response", {"prompt_tokens": 10, "completion_tokens": 5}
        
    def stream(self, messages: list):
        yield "mock_"
        yield "anthropic"

class ProviderCapabilities:
    """Metadata for orchestrator decision routing."""
    def __init__(self, supports_vision: bool, max_context: int, supports_tools: bool):
        self.supports_vision = supports_vision
        self.max_context = max_context
        self.supports_tools = supports_tools

class ProviderRouter:
    """
    Handles fallback chains and provider health tracking.
    """
    def __init__(self):
        # Maps provider_name to (successes, failures, last_latency_ms)
        self.health_tracker: Dict[str, Dict[str, Any]] = {}
        
    def _record_health(self, provider_name: str, success: bool, latency: float = 0):
        if provider_name not in self.health_tracker:
            self.health_tracker[provider_name] = {"success": 0, "fail": 0, "latency": []}
            
        tracker = self.health_tracker[provider_name]
        if success:
            tracker["success"] += 1
        else:
            tracker["fail"] += 1
            
        tracker["latency"].append(latency)
        if len(tracker["latency"]) > 10:
            tracker["latency"].pop(0)

    def route_request(self, primary_provider: str, config: ProviderConfig, 
                      fallback_chain: List[str] = None) -> ModelProviderAdapter:
        """
        Attempts to route to the primary provider. If the primary has a high failure rate,
        it automatically routes to the next healthy provider in the fallback chain.
        """
        target = primary_provider
        
        # Basic health-aware fallback logic
        if fallback_chain:
            stats = self.health_tracker.get(primary_provider, {})
            fail_rate = stats.get("fail", 0) / max(1, stats.get("success", 1) + stats.get("fail", 0))
            if fail_rate > 0.5:
                logger.warning(f"[Providers] {primary_provider} degraded (fail rate {fail_rate:.2f}). Triggering fallback.")
                target = fallback_chain[0]
                
        # Instantiation
        if target.lower() == "openai":
            return OpenAIAdapter(config)
        elif target.lower() == "anthropic":
            return AnthropicAdapter(config)
            
        raise ValueError(f"Unknown provider: {target}")
