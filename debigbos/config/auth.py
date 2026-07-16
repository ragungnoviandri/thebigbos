"""Auth manager — persistent API key storage in ~/.config/deBigBos/auth.json.

Priority (highest to lowest):
  1. Environment variable (temporary override)
  2. auth.json (persistent storage)
  3. Auto-detect from OpenCode and other external tools
"""

import json
import os
from pathlib import Path
from typing import Optional


class AuthManager:
    """Manages API keys in a central auth.json file."""

    AUTH_DIR = Path.home() / ".config" / "deBigBos"
    AUTH_FILE = AUTH_DIR / "auth.json"

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load auth data from disk."""
        if self._loaded:
            return
        self._loaded = True
        if self.AUTH_FILE.exists():
            try:
                self._data = json.loads(self.AUTH_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get_key(self, provider_name: str) -> str:
        """Get API key for a provider. Returns empty string if not found."""
        self._ensure_loaded()
        entry = self._data.get(provider_name, {})
        return entry.get("key", "")

    def set_key(self, provider_name: str, key: str, base_url: str = "") -> None:
        """Store API key (and optional base_url) for a provider."""
        self._ensure_loaded()
        if provider_name not in self._data:
            self._data[provider_name] = {}
        self._data[provider_name]["key"] = key
        if base_url:
            self._data[provider_name]["base_url"] = base_url
        self._save()

    def get_base_url(self, provider_name: str) -> str:
        """Get stored base_url for a provider."""
        self._ensure_loaded()
        entry = self._data.get(provider_name, {})
        return entry.get("base_url", "")

    def list_providers(self) -> list[str]:
        """List all providers that have keys stored."""
        self._ensure_loaded()
        return [k for k, v in self._data.items() if v.get("key", "")]

    def remove_key(self, provider_name: str) -> None:
        """Remove a provider's stored key."""
        self._ensure_loaded()
        if provider_name in self._data:
            del self._data[provider_name]
            self._save()

    def _save(self) -> None:
        """Write to auth.json, creating directory if needed."""
        self.AUTH_DIR.mkdir(parents=True, exist_ok=True)
        self.AUTH_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ——— Resolve with priority ———

    def resolve_key(self, provider_name: str, env_var: str | None = None) -> str:
        """Resolve API key with priority: env var > auth.json > auto-detect.

        Args:
            provider_name: Provider name (e.g., 'openai', 'opencode-go')
            env_var: Environment variable name to check first

        Returns:
            Resolved API key string (empty if not found anywhere)
        """
        # 1. Environment variable (highest priority)
        if env_var:
            env_val = os.environ.get(env_var, "")
            if env_val:
                return env_val

        # 2. auth.json
        key = self.get_key(provider_name)
        if key:
            return key

        # 3. Auto-detect from external tools
        key = self._detect_from_external(provider_name)
        if key:
            return key

        return ""

    def _detect_from_external(self, provider_name: str) -> str:
        """Auto-detect API keys from external tools like OpenCode."""
        # Map provider names to the keys used in external auth files
        provider_map = {
            "opencode-go": ["opencode-go", "opencode_go"],
            "opencode-zen": ["opencode-zen", "opencode_zen"],
            "openai": ["openai"],
            "anthropic": ["anthropic"],
        }
        search_names = provider_map.get(provider_name, [provider_name])

        # Check OpenCode auth (~/.local/share/opencode/auth.json)
        oc_auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if oc_auth.exists():
            try:
                data = json.loads(oc_auth.read_text(encoding="utf-8"))
                for name in search_names:
                    if name in data and data[name].get("key"):
                        return data[name]["key"]
            except Exception:
                pass

        return ""


# Singleton
_auth_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """Get the global AuthManager singleton."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
