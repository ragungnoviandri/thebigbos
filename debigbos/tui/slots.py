"""Slot system — named widget injection points.

Inspired by OpenCode's slot system where plugins inject components
into named slots (e.g., home_bottom, sidebar_content, home_prompt).

In Textual, slots are CSS IDs where widgets get mounted dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from textual.app import App
from textual.widget import Widget


@dataclass
class SlotPlugin:
    """A plugin that provides widgets for named slots.

    Attributes:
        order: Rendering order (lower = first)
        slots: Dict mapping slot name → factory function
            factory receives (app, context) and returns a Widget
    """

    order: int
    slots: dict[str, Callable[[App[Any], dict[str, Any]], Widget]]


class SlotRegistry:
    """Global registry of slot plugins."""

    _slots: dict[str, list[SlotPlugin]] = {}
    _contexts: dict[str, dict[str, Any]] = {}

    @classmethod
    def register(cls, app: App[Any], plugin: SlotPlugin) -> None:
        """Register a slot plugin."""
        for slot_name in plugin.slots:
            if slot_name not in cls._slots:
                cls._slots[slot_name] = []
            cls._slots[slot_name].append(plugin)
            # Sort by order
            cls._slots[slot_name].sort(key=lambda p: p.order)

    @classmethod
    def set_context(cls, name: str, ctx: dict[str, Any]) -> None:
        """Set context for a slot."""
        cls._contexts[name] = ctx

    @classmethod
    def get_widgets(
        cls,
        app: App[Any],
        slot_name: str,
        context: dict[str, Any] | None = None,
    ) -> list[Widget]:
        """Get all widgets for a named slot, sorted by order."""
        plugins = cls._slots.get(slot_name, [])
        ctx = context or cls._contexts.get(slot_name, {})
        widgets: list[Widget] = []

        for plugin in plugins:
            factory = plugin.slots.get(slot_name)
            if factory:
                widget = factory(app, ctx)
                if widget is not None:
                    widgets.append(widget)

        return widgets

    @classmethod
    def clear(cls) -> None:
        """Clear all registered slots."""
        cls._slots.clear()
        cls._contexts.clear()
