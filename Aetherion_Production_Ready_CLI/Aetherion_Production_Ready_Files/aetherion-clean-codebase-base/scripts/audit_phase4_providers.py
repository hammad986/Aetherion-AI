import os
import sys
sys.path.insert(0, os.path.abspath("."))

def main():
    print("=== PROVIDER ENVIRONMENT CHECK ===")
    key_names = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "LLM_PROVIDER",
        "DEFAULT_MODEL",
    ]
    for k in key_names:
        v = os.environ.get(k)
        if v:
            masked = v[:4] + "..." if len(v) > 4 else "***"
            print(f"  {k}: SET ({masked})")
        else:
            print(f"  {k}: NOT SET")

    from backend.execution.providers.store import get_provider_store
    from backend.execution.providers.registry import ProviderRegistry
    
    print("\n=== PROVIDER REGISTRY & STORE ===")
    store = get_provider_store()
    providers = store.list_providers()
    print(f"Store has {len(providers)} providers:")
    for p in providers:
        print(f"  - ID: {p.id}, Name: {p.name}, Type: {p.provider_type}, Enabled: {p.enabled}, Models: {p.models}")

    active_provider = store.get_active_provider()
    print(f"\nActive Provider: {active_provider.id if active_provider else 'None'}")

    # Inspect execution fabric agent worker provider resolution
    from backend.execution.fabric.worker import AgentWorker
    print(f"AgentWorker LLM_PROVIDER env default: {os.getenv('LLM_PROVIDER', 'mock')}")

if __name__ == "__main__":
    main()
