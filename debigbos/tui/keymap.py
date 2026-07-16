"""Keymap system — command registry + keybinding layers.

Inspired by OpenCode's keymap layer system.
Each layer has:
  - commands: list of Command objects with name, title, run()
  - bindings: mapping of command name → key sequence
  - visibility: "registered" | "palette" | "all"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from textual.app import App
from textual.binding import Binding


@dataclass
class Command:
    """A named command with optional title, category, and action."""

    name: str
    title: str = ""
    category: str = "Plugin"
    namespace: str = "palette"
    slash_name: str = ""
    enabled: Callable[[], bool] | None = None
    run: Callable[[], Any] = lambda: None


@dataclass
class LayerBinding:
    """A keybinding layer with commands and bindings."""

    layer_name: str
    commands: list[Command] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    enabled: Callable[[], bool] | None = None


class KeymapRegistry:
    """Global registry for command and keybinding layers."""

    _layers: list[LayerBinding] = []
    _all_commands: dict[str, Command] = {}
    _app_bindings: dict[str, tuple[str, str, str]] = {}  # key → (action, description, show)

    @classmethod
    def register_layer(cls, app: App[Any], layer: dict[str, Any]) -> None:
        """Register a keybinding layer.

        layer = {
            "commands": [Command, ...],
            "bindings": { "command_name": "ctrl+x" },
            "enabled": () -> bool (optional),
        }
        """
        commands = layer.get("commands", [])
        bindings = layer.get("bindings", {})
        enabled = layer.get("enabled")
        layer_name = layer.get("layer_name", f"layer_{len(cls._layers)}")

        lb = LayerBinding(
            layer_name=layer_name,
            commands=commands,
            bindings=bindings,
            enabled=enabled,
        )

        cls._layers.append(lb)

        # Register commands
        for cmd in commands:
            action_name = f"cmd_{cmd.name}"
            cls._all_commands[action_name] = cmd

        # Register bindings with the app
        for cmd_name, key_str in bindings.items():
            if cmd_name not in cls._all_commands:
                # Look up by command name
                action_name = f"cmd_{cmd_name}"
                if action_name not in cls._all_commands:
                    cls._all_commands[action_name] = Command(name=cmd_name)
            else:
                action_name = cmd_name

            cmd = cls._all_commands.get(action_name) or cls._all_commands.get(f"cmd_{cmd_name}")
            if cmd:
                keys = key_str.split(",")
                for key in keys:
                    key = key.strip()
                    if key:
                        cls._app_bindings[key] = (
                            action_name,
                            cmd.title or cmd.name,
                            True,
                        )

    @classmethod
    def apply_to_screen(cls, screen: Any) -> None:
        """Apply registered keybindings to a Textual screen."""
        bindings = []
        for key, (action, description, show) in cls._app_bindings.items():
            bindings.append(Binding(key, action, description, show=show))
        screen.BINDINGS = list(screen.BINDINGS or []) + bindings

    @classmethod
    def get_command_bindings(
        cls,
        visibility: str = "registered",
        commands: list[str] | None = None,
    ) -> "BindingLookupResult":
        """Get bindings for specific commands."""
        result = {}
        for cmd_name in (commands or []):
            found = []
            for key, (action, _, _) in cls._app_bindings.items():
                if action == f"cmd_{cmd_name}" or action == cmd_name:
                    found.append(key)
            result[cmd_name] = found if found else None
        return BindingLookupResult(result)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered layers and bindings."""
        cls._layers.clear()
        cls._all_commands.clear()
        cls._app_bindings.clear()


class BindingLookupResult:
    """Result of a binding lookup."""

    def __init__(self, bindings: dict[str, list[str] | None]) -> None:
        self._bindings = bindings

    def get(self, command_name: str) -> list[str] | None:
        return self._bindings.get(command_name)
