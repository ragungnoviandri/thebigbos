"""Configuration manager for de BigBos — merged from global, project, and env."""

import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .auth import get_auth_manager


class ProviderConfig(BaseModel):
    """Configuration for a single model provider."""
    api_key: str = ""
    base_url: Optional[str] = None
    timeout: int = 120
    models: list[str] = Field(default_factory=list)
    default_model: str = ""


class SoulConfig(BaseModel):
    """Personality / soul configuration."""
    name: str = "de BigBos"
    persona: str = "A sharp, witty AI assistant that's direct and concise."
    tone: str = "professional but casual"
    greeting: str = "Hey! Ready to ship something awesome?"
    constraints: list[str] = Field(default_factory=list)
    custom_prompt: str = ""


class SkillConfig(BaseModel):
    """Skill enable/disable settings."""
    enabled: bool = True
    paths: list[str] = Field(default_factory=lambda: [".debigbos/skills"])
    auto_load: list[str] = Field(default_factory=list)


class CompactionConfig(BaseModel):
    """Context compaction settings (OpenCode-compatible)."""
    auto: bool = True           # Auto-compact when context is full
    threshold: float = 0.8      # Trigger at X% of model context window
    keep: int = 10              # Keep last N non-system messages
    prune: bool = False         # Remove old tool outputs to save tokens
    reserved: int = 10000       # Token buffer to avoid overflow during compaction


class MemoryConfig(BaseModel):
    """Memory persistence settings."""
    enabled: bool = True
    db_path: str = ".debigbos/memory.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    max_short_term: int = 50
    compaction_threshold: float = 0.8
    vector_search_k: int = 5
    # Session resume controls
    session_load_limit: int = 200       # Max messages to load when resuming
    auto_load_session: bool = False     # Auto-load last session on startup
    session_keep_days: int = 0          # Auto-clean sessions older than N days (0=keep all)
    resume_mode: str = "full"          # "full" | "clean" (user+assistant only)
    save_reasoning: bool = True         # Persist thinking/reasoning to DB
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


class AgentConfig(BaseModel):
    """Subagent definitions."""
    name: str
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    max_steps: int = 15
    tools: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """Root configuration."""
    active_provider: str = "openai"
    active_model: str = "gpt-4o"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    soul: SoulConfig = Field(default_factory=SoulConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    max_tool_steps: int = 20
    reasoning_budget: int = 16000
    auto_approve: bool = False
    snapshot: bool = True
    mode: str = "build"  # "plan" = read-only suggestions, "build" = full read/write


DEFAULT_CONFIG = Config(
    providers={
        "openai": ProviderConfig(
            api_key="${OPENAI_API_KEY}",
            models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini", "o1"],
            default_model="gpt-4o",
        ),
        "anthropic": ProviderConfig(
            api_key="${ANTHROPIC_API_KEY}",
            models=["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
            default_model="claude-sonnet-4-20250514",
        ),
        "ollama": ProviderConfig(
            base_url="http://localhost:11434/v1",
            models=["llama3.1", "qwen2.5", "deepseek-r1", "codellama"],
            default_model="llama3.1",
        ),
        "opencode-go": ProviderConfig(
            api_key="${OPENCODE_GO_API_KEY}",
            base_url="https://opencode.ai/zen/go/v1",
            models=["deepseek-v4-pro", "deepseek-v4-flash",
                    "glm-5", "glm-5.1", "glm-5.2",
                    "kimi-k2.5", "kimi-k2.6", "kimi-k2.7-code",
                    "minimax-m2.5", "minimax-m2.7", "minimax-m3",
                    "mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5", "mimo-v2.5-pro",
                    "qwen3.5-plus", "qwen3.6-plus", "qwen3.7-plus", "qwen3.7-max",
                    "hy3-preview"],
            default_model="deepseek-v4-pro",
        ),
        "openrouter": ProviderConfig(
            api_key="${OPENROUTER_API_KEY}",
            base_url="https://openrouter.ai/api/v1",
            models=["openai/gpt-4o", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat"],
            default_model="deepseek/deepseek-chat",
        ),
        "groq": ProviderConfig(
            api_key="${GROQ_API_KEY}",
            base_url="https://api.groq.com/openai/v1",
            models=["llama-3.1-70b", "mixtral-8x7b", "gemma2-9b"],
            default_model="llama-3.1-70b",
        ),
    },
    agents={
        "explore": AgentConfig(
            name="explore",
            description="Read-only codebase explorer. Searches files, patterns, and code.",
            model="gpt-4o-mini",
            max_steps=10,
            tools=["read", "glob", "grep", "webfetch"],
            system_prompt="You are a code explorer. Search and read files only. Never write or edit. Return findings concisely.",
        ),
        "planner": AgentConfig(
            name="planner",
            description="Planning agent. Breaks down complex tasks before execution.",
            model="gpt-4o",
            max_steps=8,
            tools=["read", "glob", "grep", "todo"],
            system_prompt="You are a planner. Analyze the task and create a clear step-by-step plan. Think before coding.",
        ),
        "reviewer": AgentConfig(
            name="reviewer",
            description="Code reviewer. Reviews code for bugs, style, and security.",
            model="gpt-4o-mini",
            max_steps=5,
            tools=["read", "glob", "grep"],
            system_prompt="You are a code reviewer. Find bugs, style issues, and security problems. Be thorough but concise.",
        ),
    },
)


class ConfigManager:
    """Manages configuration loading and merging from multiple sources."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace or Path.cwd()
        self.config = DEFAULT_CONFIG

    def _resolve_env(self, value: str) -> str:
        """Resolve ${ENV_VAR} patterns in config values."""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var, "")
        return value

    def load(self) -> Config:
        """Load and merge config from all sources."""
        paths = [
            Path.home() / ".config" / "deBigBos" / "config.json",
            self.workspace / "deBigBos.json",
            self.workspace / ".debigbos" / "config.json",
        ]
        merged = self.config.model_dump()

        for path in paths:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._deep_merge(merged, data)
                except (json.JSONDecodeError, OSError):
                    continue

        config = Config.model_validate(merged)
        self._resolve_api_keys(config)
        return config

    def _deep_merge(self, base: dict, override: dict) -> None:
        """Recursively merge override into base. Resolves env vars and skips empty."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            elif isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # Env var — resolve it; if empty, don't override existing value
                resolved = os.environ.get(value[2:-1], "")
                if resolved:
                    base[key] = resolved
                # If env var not set, keep the base value (e.g., from global config)
            elif value == "" or value is None:
                continue
            else:
                base[key] = value

    def _resolve_api_keys(self, config: Config) -> None:
        """Resolve API keys with priority: env var > auth.json > auto-detect from external tools."""
        auth = get_auth_manager()

        # Map provider names to their env vars
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "opencode-go": "OPENCODE_GO_API_KEY",
            "opencode-zen": "OPENCODE_ZEN_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "together": "TOGETHER_API_KEY",
        }

        for name, provider in config.providers.items():
            # Skip ollama — no API key needed
            if name == "ollama":
                continue

            env_var = env_map.get(name)
            # Use AuthManager's priority resolution
            resolved = auth.resolve_key(name, env_var)
            if resolved:
                provider.api_key = resolved

            # Also resolve base_url from auth.json if stored there
            stored_url = auth.get_base_url(name)
            if stored_url and (not provider.base_url or provider.base_url.startswith("http://localhost")):
                provider.base_url = stored_url

    def get_provider_config(self, name: str) -> ProviderConfig | None:
        """Get config for a specific provider."""
        return self.config.providers.get(name)

    def get_agent_config(self, name: str) -> AgentConfig | None:
        """Get config for a specific subagent."""
        return self.config.agents.get(name)

    def save(self, path: Path | None = None) -> None:
        """Save current config to file."""
        target = path or (self.workspace / "deBigBos.json")
        data = self.config.model_dump(exclude_none=True)
        target.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
