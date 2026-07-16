"""de BigBos TUI — OpenCode-inspired terminal UI framework powered by Textual.

Architecture:
    Plugin System  → Extensible plugin API with lifecycle hooks
    Route System   → Screen-based navigation with params
    Slot System    → Named insertion points for widget injection
    Keymap Layer   → Command registry + keybinding management
    Theme System   → JSON theme tokens mapped to CSS
    Dialog System  → Modal dialogs (Alert, Confirm, Prompt, Select)
"""

from .app import BigBosApp, run_app
from .plugin import TuiPlugin, TuiPluginApi, TuiPluginMeta
from .routes import RouteRegistry
from .slots import SlotRegistry, SlotPlugin
from .keymap import KeymapRegistry, Command, LayerBinding
from .theme import ThemeManager
from .dialogs import DialogAlert, DialogConfirm, DialogPrompt, DialogSelect

__all__ = [
    "BigBosApp",
    "run_app",
    "TuiPlugin",
    "TuiPluginApi",
    "TuiPluginMeta",
    "RouteRegistry",
    "SlotRegistry",
    "SlotPlugin",
    "KeymapRegistry",
    "Command",
    "LayerBinding",
    "ThemeManager",
    "DialogAlert",
    "DialogConfirm",
    "DialogPrompt",
    "DialogSelect",
]
