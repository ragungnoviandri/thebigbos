"""Home screen — main chat interface with sidebar, tool log, and prompt.

Layout (inspired by OpenCode):
┌──────────────────────────────────────────┐
│  Header (mode, model, provider)          │
├──────────────────────┬───────────────────┤
│                      │  Session Info     │
│   Chat / Response    │  ─────────────    │
│   Area               │  Model/Provider   │
│                      │  Context Usage    │
│                      │  Skills           │
│                      │  Sidebar Slots    │
├──────────────────────┴───────────────────┤
│  Tool Execution Log                      │
├──────────────────────────────────────────┤
│  Prompt ChatInput                            │
└──────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TextArea,
)

from ..keymap import KeymapRegistry


class ChatInput(TextArea):
    """Multi-line input. Enter=newline, Ctrl+Enter=send, Button=send. Max 3 rows."""

    BINDINGS = [("enter", "action_submit_text", "Send")]

    class Submitted(Message):
        control = None
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_mount(self) -> None:
        self.styles.max_height = 6
        self.border_title = "Ctrl+Enter to send"

    def action_submit_text(self) -> None:
        text = self.text.strip()
        if text:
            self.post_message(self.Submitted(text))
            self.clear()


class StatusBar(Static):
    """Bottom status bar like Hermes — model, context, timing."""

    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    context_tokens: reactive[int] = reactive(0)
    mode: reactive[str] = reactive("build")
    elapsed: reactive[float] = reactive(0)
    thinking: reactive[bool] = reactive(False)

    def render(self) -> str:
        parts = []
        if self.provider and self.model:
            parts.append(f" {self.provider}/{self.model}")
        ctx_str = f"ctx {self.context_tokens:,}" if self.context_tokens > 0 else "ctx --"
        parts.append(ctx_str)
        bar = self._make_bar(min(100, int(self.context_tokens / 128000 * 100))) if self.context_tokens > 0 else "[..........]"
        parts.append(bar)
        if self.elapsed > 0:
            parts.append(f"{self.elapsed:.0f}s")
        if self.thinking:
            parts.append("[yellow]thinking...[/yellow]")
        return " │ ".join(parts)

    @staticmethod
    def _make_bar(pct: int, width: int = 8) -> str:
        filled = max(1, int(width * pct / 100))
        if pct < 50:
            color = ""
        elif pct < 80:
            color = "yellow"
        else:
            color = "red"
        bar = "[" + "|" * filled + "." * (width - filled) + "]"
        return f"[{color}]{bar}[/{color}]" if color else bar


class SidebarWidget(Static):
    """Session info sidebar."""

    session_id: reactive[str] = reactive("")
    session_title: reactive[str] = reactive("Untitled")
    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    context_tokens: reactive[int] = reactive(0)
    skill_count: reactive[int] = reactive(0)
    auto_approve: reactive[bool] = reactive(False)
    mode: reactive[str] = reactive("build")
    thinking: reactive[bool] = reactive(False)
    error_msg: reactive[str] = reactive("")

    def render(self) -> str:
        lines = []
        lines.append(" Session Info")
        lines.append(" ─────────────")
        lines.append("")
        if self.error_msg:
            lines.append(f" [red]Error: {self.error_msg[:60]}[/red]")
            lines.append("")
        if self.thinking:
            lines.append(" [bold yellow]...thinking...[/bold yellow]")
            lines.append("")
        if self.session_id:
            lines.append(f" Session: {self.session_id[:12]}")
            lines.append(f" Title: {self.session_title[:30]}")
        else:
            lines.append(" Session: -")
        lines.append("")
        lines.append(f" Mode: [bold]{self.mode}[/bold]")
        lines.append(f" Model: {self.model[:28]}")
        lines.append(f" Provider: {self.provider[:15]}")
        lines.append("")

        if self.context_tokens > 0:
            ctx_limit = 128000
            pct = min(100, int(self.context_tokens / ctx_limit * 100))
            bar = self._make_bar(pct)
            lines.append(f" Context: {self.context_tokens:,} tokens")
            lines.append(f" Usage: {bar} {pct}%")
            lines.append("")

        if self.skill_count:
            lines.append(f" Skills: {self.skill_count}")
        if self.auto_approve:
            lines.append(" Auto: [yellow]ON[/yellow]")
        return "\n".join(lines)

    @staticmethod
    def _make_bar(pct: int, width: int = 10) -> str:
        filled = int(width * pct / 100)
        return "[" + "|" * filled + "." * (width - filled) + "]"


class ToolLogWidget(Static):
    """Shows recent tool executions."""

    tool_entries: reactive[list[dict[str, Any]]] = reactive([])

    def render(self) -> str:
        if not self.tool_entries:
            return ""

        lines = [" Tools"]
        lines.append(" ─────")
        for t in self.tool_entries[-5:]:
            icon = "..." if t.get("status") == "running" else "OK"
            args_str = json.dumps(t.get("args", {}))[:60]
            lines.append(f" {icon} {t['name']}({args_str})")
        return "\n".join(lines)


class ResponseArea(RichLog):
    """Rich text area for model responses — selectable + copyable."""

    def on_mount(self) -> None:
        self.can_focus = True
        self.border_title = "[dim]Chat - select text + Ctrl+C to copy[/dim]"


class HomeScreen(Screen[Any]):
    """Main home screen with chat, sidebar, and tool log."""

    AUTO_FOCUS = "#prompt-input"

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+h", "show_help", "Help"),
        ("ctrl+s", "show_sessions", "Pick Session"),
        ("ctrl+m", "show_models", "Models"),
        ("escape", "focus_prompt", "Focus ChatInput"),
    ]

    def __init__(
        self,
        agent: Any = None,
        workspace: Path | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._external_agent = agent
        self.agent = agent
        self.workspace = workspace
        self._response = ""
        self._tool_log: list[dict[str, Any]] = []
        self._thinking = False
        self._chat_start = 0.0
        self._initialized = False

    def compose(self) -> ComposeResult:
        """Build the layout."""
        yield Header(show_clock=True, name="TheBigBos", icon="🦾")

        with Horizontal():
            # Main chat area
            with Vertical(id="main-area"):
                yield ResponseArea(
                    id="response-area",
                    highlight=True,
                    markup=True,
                    wrap=True,
                    min_width=40,
                )
                # Tool execution log
                yield ToolLogWidget(id="tool-log", classes="hidden")

            # Sidebar
            with VerticalScroll(id="sidebar"):
                yield SidebarWidget(id="sidebar-info")

        # Prompt area
        with Horizontal(id="prompt-area"):
            yield ChatInput(
                id="prompt-input",
                classes="chat-input",
            )
            yield Button("Send", variant="primary", id="send-btn")

        yield StatusBar(id="status-bar")

    async def on_mount(self) -> None:
        """Called when screen is mounted. Initialize agent."""
        KeymapRegistry.apply_to_screen(self)

        # Initialize agent if needed
        if not self._initialized:
            self._initialized = True
            await self._init_agent()

    async def _init_agent(self) -> None:
        """Initialize the BigBosAgent and set up event handling."""
        response_area = self.query_one("#response-area", ResponseArea)
        input_widget = self.query_one("#prompt-input", ChatInput)

        response_area.write("[dim]Initializing...[/dim]")

        # Create agent if not provided
        if not self.agent and self.workspace:
            from ...core.agent import BigBosAgent
            self.agent = BigBosAgent(self.workspace)

        if self.agent:
            try:
                await asyncio.wait_for(self.agent.initialize(), timeout=10)
            except asyncio.TimeoutError:
                response_area.write("\n[yellow]Init timed out. Agent may be unavailable.[/yellow]")
                self._initialized = True
                input_widget.text = ""
                return
            except Exception as e:
                response_area.write(f"\n[red]Init error: {e}[/red]")
                self.notify(f"Failed to initialize: {e}", severity="error")
                return

            # Set up event callbacks for TUI updates
            self.agent.on_event(self._on_agent_event)

            # Show config
            self._show_config_banner()

            # Show session picker at startup (lazy-load external sessions)
            self.agent._ensure_sessions_imported()
            sessions = self.agent.memory.list_sessions(limit=30)
            if sessions:
                self._show_session_picker_inline(sessions)
            else:
                # No sessions — start fresh
                self.agent.start_session()
                greeting = self.agent.soul.personalize_greeting()
                response_area.write(f"\n[bold cyan]{greeting}[/bold cyan]\n")

            self._update_sidebar()
            input_widget.focus()

        self._initialized = True

    def _on_agent_event(self, event_type: str, data: str) -> None:
        """Handle agent events — bridge to Textual reactive system."""
        response_area = self.query_one("#response-area", ResponseArea)
        tool_log = self.query_one("#tool-log", ToolLogWidget)

        if event_type == "thinking":
            self._thinking = True
            self._update_sidebar()

        elif event_type == "reasoning":
            # Model's reasoning/thinking — already streamed inline via RichLog
            # Just toggle thinking state off
            self._thinking = False
            self._update_sidebar()

        elif event_type == "response":
            self._thinking = False
            self._update_sidebar()

        elif event_type == "tool_executing":
            try:
                tools = json.loads(data)
                for tool in tools:
                    self._tool_log.append({
                        "name": tool["name"],
                        "args": tool.get("args", {}),
                        "status": "running",
                    })
                    response_area.write(f"\n[dim]🔧 {tool['name']}({json.dumps(tool.get('args', {}))[:60]})[/dim]")
                tool_log.tool_entries = list(self._tool_log)
                tool_log.refresh(layout=True)
            except Exception:
                pass

        elif event_type == "tool_result":
            try:
                info = json.loads(data)
                for t in self._tool_log:
                    if t["name"] == info["name"] and t["status"] == "running":
                        t["status"] = "done"
                        t["result"] = info.get("result", "")[:200]
                        break
                tool_log.tool_entries = list(self._tool_log)
                tool_log.refresh(layout=True)
            except Exception:
                pass

        elif event_type == "session_started":
            self._update_sidebar()

        elif event_type == "session_loaded":
            self._update_sidebar()

        elif event_type == "compacted":
            self.notify("Context compacted", title="Memory")

    def _show_config_banner(self) -> None:
        """Show active configuration."""
        response_area = self.query_one("#response-area", ResponseArea)
        if not self.agent:
            return

        lines = [
            "",
            f" Model: [bold]{self.agent.config.active_provider}/{self.agent.config.active_model}[/bold]",
            f" Soul: {self.agent.soul.name}",
            f" Skills: {len(self.agent.skills.list_skills())}",
            f" Providers: {', '.join(self.agent.providers.list_providers())}",
            f" Workspace: {self.workspace or 'current'}",
            "",
        ]
        response_area.write("\n".join(lines))

    def _show_session_picker_inline(self, sessions: list[dict]) -> None:
        """Show interactive session picker with arrow keys."""
        from textual.screen import ModalScreen
        from textual.widgets import ListView, ListItem, Label

        class SessionPicker(ModalScreen[str | None]):
            BINDINGS = [
                ("escape", "dismiss_none", "Cancel"),
                ("d", "delete_session", "Delete"),
                ("r", "rename_session", "Rename"),
            ]

            DEFAULT_CSS = """
            SessionPicker {
                align: center middle;
                background: transparent;
            }
            SessionPicker > Vertical {
                width: 50;
                height: 10;
                max-height: 80%;
                background: #1a1a2e;
                border: thick #00d4ff;
                padding: 1 2;
            }
            """

            def __init__(self, sessions_data, agent):
                super().__init__()
                self.sessions_data = sessions_data
                self.agent = agent

            def compose(self) -> ComposeResult:
                with Vertical():
                    yield Label("[bold cyan]Sessions[/bold cyan]")
                    yield ListView(id="session-list")
                    yield Label("[dim]Enter=select  Esc=close  D=delete  R=rename[/dim]")

            def on_mount(self) -> None:
                list_view = self.query_one("#session-list", ListView)
                current = self.agent.sessions.active
                for s in self.sessions_data[:20]:
                    title = (s.get("title") or "Untitled")[:45]
                    source = s.get("source", "")
                    src_tag = f" [{source}]" if source else ""
                    is_active = current and current.id == s["id"]
                    marker = " *" if is_active else ""
                    list_view.append(ListItem(Label(f"{marker} {title}{src_tag}")))

            def on_list_view_selected(self, event) -> None:
                idx = self.query_one("#session-list", ListView).index
                if idx is not None and idx < len(self.sessions_data):
                    self.dismiss(self.sessions_data[idx]["id"])

            def action_delete_session(self) -> None:
                idx = self.query_one("#session-list", ListView).index
                if idx is not None and idx < len(self.sessions_data):
                    sid = self.sessions_data[idx]["id"]
                    title = self.sessions_data[idx].get("title", "Untitled")
                    self.agent.memory.delete_session(sid)
                    if self.agent.sessions.active and self.agent.sessions.active.id == sid:
                        self.agent.start_session()
                    self.app.notify(f"Deleted: {title[:30]}")
                    self.dismiss("__refresh__")

            def action_rename_session(self) -> None:
                idx = self.query_one("#session-list", ListView).index
                if idx is not None and idx < len(self.sessions_data):
                    sid = self.sessions_data[idx]["id"]
                    old_title = self.sessions_data[idx].get("title", "Untitled")

                    class RenameDialog(ModalScreen[str | None]):
                        BINDINGS = [("escape", "dismiss_none", "Cancel")]
                        def __init__(self, prompt, default):
                            super().__init__()
                            self.prompt = prompt
                            self.default = default
                        def compose(self):
                            yield Label(f"[bold]{self.prompt}[/bold]")
                            yield Input(value=self.default, id="rename-input")
                            yield Label("[dim]Enter=confirm Esc=cancel[/dim]")
                        def on_input_submitted(self, event):
                            self.dismiss(event.value)
                        def action_dismiss_none(self):
                            self.dismiss(None)

                    def on_input(val):
                        if val:
                            self.agent.memory.update_session_title(sid, val)
                            self.app.notify(f"Renamed: {val[:30]}")
                            self.dismiss("__refresh__")

                    self.app.push_screen(RenameDialog(f"Rename:", old_title), callback=on_input)

            def action_dismiss_none(self) -> None:
                self.dismiss(None)

        picker = SessionPicker(sessions, self.agent)
        self.app.push_screen(picker, callback=self._on_session_picked)

    def _on_session_picked(self, session_id: str | None) -> None:
        """Handle session picker result."""
        if session_id == "__refresh__":
            sessions = self.agent.memory.list_sessions(limit=30)
            if sessions:
                self._show_session_picker_inline(sessions)
            return
        if session_id == "__deleted__":
            return
        if session_id:
            if self.agent.continue_session(session_id):
                self._update_sidebar()
                # Show history messages
                self._load_history()
                self.notify(f"Session loaded")
            else:
                self.notify("Failed to load session", severity="error")
        else:
            if not self.agent.sessions.active:
                self.agent.start_session()
                self._update_sidebar()

    def _load_history(self) -> None:
        """Display loaded session history in response area."""
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.clear()
        session = self.agent.sessions.active
        if not session or not session.messages:
            return
        for msg in session.messages:
            if msg.role == "system":
                continue
            if msg.role == "user":
                response_area.write(f"\n[bold yellow]You:[/bold yellow] {msg.content[:500]}")
            elif msg.role == "assistant":
                response_area.write(f"\n[bold cyan]TheBigBos:[/bold cyan] {msg.content[:1000]}")
            elif msg.role == "tool":
                response_area.write(f"\n[dim]Tool: {msg.content[:200]}[/dim]")

    @on(Button.Pressed, "#send-btn")
    @on(ChatInput.Submitted, "#prompt-input")
    async def _on_send(self, event: Button.Pressed | ChatInput.Submitted) -> None:
        """Handle send action."""
        input_widget = self.query_one("#prompt-input", ChatInput)

        if isinstance(event, ChatInput.Submitted):
            user_input = event.text.strip()
        else:
            user_input = input_widget.text.strip()
            if user_input:
                input_widget.clear()

        if not user_input:
            return

        # Handle slash commands locally
        if user_input.startswith("/"):
            await self._handle_command(user_input)
            return

        # Show user message in log
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.write(f"\n[bold yellow]You:[/bold yellow] {user_input}\n")

        # Run agent in background worker so UI stays responsive
        if self.agent:
            self._chat_start = time.time()
            self._thinking = True
            self._response = ""
            self._tool_log = []
            self._update_sidebar()
            self._chat_task = asyncio.create_task(self._run_chat(user_input))

    async def _run_chat(self, user_input: str) -> None:
        """Run chat with streaming response — text appears in real-time."""
        response_area = self.query_one("#response-area", ResponseArea)
        sidebar = self.query_one("#sidebar-info", SidebarWidget)
        try:
            async for chunk in self.agent.stream_chat(user_input):
                response_area.write(chunk)
        except Exception as e:
            error = str(e)[:100]
            response_area.write(f"\n[red]Error: {error}[/red]")
            sidebar.error_msg = error
        finally:
            self._thinking = False
            elapsed = time.time() - getattr(self, "_chat_start", time.time())
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.elapsed = elapsed
            sidebar.error_msg = ""
            self._update_sidebar()

    async def _handle_command(self, cmd: str) -> None:
        """Handle slash commands."""
        response_area = self.query_one("#response-area", ResponseArea)

        if cmd in ("/exit", "/quit", "/q"):
            if self.agent:
                self.agent.shutdown()
            self.app.exit()

        elif cmd == "/help":
            self._show_help()

        elif cmd == "/sessions":
            await self._show_sessions()

        elif cmd == "/models":
            self._show_models()

        elif cmd == "/skills":
            self._show_skills()

        elif cmd == "/clear":
            response_area.clear()

        elif cmd.startswith("/switch "):
            sid = cmd[8:].strip()
            if self.agent and self.agent.switch_session(sid):
                self.notify(f"Switched to session {sid}")
            else:
                self.notify(f"Session {sid} not found", severity="error")

        elif cmd == "/copy":
            self._copy_last_response()
            return
            if self.agent:
                self.agent.config.active_model = cmd[7:].strip()
                self.notify(f"Model: {self.agent.config.active_model}")
                self._update_sidebar()

    def _copy_last_response(self) -> None:
        """Copy last response to clipboard via platform command."""
        import subprocess
        txt = ""
        for msg in (self.agent.sessions.active.messages if self.agent and self.agent.sessions.active else []):
            if msg.role == "assistant" and msg.content:
                txt = msg.content
        if txt:
            if __import__("sys").platform == "win32":
                subprocess.run("clip", input=txt[:5000].encode("utf-8", errors="replace"), check=False)
            else:
                subprocess.run("pbcopy" if __import__("sys").platform == "darwin" else ["xclip", "-sel", "c"],
                             input=txt.encode("utf-8", errors="replace"), check=False)
            self.notify("Copied to clipboard!")
        else:
            self.notify("Nothing to copy", severity="warning")

    def _show_help(self) -> None:
        """Show help in the response area."""
        help_text = """
## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show this help |
| `/exit`, `/q` | Quit TheBigBos |
| `/sessions` | List all sessions |
| `/switch <id>` | Switch to session |
| `/clear` | Clear screen |
| `/models` | List available models |
| `/skills` | List available skills |
| `/model <id>` | Switch active model |
| `/copy` | Copy last response to clipboard |
| `/remember <key>:<value>` | Store a fact |
| `/recall <query>` | Search memories |
"""
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.write(help_text)

    async def _show_sessions(self) -> None:
        """Show interactive session picker with arrow keys."""
        response_area = self.query_one("#response-area", ResponseArea)
        if not self.agent:
            response_area.write("[yellow]Agent not initialized yet. Please wait...[/yellow]")
            return
        try:
            self.agent._ensure_sessions_imported()
        except Exception as e:
            response_area.write(f"[red]Failed to import sessions: {e}[/red]")
            return
        sessions = self.agent.memory.list_sessions(limit=30)
        if not sessions:
            response_area.write("[dim]No sessions found. Start chatting to create one![/dim]")
            return
        self._show_session_picker_inline(sessions)

    def _show_models(self) -> None:
        """Show available models."""
        if not self.agent:
            return
        response_area = self.query_one("#response-area", ResponseArea)
        lines = [" Available Models"]
        lines.append(" ────────────────")
        for pname in self.agent.providers.list_providers():
            models = self.agent.providers.list_models(pname)
            active = pname == self.agent.config.active_provider
            marker = "[bold cyan]>>[/bold cyan]" if active else "  "
            lines.append(f" [{pname}]")
            for m in models:
                active_m = m == self.agent.config.active_model and active
                am = " [green]active[/green]" if active_m else ""
                lines.append(f"   {m}{am}")
        response_area.write("\n".join(lines))

    def _show_skills(self) -> None:
        """Show available skills."""
        if not self.agent:
            return
        response_area = self.query_one("#response-area", ResponseArea)
        skills = self.agent.skills.list_skills()
        if not skills:
            response_area.write("[dim]No skills found.[/dim]")
            return
        lines = [" Skills"]
        lines.append(" ──────")
        for s in skills:
            lines.append(f" • {s['name']}: {s.get('description', '')[:60]}")
        response_area.write("\n".join(lines))

    def _update_sidebar(self) -> None:
        """Refresh sidebar with current session info."""
        sidebar = self.query_one("#sidebar-info", SidebarWidget)
        if not self.agent:
            return

        session = self.agent.sessions.active
        if session:
            sidebar.session_id = session.id
            sidebar.session_title = session.title or "Untitled"
        sidebar.model = self.agent.config.active_model
        sidebar.provider = self.agent.config.active_provider
        sidebar.skill_count = len(self.agent.skills.list_skills())
        sidebar.auto_approve = self.agent.config.auto_approve
        sidebar.thinking = self._thinking

        # Update status bar
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.model = self.agent.config.active_model
        status_bar.provider = self.agent.config.active_provider
        status_bar.context_tokens = sidebar.context_tokens
        if session:
            status_bar.context_tokens = sidebar.context_tokens
        status_bar.mode = sidebar.mode
        status_bar.thinking = self._thinking

        # Determine mode
        model_lower = self.agent.config.active_model.lower()
        if any(m in model_lower for m in ("o1", "o3", "o4", "r1", "thinking")):
            sidebar.mode = "plan"
        else:
            sidebar.mode = "build"

        # Token estimate
        provider = self.agent.providers.active
        if provider and session:
            try:
                tokens = provider.count_tokens(session.to_llm_format())
                sidebar.context_tokens = tokens
            except Exception:
                sidebar.context_tokens = 0

    # Keybinding actions
    def action_show_help(self) -> None:
        self._show_help()

    def action_show_sessions(self) -> None:
        import asyncio
        asyncio.create_task(self._show_sessions())

    def action_show_models(self) -> None:
        self._show_models()

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt-input", ChatInput).focus()

    def action_quit(self) -> None:
        if self.agent:
            self.agent.shutdown()
        self.app.exit()
