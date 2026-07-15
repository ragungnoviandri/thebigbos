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

from rich.align import Align
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text as RichText

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TextArea,
    Switch,
)

from ..keymap import KeymapRegistry
from ...tools.git_utils import GitWorkspace
from ...config.manager import ProviderConfig


class ChatInput(TextArea):
    """Multi-line chat input. Enter=send, Ctrl+J=newline, ↑↓=history, max 3 rows."""

    BINDINGS = [
        ("ctrl+j", "insert_newline", "New Line"),
    ]

    def on_mount(self) -> None:
        self.styles.max_height = 6
        self.border_title = "Enter=Send  Ctrl+J=newline  ↑↓=History"
        self._history_index: int = -1  # -1 = not browsing history
        self._saved_input: str = ""    # draft saved when browsing history

    def clear(self):
        """Clear text and undo history to prevent out-of-bounds undo crashes."""
        self._history_index = -1
        self._saved_input = ""
        result = super().clear()
        self.history.clear()
        return result

    def _get_user_messages(self) -> list[str]:
        """Collect user messages from the active session, newest first."""
        screen = self.screen
        if not hasattr(screen, 'agent') or not screen.agent:
            return []
        session = screen.agent.sessions.active if screen.agent.sessions else None
        if not session:
            return []
        # Reverse chronological, deduplicated, non-empty
        seen = set()
        result = []
        for msg in reversed(session.messages):
            if msg.role == "user" and msg.content.strip():
                stripped = msg.content.strip()
                if stripped not in seen:
                    seen.add(stripped)
                    result.append(stripped)
        return result

    def _on_key(self, event) -> None:
        if event.key == "up":
            history = self._get_user_messages()
            if history:
                cursor_row = self.cursor_location[0]
                if self._history_index == -1 and cursor_row > 0:
                    # Not at first line — let TextArea handle cursor movement
                    super()._on_key(event)
                    return
                if self._history_index == -1:
                    self._saved_input = self.text
                    self._history_index = 0
                elif self._history_index < len(history) - 1:
                    self._history_index += 1
                if self._history_index < len(history):
                    self.load_text(history[self._history_index])
                    self.move_cursor(self.document.end)
                event.stop()
                event.prevent_default()
            else:
                super()._on_key(event)

        elif event.key == "down":
            if self._history_index > 0:
                self._history_index -= 1
                history = self._get_user_messages()
                self.load_text(history[self._history_index])
                self.move_cursor(self.document.end)
                event.stop()
                event.prevent_default()
            elif self._history_index == 0:
                # Back to original draft
                self._history_index = -1
                self.load_text(self._saved_input)
                self._saved_input = ""
                event.stop()
                event.prevent_default()
            else:
                super()._on_key(event)

        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self.clear()
                # re-focus immediately so cursor is ready
                self.focus()
                # Call screen handler directly
                screen = self.screen
                if hasattr(screen, '_handle_chat_input'):
                    import asyncio
                    asyncio.create_task(screen._handle_chat_input(text))
        else:
            # Any other key resets history browsing
            if self._history_index != -1:
                self._history_index = -1
                self._saved_input = ""
            super()._on_key(event)

    def action_insert_newline(self) -> None:
        """Insert newline at cursor."""
        self.insert("\n")


class StatusBar(Static):
    """Bottom status bar — model, context, timing, API debug."""

    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    context_tokens: reactive[int] = reactive(0)
    mode: reactive[str] = reactive("build")
    elapsed: reactive[float] = reactive(0)
    thinking: reactive[bool] = reactive(False)
    done_flash: reactive[bool] = reactive(False)
    api_info: reactive[str] = reactive("")
    api_error: reactive[str] = reactive("")

    _spinner_frame: int = 0
    _think_start: float = 0.0
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def watch_thinking(self, thinking: bool) -> None:
        """Flash 'done' indicator when thinking finishes."""
        if thinking:
            self._think_start = time.time()
            self.done_flash = False
        elif self._think_start > 0:
            # Was thinking, now done — flash the checkmark
            self.done_flash = True
            self._spinner_frame = 0
            self.elapsed = time.time() - self._think_start
            # Clear flash after 3 seconds
            def _clear():
                self.done_flash = False
                self.refresh(layout=False)
            self.set_timer(3.0, _clear)

    def on_mount(self) -> None:
        """Animate spinner & elapsed time while thinking."""
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        """Update spinner frame and live elapsed time."""
        if self.thinking:
            self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
            if self._think_start > 0:
                self.elapsed = time.time() - self._think_start
            self.refresh(layout=False)

    def render(self) -> str:
        lines = []
        # Line 1: standard status
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
            frame = self.SPINNER_FRAMES[self._spinner_frame]
            parts.append(f"[yellow]{frame} thinking...[/yellow]")
        elif self.done_flash:
            parts.append("[green]✓ done[/green]")
        lines.append(" │ ".join(parts))

        # Line 2: API debug (only when there's info)
        if self.api_info:
            lines.append(f" [dim]API: {self.api_info}[/dim]")
        if self.api_error:
            lines.append(f" [red]ERR: {self.api_error}[/red]")
        return "\n".join(lines)

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
    git_branch: reactive[str] = reactive("")
    git_status: reactive[str] = reactive("")
    git_remote: reactive[str] = reactive("")

    def render(self) -> str:
        lines = []
        lines.append("[bold cyan] Info[/bold cyan]")
        lines.append(" ─────────────")
        lines.append("")
        if self.error_msg:
            lines.append(f" [red]Error: {self.error_msg[:60]}[/red]")
            lines.append("")
        if self.thinking:
            lines.append(" [bold yellow]...thinking...[/bold yellow]")
            lines.append("")
        if self.session_id:
            lines.append(f" Session: [dim]{self.session_id}[/dim]")
            title = self.session_title or "Untitled"
            lines.append(f" Title: [bold]{title[:30]}[/bold]")
            lines.append(" [dim]Ctrl+R to rename[/dim]")
        else:
            lines.append(" [dim]No active session[/dim]")
            lines.append(" [dim]Start chatting to create one[/dim]")
        lines.append("")
        lines.append(f" Mode: [bold]{self.mode}[/bold]")
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

        # Git info
        if self.git_branch:
            lines.append("")
            lines.append("[bold cyan] Git[/bold cyan]")
            lines.append(" ─────────────")
            lines.append(f" Branch: [green]{self.git_branch}[/green]")
            lines.append(f" Status: {self.git_status}")
            if self.git_remote:
                lines.append(f" Remote: [dim]{self.git_remote}[/dim]")
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
        self.border_title = "[dim]Click to focus · Shift+arrows to select · Ctrl+C to copy[/dim]"
        self.highlight = True

    @property
    def selected_text(self) -> str:
        """Get currently selected text, if any."""
        try:
            sel = self.selection
            if sel:
                # self.selection returns a Selection or tuple
                start, end = (sel.start, sel.end) if hasattr(sel, 'start') else (sel[0], sel[1])
                if start != end:
                    # Build text from the lines
                    lines = self.lines
                    return "\n".join(lines[start.row:end.row + 1])[start.column:end.column]
        except Exception:
            pass
        return ""

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy text to system clipboard. Returns True on success."""
        import subprocess
        import sys
        if not text:
            return False
        try:
            if sys.platform == "win32":
                subprocess.run("clip", input=text[:10000].encode("utf-8", errors="replace"), check=False)
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=text.encode("utf-8", errors="replace"), check=False)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"],
                               input=text.encode("utf-8", errors="replace"), check=False)
            return True
        except Exception:
            return False


class AddProviderDialog(ModalScreen[str | None]):
    """Modal dialog to add a new model provider.
    
    Returns the provider name on success, None on cancel.
    """

    PRESETS: dict[str, dict] = {
        "anthropic": {
            "label": "Anthropic (Claude)",
            "base_url": "https://api.anthropic.com/v1",
            "models": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-opus-20240229"],
            "default_model": "claude-sonnet-4-20250514",
        },
        "groq": {
            "label": "Groq",
            "base_url": "https://api.groq.com/openai/v1",
            "models": ["llama-3.1-70b", "mixtral-8x7b", "gemma2-9b"],
            "default_model": "llama-3.1-70b",
        },
        "deepseek": {
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "models": ["deepseek-chat", "deepseek-coder"],
            "default_model": "deepseek-chat",
        },
        "together": {
            "label": "Together AI",
            "base_url": "https://api.together.xyz/v1",
            "models": ["meta-llama/Meta-Llama-3.1-405B", "mistralai/Mixtral-8x22B"],
            "default_model": "meta-llama/Meta-Llama-3.1-405B",
        },
        "openrouter": {
            "label": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat"],
            "default_model": "deepseek/deepseek-chat",
        },
        "opencode-zen": {
            "label": "OpenCode Zen",
            "base_url": "https://opencode.ai/zen/v1",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash", "qwen-plus", "qwen-max"],
            "default_model": "deepseek-v4-pro",
        },
        "ollama": {
            "label": "Ollama (local)",
            "base_url": "http://localhost:11434/v1",
            "models": ["llama3.1", "qwen2.5", "deepseek-r1", "codellama"],
            "default_model": "llama3.1",
            "no_api_key": True,
        },
        "__custom__": {
            "label": "Custom...",
            "base_url": "",
            "models": ["gpt-4o", "gpt-4o-mini"],
            "default_model": "gpt-4o",
        },
    }

    def __init__(self, existing_providers: list[str] | None = None):
        super().__init__()
        self._existing = set(existing_providers or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="add-provider-dialog", classes="modal-container"):
            yield Label("[bold reverse]  ✨ Add Provider  [/bold reverse]", id="dialog-title")
            
            with Vertical(id="dialog-body"):
                # Preset selector
                yield Label("[bold]Choose a preset or enter custom:[/bold]")
                preset_options = []
                for key, info in self.PRESETS.items():
                    label = info["label"]
                    if key in self._existing:
                        label += " ✓"
                    preset_options.append((label, key))
                yield Select(preset_options, id="preset-select", prompt="Select preset...", value="__custom__")
                
                yield Label("")  # spacer
                
                # Manual fields
                yield Label("Provider Name:")
                yield Input(id="provider-name-input", placeholder="my-custom-provider")
                
                yield Label("Base URL:")
                yield Input(id="provider-url-input", placeholder="https://api.example.com/v1")
                
                yield Label("API Key:")
                yield Input(id="provider-key-input", password=True, placeholder="sk-... or ${ENV_VAR}")
                
                yield Label("Models (comma-separated):")
                yield Input(id="provider-models-input", placeholder="gpt-4o, gpt-4o-mini")
                
                yield Label("Default Model:")
                yield Input(id="provider-default-model-input", placeholder="gpt-4o")
                
                yield Label("[dim italic]API key can be literal or ${ENV_VAR} reference[/dim italic]")

            with Horizontal(id="dialog-actions"):
                yield Button("Cancel", variant="default", id="cancel-btn")
                yield Button("✨ Add Provider", variant="primary", id="add-provider-btn")

    def on_mount(self) -> None:
        """Focus preset select on open."""
        preset = self.query_one("#preset-select", Select)
        preset.focus()
        # Trigger initial preset (custom)
        self._on_preset_changed("__custom__")

    @on(Select.Changed, "#preset-select")
    def _on_preset_change(self, event: Select.Changed) -> None:
        if event.value and event.value is not Select.NULL and event.value is not Select.BLANK:
            self._on_preset_changed(event.value)

    def _on_preset_changed(self, preset_key: str) -> None:
        """Fill form fields based on selected preset."""
        info = self.PRESETS.get(preset_key, {})
        
        name_input = self.query_one("#provider-name-input", Input)
        url_input = self.query_one("#provider-url-input", Input)
        key_input = self.query_one("#provider-key-input", Input)
        models_input = self.query_one("#provider-models-input", Input)
        default_input = self.query_one("#provider-default-model-input", Input)
        
        if preset_key == "__custom__":
            name_input.value = ""
            url_input.value = ""
            key_input.value = ""
            models_input.value = "gpt-4o, gpt-4o-mini"
            default_input.value = "gpt-4o"
            name_input.disabled = False
            url_input.disabled = False
            models_input.disabled = False
            default_input.disabled = False
            key_input.disabled = False
        else:
            name_input.value = preset_key
            url_input.value = info.get("base_url", "")
            models_input.value = ", ".join(info.get("models", []))
            default_input.value = info.get("default_model", "")
            name_input.disabled = True
            url_input.disabled = True
            models_input.disabled = False  # allow editing
            default_input.disabled = False
            if info.get("no_api_key"):
                key_input.value = ""
                key_input.disabled = True
                key_input.placeholder = "(not needed)"
            else:
                key_input.disabled = False
                key_input.placeholder = "sk-... or ${ENV_VAR}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "add-provider-btn":
            self._save()

    def _save(self) -> None:
        """Validate and save the new provider."""
        name = self.query_one("#provider-name-input", Input).value.strip()
        base_url = self.query_one("#provider-url-input", Input).value.strip()
        api_key = self.query_one("#provider-key-input", Input).value.strip()
        models_str = self.query_one("#provider-models-input", Input).value.strip()
        default_model = self.query_one("#provider-default-model-input", Input).value.strip()

        # Validate
        if not name:
            self.app.notify("Provider name is required", severity="error")
            return
        if name in self._existing:
            self.app.notify(f"Provider '{name}' already exists", severity="warning")
            return
        if not base_url:
            self.app.notify("Base URL is required", severity="error")
            return
        
        models = [m.strip() for m in models_str.split(",") if m.strip()]
        if not models:
            self.app.notify("At least one model is required", severity="error")
            return
        if not default_model:
            default_model = models[0]

        # Store in auth.json
        from ...config.auth import get_auth_manager
        auth = get_auth_manager()
        # Store key if it's literal or env var reference
        if api_key:
            auth.set_key(name, api_key, base_url)

        # Pass result via dismiss
        self._result = {
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
            "default_model": default_model,
        }
        self.dismiss(name)


class HomeScreen(Screen[Any]):
    """Main home screen with chat, sidebar, and tool log."""

    AUTO_FOCUS = "#prompt-input"

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+h", "show_help", "Help"),
        ("ctrl+s", "show_sessions", "Pick Session"),
        ("ctrl+c", "copy_text", "Copy"),
        ("ctrl+r", "rename_session", "Rename Session"),
        ("ctrl+m", "show_models", "Models"),
        ("tab", "toggle_mode", "Toggle Plan/Build"),
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
        self._initialized = False
        self._git: GitWorkspace | None = None
        self._stream_bubble_open = False  # track live assistant bubble state
        self._stream_reasoning_shown = False  # track reasoning bubble for this turn

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
                yield Label("[bold cyan]Sessions[/bold cyan]", id="sidebar-session-label")
                with Horizontal(id="session-controls"):
                    yield Select([], id="session-select", prompt="Select session...")
                    yield Button("🗑", variant="error", id="delete-session-btn", classes="icon-btn")

                # Provider & Model selectors
                yield Label("[bold cyan]Provider[/bold cyan]", id="sidebar-provider-label")
                with Horizontal(id="provider-controls"):
                    yield Select([], id="provider-select", prompt="Provider...")
                    yield Button("+", variant="success", id="add-provider-sidebar-btn", classes="icon-btn")
                yield Label("[bold cyan]Model[/bold cyan]", id="sidebar-model-label")
                yield Select([], id="model-select", prompt="Model...")

                yield SidebarWidget(id="sidebar-info")

                # Git actions in sidebar
                with Horizontal(id="git-actions"):
                    yield Button("📦 Commit", variant="default", id="commit-btn")
                    yield Button("🚀 Push", variant="default", id="push-btn")

        # Prompt area
        with Horizontal(id="prompt-area"):
            # Mode toggle: single button — click or Tab to switch
            with Vertical(id="mode-toggle"):
                yield Button("Plan", variant="warning", id="mode-toggle-btn", classes="mode-btn")
            yield ChatInput(
                id="prompt-input",
                classes="chat-input",
            )
            yield Button("Send", variant="primary", id="send-btn")

        yield StatusBar(id="status-bar")

    def _populate_session_select(self) -> None:
        """Populate the session dropdown with available sessions."""
        select = self.query_one("#session-select", Select)
        if not self.agent:
            return

        self.agent._ensure_sessions_imported()
        sessions = self.agent.memory.list_sessions(limit=200)

        options = [("+ New Session", "__new__")]
        active_session = self.agent.sessions.active
        active_id = active_session.id if active_session else None

        for s in sessions:
            title = (s.get("title") or "Untitled")[:40]
            src = s.get("source", "")
            label = f"{title} [{s['id'][:8]}]"
            if src:
                label += f" ({src})"
            if s["id"] == active_id:
                label = f"► {label}"
            options.append((label, s["id"]))

        if not options:
            options = [("No sessions", "")]

        select.set_options(options)
        # Set current value to active session
        if active_id:
            for label, val in options:
                if val == active_id:
                    select.value = val
                    break

    @on(Select.Changed, "#session-select")
    def _on_session_select_changed(self, event: Select.Changed) -> None:
        """Handle session selection from dropdown."""
        if not event.value or not self.agent or event.value is Select.NULL or event.value is Select.BLANK:
            return
        # New session
        if event.value == "__new__":
            self.agent.start_session()
            response_area = self.query_one("#response-area", ResponseArea)
            response_area.clear()
            banner = self.agent.soul.welcome_banner(
                model=self.agent.config.active_model,
                provider=self.agent.config.active_provider,
                workspace=str(self.workspace),
                skills=len(self.agent.skills.list_skills()),
                tools=len(self.agent.tools.get_tool_names()),
            )
            response_area.write(banner)
            self._update_sidebar()
            self._populate_session_select()
            self.notify("New session started")
            return
        # Don't reload if it's already the active session
        active = self.agent.sessions.active
        if active and active.id == event.value:
            return

        if self.agent.continue_session(event.value):
            self._update_sidebar()
            self._load_history()
            self._populate_session_select()
            self.notify(f"Switched to session: {event.value[:12]}")
        else:
            self.notify("Failed to load session", severity="error")
            self._populate_session_select()  # Reset selection

    # ——— Provider & Model selectors ———

    def _populate_provider_select(self) -> None:
        """Populate provider dropdown with available providers."""
        if not self.agent:
            return
        select = self.query_one("#provider-select", Select)
        providers = self.agent.providers.list_providers()
        options = [(name, name) for name in providers]
        if not options:
            options = [("No providers", "")]
        select.set_options(options)
        # Set to active provider
        active = self.agent.config.active_provider
        if active in providers:
            select.value = active

    def _populate_model_select(self, provider_name: str | None = None) -> None:
        """Populate model dropdown with models for the given provider."""
        if not self.agent:
            return
        select = self.query_one("#model-select", Select)
        provider_name = provider_name or self.agent.config.active_provider
        models = self.agent.providers.list_models(provider_name)
        if not models:
            # If provider config not found, try default config
            cfg = self.agent.config.providers.get(provider_name)
            if cfg and cfg.models:
                models = cfg.models
        options = [(m, m) for m in models]
        if not options:
            options = [("No models", "")]
        select.set_options(options)
        # Set to active model
        active = self.agent.config.active_model
        if active in models:
            select.value = active
        elif options:
            select.value = options[0][1]

    @on(Select.Changed, "#provider-select")
    def _on_provider_select_changed(self, event: Select.Changed) -> None:
        """Handle provider selection — switch provider and repopulate models."""
        if not event.value or not self.agent or event.value is Select.NULL or event.value is Select.BLANK:
            return
        old_provider = self.agent.config.active_provider
        new_provider = event.value
        if new_provider == old_provider:
            return

        # Switch provider
        self.agent.config.active_provider = new_provider
        # Get first model for this provider as default
        models = self.agent.providers.list_models(new_provider)
        if not models:
            cfg = self.agent.config.providers.get(new_provider)
            if cfg and cfg.models:
                models = cfg.models
        if models:
            self.agent.config.active_model = models[0]
            # Also set small model to something reasonable
            if len(models) > 1 and "mini" in models[1].lower():
                self.agent.config.small_model = models[1]
            elif len(models) > 1:
                self.agent.config.small_model = models[1]

        # Repopulate model dropdown
        self._populate_model_select(new_provider)
        self._update_sidebar()
        self.notify(f"Provider: {new_provider} | Model: {self.agent.config.active_model}")

    @on(Select.Changed, "#model-select")
    def _on_model_select_changed(self, event: Select.Changed) -> None:
        """Handle model selection — switch active model."""
        if not event.value or not self.agent or event.value is Select.NULL or event.value is Select.BLANK:
            return
        old_model = self.agent.config.active_model
        new_model = event.value
        if new_model == old_model:
            return

        self.agent.config.active_model = new_model
        self._update_sidebar()
        self.notify(f"Model: {new_model}")

    @on(Button.Pressed, "#add-provider-sidebar-btn")
    async def _on_add_provider_btn(self) -> None:
        """Open the Add Provider dialog."""
        existing = self.agent.providers.list_providers() if self.agent else []
        dialog = AddProviderDialog(existing)
        
        async def on_result(provider_name: str | None):
            if provider_name is None:
                return
            # Get the result data from the dialog
            data = getattr(dialog, '_result', None)
            if not data:
                return
            
            name = data["name"]
            base_url = data["base_url"]
            api_key = data["api_key"]
            models = data["models"]
            default_model = data["default_model"]
            
            # Register provider at runtime
            cfg = ProviderConfig(
                api_key=api_key,
                base_url=base_url,
                models=models,
                default_model=default_model,
            )
            ok = self.agent.providers.register_runtime_provider(name, cfg)
            if ok:
                # Switch to new provider
                self.agent.config.active_provider = name
                self.agent.config.active_model = default_model
                self._populate_provider_select()
                self._populate_model_select(name)
                self._update_sidebar()
                self.notify(f"✨ Provider '{name}' added! ({len(models)} models)")
            else:
                self.notify(f"Provider '{name}' already registered", severity="warning")
        
        self.app.push_screen(dialog, callback=on_result)

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
                input_widget.clear()
                return
            except Exception as e:
                response_area.write(f"\n[red]Init error: {e}[/red]")
                self.notify(f"Failed to initialize: {e}", severity="error")
                return

            # Set up event callbacks for TUI updates
            self.agent.on_event(self._on_agent_event)

            # Lazy-load external sessions for the dropdown
            self.agent._ensure_sessions_imported()

            # Don't create a session yet — wait for first chat message
            # Show rich welcome banner
            sessions = self.agent.memory.list_sessions(limit=200)
            banner = self.agent.soul.welcome_banner(
                model=self.agent.config.active_model,
                provider=self.agent.config.active_provider,
                workspace=str(self.workspace),
                skills=len(self.agent.skills.list_skills()),
                tools=len(self.agent.tools.get_tool_names()),
                sessions=len(sessions),
            )
            response_area.write(banner)

            # Show existing sessions inline so user can pick one
            if sessions:
                response_area.write("[bold cyan]📂 Recent sessions:[/bold cyan]\n")
                for i, s in enumerate(sessions[:10], 1):
                    title = (s.get("title") or "Untitled")[:40]
                    sid = s["id"][:8]
                    src = s.get("source", "")
                    tag = f" [dim]({src})[/dim]" if src else ""
                    response_area.write(f"  [bold]{i}.[/bold] [cyan]{title}[/cyan] [dim]{sid}{tag}[/dim]")
                response_area.write("\n[dim]Select from the sidebar → or just start typing![/dim]\n")
            else:
                response_area.write("[dim]No sessions yet. Just start typing to create one![/dim]\n")

            self._populate_session_select()
            self._populate_provider_select()
            self._populate_model_select()
            self._update_sidebar()
            input_widget.focus()

            # Init git wrapper
            if self.workspace:
                self._git = GitWorkspace(self.workspace)
                self._update_git_status()
                # Also call again when sidebar is fully mounted
                self.set_timer(0.5, self._update_git_status)

        self._initialized = True

    def _on_agent_event(self, event_type: str, data: str) -> None:
        """Handle agent events — bridge to Textual reactive system with WhatsApp-style bubbles."""
        response_area = self.query_one("#response-area", ResponseArea)
        tool_log = self.query_one("#tool-log", ToolLogWidget)

        if event_type == "thinking":
            self._thinking = True
            self._stream_bubble_open = False
            self._stream_reasoning_shown = False
            self._update_sidebar()

        elif event_type == "api_info":
            try:
                info = json.loads(data)
                status_bar = self.query_one("#status-bar", StatusBar)
                status_bar.api_info = f"{info.get('provider','')}/{info.get('model','')} -> {info.get('endpoint','')}"
            except Exception:
                pass

        elif event_type == "api_error":
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.api_error = data[:150]
            status_bar.api_info = ""
            self._thinking = False

        elif event_type == "reasoning":
            # Model's reasoning/thinking — show in dim bubble
            if not self._stream_reasoning_shown and data:
                bubble = self._render_bubble("reasoning", data[:800])
                if bubble:
                    response_area.write(bubble)
                self._stream_reasoning_shown = True
            self._update_sidebar()

        elif event_type == "response":
            # Content arriving during live stream — content handled by _run_chat
            self._update_sidebar()

        elif event_type == "done":
            self._thinking = False
            self._stream_bubble_open = False
            self._stream_reasoning_shown = False
            response_area.write("\n[dim green]✓[/dim green]")
            self._update_sidebar()
            self.query_one("#prompt-input", ChatInput).focus()

        elif event_type == "tool_executing":
            try:
                tools = json.loads(data)
                for tool in tools:
                    self._tool_log.append({
                        "name": tool["name"],
                        "args": tool.get("args", {}),
                        "status": "running",
                    })
                    # Compact tool bubble
                    args_str = json.dumps(tool.get("args", {}))
                    short_args = args_str[:80] + ("..." if len(args_str) > 80 else "")
                    response_area.write(
                        self._render_bubble("tool", f"{tool['name']}({short_args})")
                    )
                tool_log.tool_entries = list(self._tool_log)
                tool_log.refresh(layout=True)
            except Exception:
                pass

        elif event_type == "tool_done":
            try:
                tools = json.loads(data)
                names = [t["name"] for t in tools]
                for t in self._tool_log:
                    if t["name"] in names and t["status"] == "running":
                        t["status"] = "done"
                tool_log.tool_entries = list(self._tool_log)
                tool_log.refresh(layout=True)
                count = len(names)
                suffix = f"  [dim green]✔ {count} tool{'s' if count > 1 else ''} done[/dim green]"
                response_area.write(suffix)
            except Exception:
                pass

        elif event_type == "session_started":
            self._update_sidebar()
            self._populate_session_select()

        elif event_type == "session_loaded":
            self._update_sidebar()
            self._populate_session_select()

        elif event_type == "compacted":
            self.notify(f"Context compacted — {data}" if data else "Context compacted", title="Memory")

    def _show_session_picker_inline(self, sessions: list[dict]) -> None:
        """Show interactive session picker with arrow keys."""
        from textual.screen import ModalScreen
        from textual.widgets import ListView, ListItem, Label

        class SessionPicker(ModalScreen[str | None]):
            BINDINGS = [
                ("escape", "dismiss_none", "Cancel"),
                ("d", "delete_session", "Delete"),
                ("r", "rename_session", "Rename"),
                ("n", "new_session", "New Session"),
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
                    yield Label("[dim]Enter=select  Esc=close  D=delete  R=rename  N=new[/dim]")

            def on_mount(self) -> None:
                list_view = self.query_one("#session-list", ListView)
                current = self.agent.sessions.active
                for s in self.sessions_data[:100]:
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

            def action_new_session(self) -> None:
                self.agent.start_session()
                self.dismiss("__new__")

            def action_dismiss_none(self) -> None:
                self.dismiss(None)

        picker = SessionPicker(sessions, self.agent)
        self.app.push_screen(picker, callback=self._on_session_picked)

    def _on_session_picked(self, session_id: str | None) -> None:
        """Handle session picker result."""
        if session_id == "__refresh__":
            sessions = self.agent.memory.list_sessions(limit=200)
            if sessions:
                self._show_session_picker_inline(sessions)
            self._populate_session_select()
            return
        if session_id == "__deleted__":
            self._populate_session_select()
            return
        if session_id == "__new__":
            response_area = self.query_one("#response-area", ResponseArea)
            response_area.clear()
            banner = self.agent.soul.welcome_banner(
                model=self.agent.config.active_model,
                provider=self.agent.config.active_provider,
                workspace=str(self.workspace),
                skills=len(self.agent.skills.list_skills()),
                tools=len(self.agent.tools.get_tool_names()),
            )
            response_area.write(banner)
            self._update_sidebar()
            self._populate_session_select()
            self.notify("New session started")
            return
        if session_id:
            if self.agent.continue_session(session_id):
                self._update_sidebar()
                self._populate_session_select()
                # Show history messages
                self._load_history()
                self.notify(f"Session loaded")
            else:
                self.notify("Failed to load session", severity="error")
        else:
            if not self.agent.sessions.active:
                self.agent.start_session()
                self._update_sidebar()
                self._populate_session_select()

    # ——— Chat bubbles (WhatsApp-style) ———

    @staticmethod
    def _render_bubble(role: str, content: str) -> Panel | Align | str:
        """Render a WhatsApp-style chat bubble using Rich Panel.

        User → right-aligned yellow, TheBigBos → left cyan, reasoning → dim italic, tool → compact grey.
        """
        if not content.strip():
            return ""

        style_map: dict[str, tuple[str, str, str, str]] = {
            # (title, title_style, border_style, text_style)
            "user":        ("You",        "bold yellow",       "yellow",  ""),
            "assistant":   ("TheBigBos",  "bold cyan",         "cyan",    ""),
            "reasoning":   ("🧠 Thinking", "italic",            "grey70",  "dim italic grey70"),
            "tool":        ("Tool",       "dim",               "grey50",  "dim grey50"),
        }
        title, title_style, border_style, text_style = style_map.get(role, ("", "", "white", ""))

        # Trim content for each type
        limits = {"user": 1000, "assistant": 4000, "reasoning": 800, "tool": 200}
        limit = limits.get(role, 2000)
        text = RichText(content[:limit], style=text_style)

        panel = Panel(
            text,
            title=f"[{title_style}]{title}[/{title_style}]",
            border_style=border_style,
            box=ROUNDED,
            padding=(0, 1),
        )

        if role == "user":
            return Align.right(panel)
        return panel

    def _load_history(self) -> None:
        """Display loaded session history as WhatsApp-style bubbles."""
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.clear()
        session = self.agent.sessions.active
        if not session or not session.messages:
            return

        resume_mode = session.metadata.get("_resume_mode", "full")

        for msg in session.messages:
            if msg.role == "system":
                content = msg.content
                if any(kw in content for kw in ("[Context compacted", "[Showing last", "[Session summary", "[skipped", "tool/reasoning")):
                    response_area.write(f"\n[dim italic]{content}[/dim italic]")
                continue

            if msg.role == "tool" and resume_mode == "clean":
                continue  # skip tools in clean resume mode

            bubble = self._render_bubble(msg.role, msg.content)
            if bubble:
                response_area.write(bubble)

    async def _load_more_history(self) -> None:
        """Load more messages from DB into the current session."""
        response_area = self.query_one("#response-area", ResponseArea)
        session = self.agent.sessions.active
        if not session:
            response_area.write("[yellow]No active session.[/yellow]\n")
            return

        total = session.metadata.get("_total_db_messages", 0)
        loaded = session.metadata.get("_loaded_count", len(session.messages))
        if total == 0 or loaded >= total:
            response_area.write("[dim]All messages already loaded.[/dim]\n")
            return

        # Load more — double the loaded count
        new_limit = min(loaded * 2, total)
        msgs = self.agent.memory.load_messages(session.id, limit=10000)
        if not msgs:
            response_area.write("[yellow]No messages in database.[/yellow]\n")
            return

        # Rebuild session messages from DB
        session.messages = []
        recent = msgs[-new_limit:]
        for m in recent:
            session.messages.append(self.agent._db_to_message(m))

        session.metadata["_loaded_count"] = new_limit
        if new_limit < total:
            session.messages.insert(0, Message(
                role="system",
                content=f"[Showing last {new_limit} of {total} total messages. Use /loadmore to load earlier messages.]",
            ))

        session.messages = self.agent._sanitize_messages(session.messages)
        self._load_history()
        response_area.write(f"\n[green]Loaded {new_limit}/{total} messages.[/green]\n")

    async def _handle_chat_input(self, text: str) -> None:
        """Process user input from chat box."""
        response_area = self.query_one("#response-area", ResponseArea)

        if text.startswith("/"):
            await self._handle_command(text)
            self.query_one("#prompt-input", ChatInput).focus()
            return

        # Show user message as WhatsApp-style bubble (right-aligned)
        user_bubble = self._render_bubble("user", text)
        response_area.write(user_bubble)

        if self.agent:
            self._thinking = True
            self._response = ""
            self._tool_log = []
            self._update_sidebar()
            self._chat_task = asyncio.create_task(self._run_chat(text))

        # Always re-focus chat input after sending
        self.query_one("#prompt-input", ChatInput).focus()

    @on(Button.Pressed, "#send-btn")
    async def _on_send_btn(self) -> None:
        """Send button clicked."""
        input_widget = self.query_one("#prompt-input", ChatInput)
        text = input_widget.text.strip()
        if text:
            input_widget.clear()
            await self._handle_chat_input(text)

    @on(Button.Pressed, "#commit-btn")
    async def _on_commit_btn(self) -> None:
        """Commit button — stage + commit with user-provided message."""
        if not self._git or not self._git.is_repo:
            self.notify("Not a git repository", severity="error")
            return

        if not self._git.has_changes():
            self.notify("Nothing to commit — working tree clean")
            return

        # Show commit message dialog
        msg_result = await self._prompt_commit_message()
        if msg_result is None:
            return  # Cancelled

        ok, result = self._git.stage_all()
        if not ok:
            self.notify(f"Stage failed: {result}", severity="error")
            return
        ok, result = self._git.commit(msg_result)
        if ok:
            short_hash = result.splitlines()[0].strip() if result else "OK"
            self.notify(f"✅ Committed: {short_hash}")
        else:
            self.notify(f"Commit failed: {result}", severity="error")
        self._update_git_status()

    async def _prompt_commit_message(self) -> str | None:
        """Show a dialog to enter a commit message. Returns None if cancelled."""
        import asyncio

        # Get changes summary
        summary = ""
        if self._git:
            try:
                files = self._git.status_porcelain()
                if files:
                    summary = "Changes:\n" + "\n".join(f"  {f}" for f in files[:15])
                    if len(files) > 15:
                        summary += f"\n  ... and {len(files)-15} more"
            except Exception:
                pass

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        agent_ref = self.agent
        git_ref = self._git

        class CommitMessageDialog(ModalScreen[None]):
            def __init__(self, changes_summary: str, mode: str, fut: asyncio.Future):
                super().__init__()
                self._summary = changes_summary
                self._mode = mode
                self._future = fut
                self._generating = False

            def compose(self) -> ComposeResult:
                mode_class = "mode-build" if self._mode == "build" else "mode-plan"
                with Vertical(id="commit-dialog", classes=f"modal-container {mode_class}"):
                    yield Label("[bold reverse]  Commit  [/bold reverse]", id="dialog-title")
                    with Vertical(id="dialog-body"):
                        if self._summary:
                            yield Label(f"[dim]{self._summary}[/dim]")
                        yield Label("Commit message:")
                        yield Input(id="commit-msg-input", placeholder="feat: ...")
                        yield Label("[dim]Enter=Confirm  Esc=Cancel  |  Auto = AI-generated[/dim]")
                    with Horizontal(id="dialog-actions"):
                        yield Button("Cancel", variant="default", id="cancel-btn")
                        yield Button("Auto", variant="warning", id="auto-commit-btn")
                        yield Button("Commit", variant="primary", id="confirm-commit-btn")

            def on_mount(self) -> None:
                self.query_one("#commit-msg-input", Input).focus()

            def on_input_submitted(self, event: Input.Submitted) -> None:
                msg = event.value.strip()
                if msg:
                    self._future.set_result(msg)
                    self.dismiss()

            async def _generate_message(self) -> None:
                """Use the active AI provider to generate a commit message from git diff."""
                input_widget = self.query_one("#commit-msg-input", Input)
                auto_btn = self.query_one("#auto-commit-btn", Button)

                if self._generating:
                    return
                self._generating = True
                auto_btn.disabled = True
                auto_btn.label = "Generating..."

                try:
                    diff_text = git_ref.diff_summary() if git_ref else ""
                    if not diff_text or diff_text.strip().startswith("branch:") and "\ndiff:" not in diff_text:
                        input_widget.value = "chore: update"
                        return

                    from thebigbos.models.provider import Message, ModelOptions
                    provider = agent_ref.providers.active if agent_ref else None
                    if not provider:
                        input_widget.value = "chore: update"
                        return

                    prompt = (
                        "Write a SHORT git commit message for the changes below. "
                        "Use conventional commits: feat:, fix:, chore:, refactor:, docs:, style:, test:.\n"
                        "Rules:\n"
                        "- Single line, max 72 chars, present tense, imperative mood.\n"
                        "- Be SPECIFIC — mention the file or feature changed, not vague like 'update code'.\n"
                        "- If multiple files, summarize the main change, not every file.\n"
                        "- Output ONLY the commit message, no quotes, no markdown, no explanation.\n\n"
                        f"{diff_text}"
                    )

                    response = await provider.chat(
                        [Message(role="user", content=prompt)],
                        [],
                        ModelOptions(
                            model=agent_ref._resolve_small_model() if agent_ref else "deepseek-v4-flash",
                            max_tokens=80,
                        ),
                    )

                    # Don't use error responses as commit messages
                    if response.finish_reason == "error":
                        self.notify(
                            f"AI commit message failed: {response.content[:120]}",
                            severity="warning",
                        )
                        input_widget.value = "chore: update"
                        return

                    msg = response.content.strip().strip('"').strip("'")
                    # Also guard against error-prefixed content that snuck through
                    if msg.startswith("[") and "] " in msg[:30]:
                        input_widget.value = "chore: update"
                        return
                    msg = msg.split("\n")[0].strip()
                    if len(msg) > 100:
                        msg = msg[:97] + "..."
                    input_widget.value = msg or "chore: update"
                except Exception:
                    input_widget.value = "chore: update"
                finally:
                    self._generating = False
                    auto_btn.disabled = False
                    auto_btn.label = "Auto"
                    input_widget.focus()

            def on_button_pressed(self, event: Button.Pressed) -> None:
                if event.button.id == "cancel-btn":
                    self._future.set_result(None)
                    self.dismiss()
                elif event.button.id == "confirm-commit-btn":
                    msg = self.query_one("#commit-msg-input", Input).value.strip()
                    if msg:
                        self._future.set_result(msg)
                        self.dismiss()
                elif event.button.id == "auto-commit-btn":
                    import asyncio
                    asyncio.create_task(self._generate_message())

            def _on_key(self, event) -> None:
                if hasattr(event, 'key') and event.key == "escape":
                    self._future.set_result(None)
                    self.dismiss()

        mode = self.agent.config.mode if self.agent else "build"
        dialog = CommitMessageDialog(summary, mode, future)
        self.app.push_screen(dialog)
        try:
            return await asyncio.wait_for(future, timeout=300)
        except asyncio.TimeoutError:
            return None

    @on(Button.Pressed, "#push-btn")
    async def _on_push_btn(self) -> None:
        """Push button — push to origin. Prompts for remote if not set."""
        if not self._git or not self._git.is_repo:
            self.notify("Not a git repository", severity="error")
            return

        # Remote dibaca langsung dari .git/config project ini
        if not self._git.has_remote():
            await self._prompt_remote_url()
            return

        ok, result = self._git.push()
        if ok:
            self.notify("Pushed! 🚀")
            self._update_git_status()
        else:
            self.notify(f"Push failed: {result[:100]}", severity="error")

    @on(Button.Pressed, "#delete-session-btn")
    async def _on_delete_session_btn(self) -> None:
        """Delete the currently selected session from the dropdown."""
        if not self.agent:
            return
        select = self.query_one("#session-select", Select)
        sid = select.value
        if not sid or sid in (Select.NULL, "__new__", ""):
            self.notify("No session selected to delete", severity="warning")
            return

        # Get session info for confirmation
        sessions = self.agent.memory.list_sessions(limit=200)
        title = next((s.get("title", "Untitled") for s in sessions if s["id"] == sid), sid[:12])

        from ..dialogs import DialogConfirm

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self.agent.memory.delete_session(sid)
            # Also remove from in-memory sessions
            self.agent.sessions.sessions.pop(sid, None)
            if self.agent.sessions.active and self.agent.sessions.active.id == sid:
                self.agent.sessions.active_session_id = None
            self._populate_session_select()
            self._update_sidebar()
            response_area = self.query_one("#response-area", ResponseArea)
            response_area.clear()
            response_area.write(f"[dim]Session '{title}' deleted. Start a new one![/dim]")
            self.notify(f"Deleted: {title[:30]}")

        self.app.push_screen(
            DialogConfirm(
                title="Delete Session",
                message=f"Delete '{title[:40]}'?\nThis cannot be undone.",
            ),
            callback=_on_confirm,
        )

    async def _prompt_remote_url(self) -> None:
        """Show dialog to add git remote (stored in project's .git/config)."""
        from ..dialogs import DialogPrompt

        def _on_remote_set(url: str | None) -> None:
            if not url or not url.strip():
                return
            url = url.strip()
            ok, msg = self._git.set_remote(url)
            if ok:
                self.notify(f"Remote added: {url}")
                self._update_git_status()
            else:
                self.notify(f"Failed: {msg}", severity="error")

        self.app.push_screen(
            DialogPrompt(
                title="Add Git Remote (origin):",
                value="https://github.com/user/repo.git",
            ),
            callback=_on_remote_set,
        )

    def _update_git_status(self) -> None:
        """Update button labels + sidebar with git status indicators."""
        sidebar = self.query_one("#sidebar-info", SidebarWidget)

        if not self._git or not self._git.is_repo:
            sidebar.git_branch = ""
            sidebar.git_status = ""
            sidebar.git_remote = ""
            try:
                self.query_one("#commit-btn", Button).label = "No Repo"
                self.query_one("#push-btn", Button).label = "No Repo"
            except Exception:
                pass
            return

        try:
            commit_btn = self.query_one("#commit-btn", Button)
            push_btn = self.query_one("#push-btn", Button)

            status = self._git.status_summary()
            has_changes = self._git.has_changes()
            has_remote = self._git.has_remote()
            branch = self._git.current_branch()
            remote_url = self._git.get_remote_url() if has_remote else ""

            sidebar.git_branch = branch
            sidebar.git_status = status
            sidebar.git_remote = remote_url[:40] + ("..." if len(remote_url) > 40 else "") if remote_url else ""

            if has_changes:
                commit_btn.label = f"💾 Commit"
                commit_btn.variant = "warning"
            else:
                commit_btn.label = "✓ Commit"
                commit_btn.variant = "success"

            if has_remote:
                push_btn.label = "🚀 Push"
                push_btn.variant = "primary"
            else:
                push_btn.label = "🔗 Set Remote"
                push_btn.variant = "default"
        except Exception:
            pass

    async def _run_chat(self, user_input: str) -> None:
        """Run chat with streaming response — WhatsApp-style live bubbles."""
        response_area = self.query_one("#response-area", ResponseArea)
        sidebar = self.query_one("#sidebar-info", SidebarWidget)
        first_content = True
        try:
            async for chunk in self.agent.stream_chat(user_input):
                if first_content and chunk.strip():
                    # Open live assistant bubble header
                    response_area.write("\n[bold cyan]▌ TheBigBos[/bold cyan]\n")
                    first_content = False
                response_area.write(chunk)
        except Exception as e:
            error = str(e)[:100]
            response_area.write(f"\n[red]❌ Error: {error}[/red]")
            sidebar.error_msg = error
        finally:
            if not first_content:
                response_area.write("\n")  # close bubble spacing
            self._thinking = False
            sidebar.error_msg = ""
            self._update_sidebar()
            self.query_one("#prompt-input", ChatInput).focus()

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

        elif cmd == "/fix":
            await self._fix_session()

        elif cmd == "/compact":
            if self.agent and self.agent.sessions.active:
                sess = self.agent.sessions.active
                before = len(sess.messages)
                response_area.write(f"[bold yellow]Compacting... ({before} msgs)[/bold yellow]\n")
                try:
                    await self.agent._compact_context()
                    after = len(sess.messages)
                    response_area.write(
                        f"[green]Compacted![/green] {before} → {after} messages\n"
                    )
                    self._load_history()
                except Exception as e:
                    response_area.write(f"[red]Compaction failed:[/red] {e}\n")
            else:
                response_area.write("[yellow]No active session to compact.[/yellow]\n")

        elif cmd == "/loadmore":
            await self._load_more_history()

        elif cmd == "/resume":
            # Toggle resume mode
            if self.agent:
                current = self.agent.config.memory.resume_mode
                new_mode = "full" if current == "clean" else "clean"
                self.agent.config.memory.resume_mode = new_mode
                icons = {"clean": "🧹", "full": "📚"}
                response_area.write(
                    f"[green]Resume mode: {icons.get(new_mode, '')} {new_mode}[/green]\n"
                    f"[dim]Reload session dengan /switch untuk menerapkan.[/dim]\n"
                )

        elif cmd.startswith("/config memory"):
            await self._config_memory(cmd)

        elif cmd == "/copy":
            self._copy_last_response()

        elif cmd.startswith("/model "):
            if self.agent:
                self.agent.config.active_model = cmd[7:].strip()
                self.notify(f"Model: {self.agent.config.active_model}")
                self._update_sidebar()

        # —— Memory commands ——
        elif cmd.startswith("/remember "):
            parts = cmd[10:].strip()
            if ":" in parts:
                key, _, value = parts.partition(":")
                key, value = key.strip(), value.strip()
                if self.agent and key and value:
                    await self.agent.remember_fact(key, value)
                    response_area.write(f"[green]Remembered:[/green] {key} = {value}\n")
                else:
                    response_area.write("[yellow]Usage: /remember key:value[/yellow]\n")
            else:
                response_area.write("[yellow]Usage: /remember key:value[/yellow]\n")

        elif cmd.startswith("/recall "):
            query = cmd[8:].strip()
            if self.agent and query:
                results = await self.agent.recall_facts(query)
                if results:
                    response_area.write(f"[bold]Memories for '{query}':[/bold]\n")
                    for r in results:
                        response_area.write(f"  • {r['content'][:200]}\n")
                else:
                    response_area.write(f"[dim]No memories found for '{query}'.[/dim]\n")
            else:
                response_area.write("[yellow]Usage: /recall query[/yellow]\n")

        elif cmd == "/recall":
            # Show all facts
            if self.agent:
                facts = self.agent.memory.get_all_facts()
                if facts:
                    response_area.write("[bold]All remembered facts:[/bold]\n")
                    for k, v in facts.items():
                        response_area.write(f"  • {k} = {v}\n")
                else:
                    response_area.write("[dim]No facts stored yet. Use /remember key:value[/dim]\n")

        # —— Skill learning commands ——
        elif cmd == "/learn":
            response_area.write("[yellow]Usage: /learn <skill-name> [tags:tag1,tag2][/yellow]\n")

        elif cmd.startswith("/learn "):
            args = cmd[7:].strip()
            if self.agent and args:
                response_area.write(f"[bold cyan]Generating skill '{args}' from conversation...[/bold cyan]\n")
                # Check for tags suffix: "/learn topic tags:tag1,tag2"
                topic = args
                tags = ""
                if " tags:" in args:
                    parts = args.rsplit(" tags:", 1)
                    topic = parts[0].strip()
                    tags = parts[1].strip()
                result = await self.agent.learn_skill(topic, tags=tags)
                response_area.write(f"{result}\n")
            else:
                response_area.write("[yellow]Usage: /learn <skill-name> [tags:tag1,tag2][/yellow]\n")

        elif cmd == "/learn-suggest":
            if self.agent:
                response_area.write("[dim]Analyzing conversation for teachable moments...[/dim]\n")
                suggestion = await self.agent.suggest_skill()
                if suggestion:
                    response_area.write(f"{suggestion}\n")
                else:
                    response_area.write("[dim]Nothing new to learn from this session yet. Keep chatting![/dim]\n")

        else:
            # Unknown command
            if cmd.startswith("/"):
                response_area.write(f"[yellow]Unknown command: {cmd}[/yellow]\n")
                response_area.write("[dim]Type /help for available commands[/dim]\n")

    async def _config_memory(self, cmd: str) -> None:
        """View or edit memory configuration."""
        response_area = self.query_one("#response-area", ResponseArea)
        if not self.agent:
            return

        mem = self.agent.config.memory
        args = cmd[14:].strip()  # after "/config memory "

        if not args:
            # Show current config
            icons = {"clean": "🧹", "full": "📚"}
            response_area.write("[bold]Memory Configuration[/bold]\n")
            response_area.write(f"  [cyan]resume_mode:[/cyan] {icons.get(mem.resume_mode, '')} {mem.resume_mode}\n")
            response_area.write(f"  [cyan]session_load_limit:[/cyan] {mem.session_load_limit} messages\n")
            response_area.write(f"  [cyan]auto_load_session:[/cyan] {'✅ on' if mem.auto_load_session else '❌ off'}\n")
            response_area.write(f"  [cyan]session_keep_days:[/cyan] {mem.session_keep_days} (0=keep all)\n")
            response_area.write(f"  [cyan]save_reasoning:[/cyan] {'✅ on' if mem.save_reasoning else '❌ off'}\n")
            response_area.write(f"  [cyan]compaction_threshold:[/cyan] {mem.compaction_threshold:.0%}\n")
            response_area.write(f"  [cyan]max_short_term:[/cyan] {mem.max_short_term} messages\n")
            response_area.write("\n[dim]Usage: /config memory <key> <value>[/dim]\n")
            return

        parts = args.split(maxsplit=1)
        key = parts[0].lower()
        val = parts[1].strip().lower() if len(parts) > 1 else ""

        if key == "load":
            try:
                mem.session_load_limit = int(val)
                response_area.write(f"[green]session_load_limit = {mem.session_load_limit}[/green]\n")
            except ValueError:
                response_area.write("[yellow]Usage: /config memory load <number>[/yellow]\n")

        elif key == "auto":
            if val in ("on", "true", "1", "yes"):
                mem.auto_load_session = True
            elif val in ("off", "false", "0", "no"):
                mem.auto_load_session = False
            else:
                response_area.write("[yellow]Usage: /config memory auto on|off[/yellow]\n")
                return
            response_area.write(f"[green]auto_load_session = {mem.auto_load_session}[/green]\n")

        elif key == "keep":
            try:
                mem.session_keep_days = int(val)
                response_area.write(f"[green]session_keep_days = {mem.session_keep_days}[/green]\n")
            except ValueError:
                response_area.write("[yellow]Usage: /config memory keep <days> (0=keep all)[/yellow]\n")

        elif key == "resume":
            if val in ("clean", "full"):
                mem.resume_mode = val
                response_area.write(f"[green]resume_mode = {mem.resume_mode}[/green]\n")
                response_area.write("[dim]Reload session dengan /switch untuk menerapkan.[/dim]\n")
            else:
                response_area.write("[yellow]Usage: /config memory resume clean|full[/yellow]\n")

        elif key == "reasoning":
            if val in ("on", "true", "1"):
                mem.save_reasoning = True
            elif val in ("off", "false", "0"):
                mem.save_reasoning = False
            else:
                response_area.write("[yellow]Usage: /config memory reasoning on|off[/yellow]\n")
                return
            response_area.write(f"[green]save_reasoning = {mem.save_reasoning}[/green]\n")

        else:
            response_area.write(f"[yellow]Unknown key: {key}[/yellow]\n")
            response_area.write("[dim]Keys: load, auto, keep, resume, reasoning[/dim]\n")

    async def _fix_session(self) -> None:
        """Fix corrupted session messages — strip incomplete tool-call sequences."""
        response_area = self.query_one("#response-area", ResponseArea)
        if not self.agent or not self.agent.sessions.active:
            response_area.write("[yellow]No active session to fix.[/yellow]\n")
            return

        session = self.agent.sessions.active
        before = len(session.messages)
        session.messages = self.agent._sanitize_messages(session.messages)
        after = len(session.messages)
        removed = before - after

        # Persist cleaned state to DB so corruption doesn't come back on reload
        try:
            clean_dicts = [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in (m.tool_calls or [])],
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                }
                for m in session.messages
            ]
            self.agent.memory.resync_messages(session.id, clean_dicts)
        except Exception as e:
            response_area.write(f"[yellow]Memory sync warning: {e}[/yellow]\n")

        if removed > 0:
            response_area.write(f"[green]Fixed! Removed {removed} incomplete message(s).[/green]\n")
            self._load_history()
        else:
            response_area.write("[dim]Session is clean — nothing to fix.[/dim]\n")

    def action_copy_text(self) -> None:
        """Copy selected text (if any) or last response to clipboard."""
        response_area = self.query_one("#response-area", ResponseArea)

        # 1. Try to get selected text from response area
        selected = response_area.selected_text
        if selected:
            if response_area.copy_to_clipboard(selected):
                self.notify("Copied selection!", timeout=1.5)
            else:
                self.notify("Copy failed", severity="warning")
            return

        # 2. Fallback: copy last assistant response
        self._copy_last_response()

    def _copy_last_response(self) -> None:
        """Copy last response to clipboard via platform command."""
        txt = ""
        for msg in (self.agent.sessions.active.messages if self.agent and self.agent.sessions.active else []):
            if msg.role == "assistant" and msg.content:
                txt = msg.content
        if txt:
            response_area = self.query_one("#response-area", ResponseArea)
            if response_area.copy_to_clipboard(txt[:5000]):
                self.notify("Copied last response!", timeout=1.5)
            else:
                self.notify("Copy failed", severity="warning")
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
| `/fix` | Repair corrupted session (after crash) |
| `/compact` | Manually compact long conversations |
| `/loadmore` | Load more messages from DB |
| `/resume` | Toggle resume mode: clean / full |
| `/config memory` | View/edit memory settings |
| `/copy` | Copy last response to clipboard |
| `/remember <key>:<value>` | Store a persistent fact |
| `/recall [query]` | Search memories (or show all) |
| `/learn <name> [tags:t1,t2]` | Save conversation as reusable SKILL.md |
| `/learn-suggest` | Auto-detect skill from current session |
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
        sessions = self.agent.memory.list_sessions(limit=200)
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

        # Determine mode — use config mode, not model name
        sidebar.mode = self.agent.config.mode

        # Update mode button visuals
        self._update_mode_buttons()

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

    def action_rename_session(self) -> None:
        """Rename the current session title."""
        if not self.agent or not self.agent.sessions.active:
            self.notify("No active session to rename", severity="warning")
            return

        session = self.agent.sessions.active
        old_title = session.title or "Untitled"

        from textual.screen import ModalScreen
        from textual.widgets import Input, Label

        class RenameDialog(ModalScreen[str | None]):
            BINDINGS = [("escape", "dismiss_none", "Cancel")]
            DEFAULT_CSS = """
            RenameDialog {
                align: center middle;
                background: transparent;
            }
            RenameDialog > Vertical {
                width: 50;
                height: auto;
                background: #1a1a2e;
                border: thick #00d4ff;
                padding: 1 2;
            }
            """
            def __init__(self, prompt: str, default: str):
                super().__init__()
                self._prompt = prompt
                self._default = default
            def compose(self):
                from textual.containers import Vertical
                with Vertical():
                    yield Label(f"[bold]{self._prompt}[/bold]")
                    yield Input(value=self._default, id="rename-input")
                    yield Label("[dim]Enter=confirm  Esc=cancel[/dim]")
            def on_input_submitted(self, event):
                val = event.value.strip()
                if val:
                    self.dismiss(val)
                else:
                    self.dismiss(None)
            def action_dismiss_none(self):
                self.dismiss(None)

        def _on_renamed(new_title: str | None):
            if new_title and new_title != old_title:
                self.agent.memory.update_session_title(session.id, new_title)
                session.title = new_title
                self._update_sidebar()
                self._populate_session_select()
                self.notify(f"Session renamed: {new_title[:30]}")

        self.app.push_screen(RenameDialog("Rename session:", old_title), callback=_on_renamed)

    def action_show_models(self) -> None:
        self._show_models()

    def action_toggle_mode(self) -> None:
        """Toggle between plan and build mode."""
        if not self.agent:
            return
        current = self.agent.config.mode
        new_mode = "build" if current == "plan" else "plan"
        self.agent.config.mode = new_mode
        # Propagate to tool registry — hard-blocks write tools in PLAN mode
        self.agent.tools.mode = new_mode
        self._update_mode_buttons()
        self._update_sidebar()
        self.notify(f"Mode: {new_mode.upper()} — {'read/write' if new_mode == 'build' else 'read-only'}")

    @on(Button.Pressed, "#mode-toggle-btn")
    def _on_mode_toggle(self) -> None:
        """Toggle between plan and build mode on button click."""
        self.action_toggle_mode()

    def _update_mode_buttons(self) -> None:
        """Update mode toggle button label and color to match current mode."""
        if not self.agent:
            return
        mode = self.agent.config.mode
        btn = self.query_one("#mode-toggle-btn", Button)
        btn.label = mode.upper()
        
        # Build = blue (primary), Plan = orange (warning)
        if mode == "build":
            btn.remove_class("mode-plan")
            btn.add_class("mode-build")
        else:
            btn.remove_class("mode-build")
            btn.add_class("mode-plan")

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt-input", ChatInput).focus()

    def action_quit(self) -> None:
        if self.agent:
            self.agent.shutdown()
        self.app.exit()
