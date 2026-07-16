"""Abstract model provider and data types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    """A tool call from the model."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result from executing a tool."""
    tool_call_id: str
    name: str
    output: str
    error: str | None = None


@dataclass
class Message:
    """A conversation message."""
    role: Literal["system", "user", "assistant", "tool", "reasoning"]
    content: str
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ModelResponse:
    """Response from a model call."""
    content: str = ""
    reasoning_content: str = ""  # Thinking/reasoning from DeepSeek, o1/o3, Claude
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class ModelOptions:
    """Options for a model call."""
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    reasoning_effort: str | None = None
    thinking_budget: int | None = None


class ModelProvider(ABC):
    """Abstract base for all model providers."""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> ModelResponse:
        """Send messages to the model and get a response."""
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ):
        """Stream responses from the model."""
        ...

    def count_tokens(self, messages: list[Message]) -> int:
        """Estimate token count for a list of messages."""
        return sum(len(m.content.split()) * 1.3 for m in messages)

    def get_context_window(self, model: str) -> int:
        """Return the context window size (in tokens) for a given model.

        Resolution order:
          1. models.dev dynamic registry (accurate, community-maintained)
          2. Hardcoded _MODEL_CONTEXT_WINDOWS fallback
          3. Default: 128_000 tokens
        """
        # Try models.dev first (dynamic, updated hourly)
        try:
            from ..core.models_dev import get_context_window as _mdev_ctx
            ctx = _mdev_ctx(self.name, model)
            if ctx and ctx > 0:
                return ctx
        except Exception:
            pass
        return _MODEL_CONTEXT_WINDOWS.get(model, 128000)

    async def fetch_models(self) -> list[str]:
        """Fetch available models from the provider's API.
        
        Override in subclasses that support model listing via API.
        Default returns empty list — provider uses hardcoded config models.
        """
        return []


# ——— Known model context windows (in tokens) ———
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-5": 128000,
    "gpt-5.1": 128000,
    "gpt-5.1-codex": 128000,
    "gpt-5.2": 128000,
    "o3-mini": 200000,
    "o1": 200000,
    "o4-mini": 200000,
    # Anthropic
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-opus-4.5": 200000,
    "claude-sonnet-4.5": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-opus-20240229": 200000,
    "claude-3-haiku-20240307": 200000,
    # DeepSeek (via OpenCode)
    "deepseek-v4-pro": 1000000,
    "deepseek-v4-flash": 1000000,
    "deepseek-v3": 128000,
    "deepseek-v3.2": 1000000,
    "deepseek-r1": 128000,
    "deepseek-r1-0528": 1000000,
    "deepseek-r1-distill-llama-70b": 128000,
    # Qwen (via OpenCode)
    "qwen-plus": 131072,
    "qwen-max": 131072,
    "qwen3.5-397b": 131072,
    "qwen3.5-plus": 131072,
    "qwen3.6-plus": 131072,
    "qwen3.7-plus": 131072,
    "qwen3.7-max": 131072,
    "qwen2.5": 131072,
    # Kimi (via OpenCode)
    "kimi-k2": 131072,
    "kimi-k2.5": 131072,
    "kimi-k2.6": 131072,
    "kimi-k2.7-code": 131072,
    # GLM (via OpenCode)
    "glm-4": 131072,
    "glm-5": 131072,
    "glm-5.1": 131072,
    "glm-5.2": 131072,
    "glm5": 131072,  # legacy alias
    # MiniMax (via OpenCode)
    "minimax-m1": 1000000,
    "minimax-m2.5": 1000000,
    "minimax-m2.7": 1000000,
    "minimax-m3": 1000000,
    # Mimo (via OpenCode)
    "mimo-v2": 131072,
    "mimo-v2-pro": 131072,
    "mimo-v2-omni": 131072,
    "mimo-v2.5": 131072,
    "mimo-v2.5-pro": 131072,
    # Hy (via OpenCode)
    "hy3-preview": 131072,
    # Mistral
    "mistral-large-3": 131072,
    # Gemini
    "gemini-2.5-pro": 1048576,
    "gemini-2.5-flash": 1048576,
    "gemini-3-pro": 1048576,
    # Groq
    "llama-3.1-70b": 128000,
    "llama-3.3-70b": 128000,
    "mixtral-8x7b": 32000,
    "gemma2-9b": 8192,
    # Ollama local
    "llama3.1": 128000,
    "qwen2.5": 128000,
    "codellama": 16384,
    "phi3": 4096,
}

# ——— Model pricing (per 1M tokens) ———
# Format: (input_price_per_1M, output_price_per_1M) in USD
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-5": (1.75, 14.00),
    "gpt-5.1": (1.75, 14.00),
    "gpt-5.1-codex": (1.75, 14.00),
    "gpt-5.2": (1.75, 14.00),
    "o3-mini": (1.10, 4.40),
    "o1": (15.00, 60.00),
    "o4-mini": (1.10, 4.40),
    # Anthropic
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-opus-4.5": (15.00, 75.00),
    "claude-sonnet-4.5": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    # DeepSeek (via OpenCode)
    "deepseek-v4-pro": (0.55, 2.19),
    "deepseek-v4-flash": (0.27, 1.10),
    "deepseek-v3": (0.27, 1.10),
    "deepseek-v3.2": (0.55, 2.19),
    "deepseek-r1": (0.55, 2.19),
    "deepseek-r1-0528": (0.55, 2.19),
    "deepseek-r1-distill-llama-70b": (0.27, 1.10),
    # Qwen (via OpenCode)
    "qwen-plus": (0.80, 3.20),
    "qwen-max": (3.20, 12.80),
    "qwen3.5-397b": (0.80, 3.20),
    "qwen3.5-plus": (0.80, 3.20),
    "qwen3.6-plus": (0.80, 3.20),
    "qwen3.7-plus": (0.80, 3.20),
    "qwen3.7-max": (3.20, 12.80),
    "qwen2.5": (0.80, 3.20),
    # Kimi (via OpenCode)
    "kimi-k2": (0.55, 2.19),
    "kimi-k2.5": (0.55, 2.19),
    "kimi-k2.6": (0.55, 2.19),
    "kimi-k2.7-code": (0.55, 2.19),
    # GLM (via OpenCode)
    "glm-4": (0.55, 2.19),
    "glm-5": (0.55, 2.19),
    "glm-5.1": (0.55, 2.19),
    "glm-5.2": (0.55, 2.19),
    "glm5": (0.55, 2.19),
    # MiniMax (via OpenCode)
    "minimax-m1": (0.55, 2.19),
    "minimax-m2.5": (0.55, 2.19),
    "minimax-m2.7": (0.55, 2.19),
    "minimax-m3": (0.55, 2.19),
    # Mimo (via OpenCode)
    "mimo-v2": (0.55, 2.19),
    "mimo-v2-pro": (0.55, 2.19),
    "mimo-v2-omni": (0.55, 2.19),
    "mimo-v2.5": (0.55, 2.19),
    "mimo-v2.5-pro": (0.55, 2.19),
    # Hy (via OpenCode)
    "hy3-preview": (0.55, 2.19),
    # Mistral
    "mistral-large-3": (4.00, 12.00),
    # Gemini
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-3-pro": (1.25, 10.00),
    # Groq
    "llama-3.1-70b": (0.59, 0.79),
    "llama-3.3-70b": (0.59, 0.79),
    "mixtral-8x7b": (0.50, 0.50),
    "gemma2-9b": (0.20, 0.20),
    # Ollama local — free
    "llama3.1": (0, 0),
    "qwen2.5": (0, 0),
    "codellama": (0, 0),
    "phi3": (0, 0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for token usage on a given model."""
    pricing = _MODEL_PRICING.get(model)
    if not pricing:
        # Fuzzy match
        model_lower = model.lower()
        for key, price in _MODEL_PRICING.items():
            if key in model_lower or model_lower in key:
                pricing = price
                break
    if not pricing:
        return 0.0
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
