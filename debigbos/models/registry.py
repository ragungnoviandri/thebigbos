"""Provider registry — manages all model providers."""

from typing import Any

from ..config.manager import Config, ProviderConfig
from .openai_provider import OpenAIProvider
from .anthropic_provider import AnthropicProvider
from .opencode_provider import OpencodeGoProvider
from .ollama_provider import OllamaProvider
from .provider import Message, ModelOptions, ModelProvider, ModelResponse


class ProviderRegistry:
    """Registry of all available model providers."""

    def __init__(self, config: Config):
        self.config = config
        self._providers: dict[str, ModelProvider] = {}

    async def initialize(self) -> None:
        """Initialize configured providers. Only creates providers with valid API keys.
        
        After creating each provider, attempts to fetch the live model list from its API
        and merges it into the config (falling back to hardcoded defaults).
        """
        for name, provider_cfg in self.config.providers.items():
            # Skip providers without API keys (unless they don't need one, like ollama)
            if name not in ("ollama",) and (not provider_cfg.api_key or provider_cfg.api_key.startswith("${")):
                continue
            try:
                provider = self._create_provider(name, provider_cfg)
                if provider:
                    provider.name = name  # Use config key as canonical name
                    self._providers[name] = provider
                    # Auto-fetch live model list from provider API
                    await self._sync_models(name, provider, provider_cfg)
            except Exception:
                pass

    async def _sync_models(self, name: str, provider: ModelProvider, cfg: ProviderConfig) -> None:
        """Fetch live models from provider API and merge into config."""
        try:
            live_models = await provider.fetch_models()
            if live_models:
                # Merge: live models first, then append any hardcoded models not in live list
                existing = set(cfg.models)
                for m in live_models:
                    if m not in existing:
                        cfg.models.append(m)
                # Update default model if current default is missing and we have live models
                if cfg.default_model not in cfg.models and live_models:
                    cfg.default_model = live_models[0]
        except Exception:
            pass  # Never break startup for model fetching failure

    def _create_provider(self, name: str, cfg: ProviderConfig) -> ModelProvider | None:
        """Create a provider instance from config."""
        if name == "openai":
            return OpenAIProvider(cfg)
        elif name == "anthropic":
            return AnthropicProvider(cfg)
        elif name == "ollama":
            return OllamaProvider(cfg)
        elif name == "opencode-go":
            return OpencodeGoProvider(cfg)
        elif name in ("openrouter", "groq", "deepseek", "together"):
            return OpenAIProvider(cfg)
        # Default: treat any unknown provider as OpenAI-compatible
        return OpenAIProvider(cfg)

    def get(self, name: str | None = None) -> ModelProvider | None:
        """Get a provider by name, or the active provider."""
        name = name or self.config.active_provider
        return self._providers.get(name)

    @property
    def active(self) -> ModelProvider | None:
        """Get the currently active provider."""
        return self.get(self.config.active_provider)

    @property
    def active_model(self) -> str:
        """Get the currently active model ID."""
        return self.config.active_model

    def list_providers(self) -> list[str]:
        """List all available provider names."""
        return list(self._providers.keys())

    def list_models(self, provider_name: str | None = None) -> list[str]:
        """List models for a provider."""
        provider_name = provider_name or self.config.active_provider
        if cfg := self.config.providers.get(provider_name):
            return cfg.models
        return []

    def register_runtime_provider(self, name: str, cfg: ProviderConfig) -> bool:
        """Register a new provider at runtime. Returns True on success."""
        if name in self._providers:
            return False  # Already registered
        # Add to config
        self.config.providers[name] = cfg
        # Create and register
        provider = self._create_provider(name, cfg)
        if provider:
            provider.name = name  # Use config key as canonical name
            self._providers[name] = provider
            return True
        return False

    def build_tool_schemas(self, tool_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert internal tool definitions to provider-specific format."""
        schemas = []
        for tool in tool_definitions:
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}, "required": []}),
                },
            })
        return schemas
