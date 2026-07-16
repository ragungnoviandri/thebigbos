"""Plugin system — inspired by OpenCode's TuiPlugin architecture.

Plugins are async functions that receive a TuiPluginApi and optional config.
They can:
  - Register routes (screens)
  - Register slots (widget injection points)
  - Register keybindings (commands + bindings)
  - Install themes
  - Hook into lifecycle events
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Protocol

from textual.app import App
from textual.screen import Screen


@dataclass
class TuiPluginMeta:
    """Metadata about a loaded plugin."""
    id: str
    name: str = ""
    state: str = "first"       # first | updated | error
    source: str = ""
    load_count: int = 0
    error: str | None = None


class TuiPluginApi:
    """API surface exposed to plugins.

    Mirrors OpenCode's TuiPluginApi:
      - api.route.register(...)
      - api.slots.register(...)
      - api.keymap.register_layer(...)
      - api.theme.install(...)
      - api.theme.set(...)
      - api.ui.dialog — open/close/stack
      - api.ui.toast(...)
      - api.renderer.add_post_process_fn(...)
      - api.lifecycle.on_dispose(...)
      - api.keys.format_bindings(...)
    """

    def __init__(self, app: App[Any]) -> None:
        self._app = app
        self.route = _RouteApi(self)
        self.slots = _SlotApi(self)
        self.keymap = _KeymapApi(self)
        self.theme = _ThemeApi(self)
        self.ui = _UiApi(self)
        self.renderer = _RendererApi(self)
        self.lifecycle = _LifecycleApi(self)
        self.keys = _KeysApi(self)


class TuiPlugin(Protocol):
    """Protocol for a TUI plugin function.

    async def my_plugin(api: TuiPluginApi, options: dict | None, meta: TuiPluginMeta) -> None:
        ...
    """

    async def __call__(
        self,
        api: TuiPluginApi,
        options: dict[str, Any] | None = None,
        meta: TuiPluginMeta | None = None,
    ) -> None: ...


# ——— Sub-APIs ———


class _RouteApi:
    """Route registration and navigation."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent

    @property
    def current(self) -> Any:
        """Current route info: { name, params }."""
        app = self._parent._app
        return type("RouteInfo", (), {
            "name": type(app.screen).__name__,
            "params": getattr(app.screen, "_route_params", {}),
        })

    def register(self, routes: list[dict[str, Any]]) -> None:
        """Register one or more routes (screens) with the app.

        Each route: { name: str, screen: Screen, render: callable (optional) }
        """
        from .routes import RouteRegistry
        RouteRegistry.register_routes(self._parent._app, routes)

    def navigate(self, name: str, params: dict[str, Any] | None = None) -> None:
        """Navigate to a named route/screen."""
        from .routes import RouteRegistry
        RouteRegistry.navigate(self._parent._app, name, params)


class _SlotApi:
    """Slot registration for widget injection."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent

    def register(self, plugin: Any) -> None:
        """Register a SlotPlugin."""
        from .slots import SlotRegistry
        SlotRegistry.register(self._parent._app, plugin)


class _KeymapApi:
    """Keymap layer registration."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent

    def register_layer(self, layer: dict[str, Any]) -> None:
        """Register a keybinding layer with commands and bindings."""
        from .keymap import KeymapRegistry
        KeymapRegistry.register_layer(self._parent._app, layer)


class _ThemeApi:
    """Theme management."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent
        self._themes: dict[str, dict[str, Any]] = {}

    @property
    def current(self) -> dict[str, Any]:
        """Current active theme tokens."""
        from .theme import ThemeManager
        return ThemeManager.current()

    async def install(self, path: str) -> None:
        """Load and install a theme from a JSON file."""
        from .theme import ThemeManager
        await ThemeManager.install(self._parent._app, path)

    def set(self, name: str) -> None:
        """Set the active theme by name."""
        from .theme import ThemeManager
        ThemeManager.set_active(self._parent._app, name)


class _UiApi:
    """UI helpers — dialogs, toasts, etc."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent
        self.dialog = _DialogStackApi(parent)
        from .dialogs import DialogAlert, DialogConfirm, DialogPrompt, DialogSelect
        self.DialogAlert = DialogAlert
        self.DialogConfirm = DialogConfirm
        self.DialogPrompt = DialogPrompt
        self.DialogSelect = DialogSelect

    def toast(self, info: dict[str, Any]) -> None:
        """Show a toast notification."""
        app = self._parent._app
        app.notify(
            info.get("message", ""),
            title=info.get("title", ""),
            severity=info.get("variant", "information"),
            timeout=info.get("duration", 3000) / 1000,
        )


class _DialogStackApi:
    """Dialog stack management — mimics OpenCode's api.ui.dialog."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent
        self._size: str = "medium"
        self._stack: list[Screen[Any]] = []

    @property
    def open(self) -> bool:
        return len(self._stack) > 0

    @property
    def depth(self) -> int:
        return len(self._stack)

    def set_size(self, size: str) -> None:
        self._size = size

    def replace(self, screen_factory: Callable[[], Screen[Any]]) -> None:
        """Replace the current dialog with a new one."""
        self.clear()
        screen = screen_factory()
        self._stack.append(screen)
        self._parent._app.push_screen(screen)

    def clear(self) -> None:
        """Close all dialogs."""
        while self._stack:
            self._parent._app.pop_screen()
        self._stack.clear()


class _RendererApi:
    """Post-processing effects for the renderer."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent
        self._effects: list[Callable] = []

    def add_post_process_fn(self, fn: Callable) -> None:
        """Add a post-processing effect function."""
        self._effects.append(fn)

    def remove_post_process_fn(self, fn: Callable) -> None:
        """Remove a post-processing effect function."""
        if fn in self._effects:
            self._effects.remove(fn)


class _LifecycleApi:
    """Lifecycle hooks."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent
        self._dispose_hooks: list[Callable[[], Any]] = []

    def on_dispose(self, fn: Callable[[], Any]) -> None:
        """Register a cleanup function called when plugin is disposed."""
        self._dispose_hooks.append(fn)


class _KeysApi:
    """Key formatting helpers."""

    def __init__(self, parent: TuiPluginApi) -> None:
        self._parent = parent

    @staticmethod
    def format_bindings(bindings: list[str] | None) -> str | None:
        """Format a list of keybindings for display."""
        if not bindings:
            return None
        return ", ".join(bindings[:3])


# ——— Plugin Manager ———


@dataclass
class PluginEntry:
    """A loaded plugin instance."""
    meta: TuiPluginMeta
    fn: TuiPlugin
    dispose: list[Callable[[], Any]] = field(default_factory=list)


class PluginManager:
    """Manages the lifecycle of all TUI plugins."""

    def __init__(self) -> None:
        self._plugins: list[PluginEntry] = []
        self._api: TuiPluginApi | None = None

    def bind(self, app: App[Any]) -> None:
        """Bind to a Textual app."""
        self._api = TuiPluginApi(app)

    async def load(
        self,
        plugin_fn: TuiPlugin,
        options: dict[str, Any] | None = None,
        plugin_id: str = "",
    ) -> PluginEntry | None:
        """Load and activate a plugin."""
        if not self._api:
            return None

        meta = TuiPluginMeta(id=plugin_id or getattr(plugin_fn, "__name__", "unknown"))

        try:
            meta.state = "first"
            await plugin_fn(self._api, options, meta)
            meta.load_count += 1

            entry = PluginEntry(
                meta=meta,
                fn=plugin_fn,
                dispose=list(self._api.lifecycle._dispose_hooks),
            )
            self._plugins.append(entry)
            return entry

        except Exception as e:
            meta.state = "error"
            meta.error = str(e)
            return None

    async def reload(self, plugin_id: str) -> PluginEntry | None:
        """Reload a plugin by ID."""
        for entry in self._plugins:
            if entry.meta.id == plugin_id:
                # Dispose old
                for hook in entry.dispose:
                    try:
                        hook()
                    except Exception:
                        pass
                self._plugins.remove(entry)
                # Reload
                return await self.load(entry.fn, None, plugin_id)

    def dispose_all(self) -> None:
        """Dispose all plugins."""
        for entry in self._plugins:
            for hook in entry.dispose:
                try:
                    hook()
                except Exception:
                    pass
        self._plugins.clear()
