"""Route system — screen-based navigation with params.

Inspired by OpenCode's route.register() / route.navigate() pattern.
Routes map to Textual Screen classes.
"""

from __future__ import annotations

from typing import Any

from textual.app import App
from textual.screen import Screen


class RouteRegistry:
    """Global registry of named routes → Screen classes."""

    _routes: dict[str, type[Screen[Any]]] = {}
    _renderers: dict[str, Any] = {}  # optional render callbacks

    @classmethod
    def register_routes(
        cls,
        app: App[Any],
        routes: list[dict[str, Any]],
    ) -> None:
        """Register routes. Each route dict needs 'name' and 'screen' (Screen class or callable).

        Example:
            RouteRegistry.register_routes(app, [
                {"name": "home", "screen": HomeScreen},
                {"name": "settings", "screen": SettingsScreen},
                {
                    "name": "my-modal",
                    "render": lambda params: MyModal(params),
                },
            ])
        """
        for route in routes:
            name = route["name"]

            if "screen" in route:
                screen_cls = route["screen"]
                cls._routes[name] = screen_cls
                # Register with Textual's screen system
                if issubclass(screen_cls, Screen):
                    app.install_screen(screen_cls, name=name)
            elif "render" in route:
                cls._renderers[name] = route["render"]

    @classmethod
    def navigate(
        cls,
        app: App[Any],
        name: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Navigate to a named route, optionally passing params."""
        if name == "home":
            # Pop all screens and go to root
            while len(app._screen_stack) > 1:
                try:
                    app.pop_screen()
                except Exception:
                    break
            return

        if name in cls._renderers:
            renderer = cls._renderers[name]
            screen = renderer({"params": params or {}}) if params else renderer({})
            app.push_screen(screen)
        elif name in cls._routes:
            screen_cls = cls._routes[name]
            screen = screen_cls()
            if params:
                screen._route_params = params
            app.push_screen(screen)
        else:
            # Try to find by installed screen name
            try:
                app.push_screen(name)
            except Exception:
                app.notify(f"Route not found: {name}", severity="error")

    @classmethod
    def clear(cls) -> None:
        """Clear all registered routes."""
        cls._routes.clear()
        cls._renderers.clear()
