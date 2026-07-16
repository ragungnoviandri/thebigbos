"""Theme system — JSON-based theme tokens.

Inspired by OpenCode's theme.install() / theme.set() pattern.
Themes are JSON files with color tokens that map to Textual CSS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.app import App


DEFAULT_THEME = {
    "name": "opencode-dark",
    "background": "#0a0a0a",
    "backgroundPanel": "#212121",
    "border": "#4b4c5c",
    "text": "#e0e0e0",
    "textMuted": "#6a6a6a",
    "primary": "#fab283",
    "secondary": "#5c9cf5",
    "accent": "#9d7cd8",
    "success": "#7fd88f",
    "warning": "#f5a742",
    "error": "#e06c75",
    "info": "#56b6c2",
    "selectedListItemText": "#ffffff",
    "scrollbar": "#4b4c5c",
    "scrollbarHover": "#5c9cf5",
    "surface": "#252525",
    "surfaceHover": "#303030",
}


class ThemeManager:
    """Manages theme loading, switching, and hot reload."""

    _themes: dict[str, dict[str, Any]] = {"default": DEFAULT_THEME}
    _active: str = "default"
    _app: App[Any] | None = None

    @classmethod
    def current(cls) -> dict[str, Any]:
        """Get the current active theme tokens."""
        return cls._themes.get(cls._active, DEFAULT_THEME)

    @classmethod
    async def install(cls, app: App[Any], path: str) -> None:
        """Load and install a theme from a JSON file."""
        cls._app = app

        # Resolve path
        theme_path = Path(path)
        if not theme_path.is_absolute():
            # Try relative to workspace or .debigbos/themes/
            candidates = [
                Path.cwd() / path,
                Path.cwd() / ".debigbos" / "themes" / Path(path).name,
                Path.home() / ".config" / "deBigBos" / "themes" / Path(path).name,
            ]
            for candidate in candidates:
                if candidate.exists():
                    theme_path = candidate
                    break

        if not theme_path.exists():
            app.notify(f"Theme not found: {path}", severity="warning")
            return

        try:
            theme_data = json.loads(theme_path.read_text(encoding="utf-8"))
            name = theme_data.get("name", theme_path.stem)
            cls._themes[name] = theme_data
            app.notify(f"Theme loaded: {name}", title="Theme")
        except Exception as e:
            app.notify(f"Failed to load theme: {e}", severity="error")

    @classmethod
    def set_active(cls, app: App[Any], name: str) -> None:
        """Set the active theme by name."""
        if name in cls._themes:
            cls._active = name
            cls._app = app
            cls._apply_theme(app)

    @classmethod
    def _apply_theme(cls, app: App[Any]) -> None:
        """Apply current theme to the app via stylesheet."""
        theme = cls.current()

        # Build clean CSS — no CSS variables (Textual doesn't support them)
        css_lines = []
        css_lines.append("/* Auto-generated theme CSS */")
        css_lines.append("Screen {")
        css_lines.append(f"  background: {theme.get('background', '#0d0d0d')};")
        css_lines.append(f"  border: solid {theme.get('primary', '#00d4ff')};")
        css_lines.append("}")

        # Apply via app
        try:
            app.stylesheet.add("\n".join(css_lines))
        except Exception:
            pass

    @classmethod
    def list_themes(cls) -> list[str]:
        """List all loaded theme names."""
        return list(cls._themes.keys())
