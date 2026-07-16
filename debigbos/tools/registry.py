"""Tool registry — manages all available tools with schemas."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolDefinition:
    """Definition of a tool that the agent can call."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    requires_approval: bool = False
    read_only: bool = False


# Tools that are always forbidden in PLAN mode (destructive/irreversible)
_PLAN_BLOCKED_TOOLS = frozenset({"write", "edit", "bash"})

class ToolRegistry:
    """Registry of all available tools with mode-aware filtering."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._tools: dict[str, ToolDefinition] = {}
        self._custom_tools: dict[str, ToolDefinition] = {}
        self._mode: str = "build"  # "build" or "plan"

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        """Set execution mode — filters available tools."""
        self._mode = value.lower()

    def register(self, tool: ToolDefinition) -> None:
        """Register a built-in tool."""
        self._tools[tool.name] = tool

    def load_custom_tools(self) -> None:
        """Load custom tools from .debigbos/tools/."""
        custom_dir = self.workspace / ".debigbos" / "tools"
        if not custom_dir.exists():
            return

        for tool_file in custom_dir.glob("*.json"):
            try:
                data = json.loads(tool_file.read_text(encoding="utf-8"))
                name = data.get("name") or tool_file.stem
                # Custom tools are shell-executed
                def make_handler(cmd, timeout):
                    async def _run(**kwargs):
                        import asyncio
                        import subprocess
                        import os
                        env = {**os.environ, **{f"TOOL_{k.upper()}": str(v) for k, v in kwargs.items()}}
                        proc = subprocess.run(
                            cmd, shell=True, capture_output=True, text=True,
                            timeout=timeout, env=env, cwd=str(self.workspace),
                        )
                        return proc.stdout or proc.stderr or ""
                    return _run

                self._custom_tools[name] = ToolDefinition(
                    name=name,
                    description=data.get("description", ""),
                    parameters=data.get("parameters", {"type": "object", "properties": {}, "required": []}),
                    handler=make_handler(data.get("command", "echo 'no command'"), data.get("timeout", 60)),
                    requires_approval=data.get("requires_approval", True),
                )
            except Exception:
                continue

    def get(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name) or self._custom_tools.get(name)

    def _is_allowed_in_mode(self, tool: ToolDefinition) -> bool:
        """Check if a tool is allowed in the current mode."""
        if self._mode == "plan":
            # Hard-block: plan mode = no write/edit/execute
            if tool.name in _PLAN_BLOCKED_TOOLS:
                return False
            # Trust the tool's own read_only flag as well
            return tool.read_only
        return True  # build mode — everything allowed

    def get_all(self) -> list[ToolDefinition]:
        """Get all registered tools (filtered by current mode)."""
        all_tools = list(self._tools.values()) + list(self._custom_tools.values())
        return [t for t in all_tools if self._is_allowed_in_mode(t)]

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI function format (mode-filtered)."""
        schemas = []
        for tool in self.get_all():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return schemas

    def get_tool_names(self) -> list[str]:
        """Get names of all available tools."""
        return [t.name for t in self.get_all()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with arguments."""
        tool = self.get(name)
        if not tool:
            return json.dumps({"error": f"Tool '{name}' not found"})

        # Hard guard: block write tools in PLAN mode even if LLM hallucinates them
        if not self._is_allowed_in_mode(tool):
            return json.dumps({
                "error": f"Tool '{name}' is blocked in {self._mode.upper()} mode. "
                         f"Switch to BUILD mode to write/edit/execute."
            })

        try:
            result = tool.handler(**arguments)
            if hasattr(result, '__await__'):
                result = await result
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})
