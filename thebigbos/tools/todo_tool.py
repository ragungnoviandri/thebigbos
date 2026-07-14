"""Todo tracking tool."""

import json
from pathlib import Path

from .registry import ToolDefinition


class TodoTool:
    """Tool for managing task lists during sessions."""

    @staticmethod
    def definition(workspace: Path) -> ToolDefinition:
        state: dict = {"todos": []}

        async def _todos(todos: list[dict]) -> str:
            state["todos"] = todos
            lines = ["## Task List", ""]
            status_icons = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}
            for t in todos:
                icon = status_icons.get(t.get("status", "pending"), "[ ]")
                priority = t.get("priority", "medium")
                lines.append(f"{icon} [{priority}] {t['content']}")
            return "\n".join(lines)

        return ToolDefinition(
            name="todowrite",
            description="Create and maintain a structured task list. Track progress of multi-step work.",
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The todo list items",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Brief description of the task",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                                    "description": "Current status",
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                    "description": "Priority level",
                                },
                            },
                            "required": ["content", "status", "priority"],
                        },
                    },
                },
                "required": ["todos"],
            },
            handler=_todos,
            read_only=True,  # only modifies in-memory state, safe for PLAN mode
        )
