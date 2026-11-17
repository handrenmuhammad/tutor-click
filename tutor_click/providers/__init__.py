"""AI backends, chosen with TUTOR_CLICK_BACKEND or `tutor-click backend`."""
from .anthropic_api import AnthropicAPI
from .base import Provider, ProviderError
from .claude_code import ClaudeCode
from .codex_cli import CodexCLI
from .openai_compat import GeminiAPI, LocalModel, OpenAIAPI

# Menu order: the two subscriptions first, then what runs here, then what is billed per use.
PROVIDERS: dict[str, type] = {p.id: p for p in (ClaudeCode, CodexCLI, LocalModel, AnthropicAPI, OpenAIAPI, GeminiAPI)}

__all__ = ["PROVIDERS", "Provider", "ProviderError", "create"]


def create(provider_id: str) -> Provider:
    if provider_id not in PROVIDERS:
        raise ProviderError(f"There is no provider called {provider_id}. Choose one of: {', '.join(PROVIDERS)}.", "unknown")
    return PROVIDERS[provider_id]()
