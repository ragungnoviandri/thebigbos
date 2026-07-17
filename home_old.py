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
from textual import events
from textual.app import ComposeResult
from textual.css.query import NoMatches
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
from ...models.provider import Message as ProviderMessage
from ... import get_version_string, get_build_number

from rich.markup import escape as _rich_escape
import re


def _strip_markup(text: str) -> str:
    """Strip Rich markup tags to get plain text length."""
    return re.sub(r"\[/?[^\]]*\]", "", text)


class UpdateAvailable(Message):
    """Posted when user clicks version label while update is available."""

    def __init__(self, new_version: str = "", changelog: str = "") -> None:
        super().__init__()
        self.new_version = new_version
        self.changelog = changelog


class VersionLabel(Button):
    """Clickable version display — shows update dialog on click."""

    version: reactive[str] = reactive("")
    update_available: reactive[bool] = reactive(False)
    _latest_version: str = ""
    _changelog: str = ""

    def __init__(self, id: str | None = "sidebar-version", **kwargs):
        super().__init__(id=id, **kwargs)
        self.can_focus = True

    def on_button_pressed(self) -> None:
        """Always show version/update dialog on press."""
        if self.update_available:
            self.post_message(UpdateAvailable(self._latest_version, self._changelog))
        else:
            self.post_message(UpdateAvailable("", ""))  # Show "checking" or "up-to-date"

    def render(self) -> str:
        if self.update_available:
            dot = "[bold blue]●[/bold blue]"
        else:
            dot = "[bold green]●[/bold green]"
        return f" {dot} [dim]de BigBos {self.version}[/dim]"


class ChatInput(TextArea):
    """Multi-line chat input. Enter=send, Ctrl+J=newline, ↑↓=history, max 3 rows."""

    BINDINGS = [
        ("ctrl+j", "insert_newline", "New Line"),
    ]

    def on_mount(self) -> None:
        self.styles.max_height = 6
        self._history_index: int = -1
        self._saved_input: str = ""

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


# Regex to strip Rich markup tags for plain-text length calculation
import re as _re
_MARKUP_TAG = _re.compile(r"\[/?[^\]]*\]")


def _strip_markup(text: str) -> str:
    return _MARKUP_TAG.sub("", text)


class StatusBar(Static):
    """Bottom status bar — 3-column: [indicator] dir | provider/model | stats."""

    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    context_tokens: reactive[int] = reactive(0)
    context_limit: reactive[int] = reactive(0)
    total_cost: reactive[float] = reactive(0.0)
    mode: reactive[str] = reactive("build")
    elapsed: reactive[float] = reactive(0)
    thinking: reactive[bool] = reactive(False)
    done_flash: reactive[bool] = reactive(False)
    api_info: reactive[str] = reactive("")
    api_error: reactive[str] = reactive("")
    git_info: reactive[str] = reactive("")
    workspace: reactive[str] = reactive("")

    _spinner_frame: int = 0
    _think_start: float = 0.0
    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def watch_thinking(self, thinking: bool) -> None:
        if thinking:
            self._think_start = time.time()
            self.done_flash = False
        elif self._think_start > 0:
            self.done_flash = True
            self._spinner_frame = 0
            self.elapsed = time.time() - self._think_start
            self.set_timer(3.0, lambda: self._clear_done())

    def _clear_done(self) -> None:
        self.done_flash = False
        self.refresh(layout=False)

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self.thinking:
            self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
            if self._think_start > 0:
                self.elapsed = time.time() - self._think_start
            self.refresh(layout=False)

    def render(self) -> str:
        # ── Left: [spinner/checkmark] workspace ──
        if self.thinking:
            frame = self.SPINNER_FRAMES[self._spinner_frame]
            indicator = f"[yellow]{frame}[/yellow] thinking"
        elif self.done_flash:
            indicator = "[green]✓ Done[/green]"
        elif self.api_error:
            indicator = f"[red]✗ {self.api_error[:20]}[/red]"
        else:
            indicator = "[dim]✓ Ready[/dim]"

        ws = self.workspace or self.git_info or "~"
        left = f"{indicator}  [dim]{ws}[/dim]"

        # ── Center: provider/model ──
        center = ""
        if self.provider:
            center = f"[primary]{self.provider}[/primary]/[secondary]{self.model}[/secondary]"

        # ── Right: elapsed | ctx tokens (%) | cost ──
        right_parts = []
        if self.thinking or self.done_flash:
            right_parts.append(f"[dim]{self.elapsed:.0f}s[/dim]")

        if self.context_tokens > 0:
            limit = self.context_limit or 128000
            pct = min(100, int(self.context_tokens / limit * 100))
            pct_color = "[red]" if pct > 80 else "[yellow]" if pct > 50 else ""
            pct_end = "[/red]" if pct > 80 else "[/yellow]" if pct > 50 else ""
            right_parts.append(f"[dim]ctx {self.context_tokens:,}/{limit:,} {pct_color}({pct}%){pct_end}[/dim]")

        if self.total_cost > 0:
            right_parts.append(f"[green]${self.total_cost:.2f} spent[/green]")

        right = "  ".join(right_parts) if right_parts else ""

        # ── 3-column layout using container width ──
        width = max(80, self.size.width)
        third = width // 3

        # Strip Rich markup for length calculation
        left_plain = _strip_markup(left)
        center_plain = _strip_markup(center)
        right_plain = _strip_markup(right)

        # Left: pad to fill first third
        left_pad = max(0, third - len(left_plain))
        # Center: pad both sides to fill middle third
        center_pad = max(0, third - len(center_plain))
        center_left = center_pad // 2
        center_right = center_pad - center_left
        # Right: pad to fill last third
        right_pad = max(0, third - len(right_plain))

        return f"{left}{' ' * left_pad}{' ' * center_left}{center}{' ' * center_right}{' ' * right_pad}{right}"


class SidebarWidget(Static):
    """Session info sidebar."""

    session_id: reactive[str] = reactive("")
    session_title: reactive[str] = reactive("Untitled")
    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    context_tokens: reactive[int] = reactive(0)
    context_limit: reactive[int] = reactive(0)
    total_cost: reactive[float] = reactive(0.0)
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
        lines.append(f"[bold #fab283] de BigBos[/bold #fab283]")
        lines.append(f" [dim]#{self.session_id or '---'}[/dim]")
        lines.append("")
        if self.error_msg:
            lines.append(f" [red]{self.error_msg[:60]}[/red]")
            lines.append("")
        if self.thinking:
            lines.append(" [yellow]⠋ thinking...[/yellow]")
            lines.append("")
        if self.session_id:
            title = self.session_title or "Untitled"
            lines.append(f" [bold]{title[:35]}[/bold]")
            lines.append("")
        else:
            lines.append(" [dim]No active session[/dim]")
            lines.append("")

        # Mode: BUILD (blue), PLAN (orange)
        color = "#5c9cf5" if self.mode == "build" else "#fab283"
        lines.append(f" [bold {color}]{self.mode.upper()}[/bold {color}]")
        lines.append("")

        limit = self.context_limit or 128000
        pct = min(100, int(self.context_tokens / limit * 100)) if self.context_tokens > 0 else 0
        pct_color = "[red]" if pct > 80 else "[yellow]" if pct > 50 else ""
        pct_end = "[/red]" if pct > 80 else "[/yellow]" if pct > 50 else ""
        bar = self._make_bar(pct)
        lines.append(" Context")
        lines.append(f"  [dim]{self.context_tokens:,}[/dim]/[bold]{limit:,}[/bold] tokens")
        lines.append(f"  {pct_color}{bar} {pct}% used{pct_end}")
        if self.total_cost > 0:
            lines.append(f"  ${self.total_cost:,.4f} spent")
        else:
            lines.append(f"  [dim]$0 spent[/dim]")
        lines.append("")

        if self.skill_count:
            lines.append(f" Skills: {self.skill_count}")
        if self.auto_approve:
            lines.append(" Auto: [yellow]ON[/yellow]")
        if self.git_branch:
            lines.append("")
            lines.append(f"[bold #5c9cf5] Git[/bold #5c9cf5]")
            lines.append(f" [green]{self.git_branch}[/green] {self.git_status}")
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


class ShortcutsWidget(Static):
    """Command palette reference in sidebar."""

    def render(self) -> str:
        shortcuts = [
            # Title
            ("[bold #5c9cf5]⌘ Command Palette[/bold #5c9cf5]", ""),
            ("", ""),
            # Chat
            ("[bold #fab283]Chat[/bold #fab283]", ""),
            ("Enter", "Send"),
            ("Ctrl+J", "New Line"),
            ("↑ / ↓", "History"),
            ("", ""),
            # Nav
            ("[bold #5c9cf5]Navigate[/bold #5c9cf5]", ""),
            ("Esc", "Focus Input"),
            ("Tab", "Plan ⇄ Build"),
            ("Ctrl+P", "Command Palette"),
            ("Ctrl+S", "Sessions"),
            ("Ctrl+M", "Models"),
            ("Ctrl+H", "Help"),
            ("", ""),
            # Actions
            ("[bold #a0d2a0]Actions[/bold #a0d2a0]", ""),
            ("Ctrl+C", "Copy Selection"),
            ("Ctrl+R", "Rename"),
            ("Ctrl+Q", "Quit"),
            ("Shift+Drag", "Select Text"),
        ]

        lines = []
        for key, desc in shortcuts:
            if not key and not desc:
                lines.append("")
            elif not desc:
                lines.append(key)
            else:
                lines.append(f" [dim]{key:<14}[/dim] [dim italic]{desc}[/dim italic]")
        return "\n".join(lines)


class ResponseArea(RichLog):
    """Rich text area for model responses — selectable + copyable, never steals focus."""

    def on_mount(self) -> None:
        self.can_focus = False  # Focus stays on chat input
        self.highlight = True

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


class SettingsDialog(ModalScreen[None]):
    """Settings dialog with General + Skills tabs."""
     # 1. TARUH CSS KHUSUS DI SINI AGAR TOMBOL BERADA DI KANAN BAWAH
    DEFAULT_CSS = """
    #settings-dialog {
        width: 80%;
        height: auto;
        border: solid green;
        background: $surface;
        padding: 1;
    }

    #settings-actions {
        width: 100%;                  /* Wajib 100% agar memenuhi lebar dialog */
        height: auto;
        align-horizontal: right;      /* Menggeser penampung/container ke kanan */
        margin-top: 1;                /* Memberi jarak atas agar tidak menempel */
    }

    #settings-actions Button {
        margin-left: 1;               /* Memberi jarak antar tombol */
    }

    .provider-row {
        padding: 0 1;
    }
    """

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, home_screen: "HomeScreen"):
        super().__init__()
        self._home = home_screen
        self._skill_switches: dict[str, "Switch"] = {}

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical, Horizontal, VerticalScroll
        from textual.widgets import Label as ModalLabel, Button as ModalButton, Input, Select
        from textual.widgets import TabbedContent, TabPane, Switch

        with Vertical(id="settings-dialog", classes="modal-container"):
            yield ModalLabel(" ⚙ Settings ", id="dialog-title")
            with TabbedContent(id="settings-tabs"):
                # Tab 1: AI Provider
                with TabPane("🤖 AI Provider", id="tab-provider"):
                    yield ModalLabel("")
                    yield ModalLabel("[bold]Current Model[/bold]")
                    agent = self._home.agent
                    if agent:
                        yield ModalLabel(f"  {agent.config.active_provider} / {agent.config.active_model}", id="current-model-label")
                    else:
                        yield ModalLabel("  [dim]No agent loaded[/dim]", id="current-model-label")
                    yield ModalLabel("")
                    yield ModalLabel("[bold]Providers[/bold]")
                    # Provider rows — inline in compose
                    if agent and agent.config.providers:
                        for name, cfg in agent.config.providers.items():
                            is_active = name == agent.config.active_provider
                            marker = "[green]●[/green]" if is_active else "○"
                            models = cfg.models if isinstance(cfg.models, list) else []
                            model_options = [(m, m) for m in models] if models else [("none", "")]
                            active_model = cfg.default_model or (models[0] if models else "")
                            with Horizontal(id=f"provider-row-{name}", classes="provider-row"):
                                yield ModalLabel(f"  {marker} [bold]{name}[/bold]", id=f"plabel-{name}")
                                yield Select(model_options, prompt="Model", id=f"model-{name}", value=active_model)
                                yield ModalButton("✎", variant="default", id=f"edit-provider-btn-{name}", classes="icon-btn")
                    else:
                        yield ModalLabel("  [dim]No providers configured[/dim]")
                    yield ModalLabel("")
                    with Horizontal():
                        yield ModalButton("+ Add Provider", variant="success", id="add-provider-btn")

                # Tab 2: Skills
                with TabPane("🛠 Skills", id="tab-skills"):
                    yield ModalLabel("")
                    yield ModalLabel("[bold]Enable / Disable Skills[/bold]")
                    yield ModalLabel("[dim]Toggle individual skills. Disabled skills are hidden from the AI.[/dim]")
                    yield ModalLabel("")
                    yield VerticalScroll(id="skill-toggle-list")

            yield ModalLabel("")
            with Horizontal(id="settings-actions"):
                yield ModalButton("💾 Save & Close", variant="primary", id="settings-save-btn")
                yield ModalButton("Cancel", variant="default", id="settings-cancel-btn")

    def on_mount(self) -> None:
        """Populate skill toggles."""
        self._populate_skill_toggles()

    @on(events.Click, ".provider-row")
    def _on_provider_row_click(self, event: events.Click) -> None:
        """Click provider row → switch active provider."""
        # Ignore clicks on buttons, selects, or inputs inside the row
        if isinstance(event.widget, (Button, Select, Input)):
            return
        # Find the provider row (closest with id starting with provider-row-)
        target = event.widget
        while target and target.parent:
            if target.id and target.id.startswith("provider-row-"):
                name = target.id.replace("provider-row-", "")
                self._switch_provider(name)
                return
            target = target.parent

    def _switch_provider(self, name: str) -> None:
        """Switch active provider and update UI markers."""
        agent = self._home.agent
        if not agent or name not in agent.config.providers:
            return
        cfg = agent.config.providers[name]
        agent.config.active_provider = name
        agent.config.active_model = cfg.default_model or (cfg.models[0] if cfg.models else "")

        # Update "Current Model" label
        try:
            current_label = self.query_one("#current-model-label", Label)
            current_label.update(f"  {name} / {agent.config.active_model}")
        except NoMatches:
            pass

        # Update all provider row markers
        for row_name in agent.config.providers:
            try:
                label = self.query_one(f"#plabel-{row_name}", Label)
                marker = "[green]●[/green]" if row_name == name else "○"
                label.update(f"  {marker} [bold]{row_name}[/bold]")
            except NoMatches:
                pass

    @on(Button.Pressed, "#add-provider-btn")
    async def _on_add_provider_dialog(self) -> None:
        """Open the Add Provider dialog, register provider, then refresh."""
        agent = self._home.agent
        if not agent:
            return
        existing = list(agent.config.providers.keys())
        dialog = AddProviderDialog(existing)
        worker = self.run_worker(
            self.app.push_screen_wait(dialog),
            exclusive=True
        )
        result = await worker.wait()

        if result:
            # Register provider at runtime
            data = getattr(dialog, '_result', None)
            if data:
                from ...config.models import ProviderConfig
                cfg = ProviderConfig(
                    api_key=data["api_key"],
                    base_url=data["base_url"],
                    models=data["models"],
                    default_model=data["default_model"],
                )
                ok = agent.providers.register_runtime_provider(data["name"], cfg)
                if ok:
                    agent.config.active_provider = data["name"]
                    agent.config.active_model = data["default_model"]
                    agent.notify(f"✨ Provider '{data['name']}' added!")

            self.dismiss(None)
            await asyncio.sleep(0.1)
            # Re-open settings to show new provider
            worker2 = self.run_worker(self._home._show_settings(), exclusive=True)
            await worker2.wait()

    def _populate_skill_toggles(self) -> None:
        """Add switch toggles for every skill — grouped by category."""
        from textual.containers import Horizontal
        from textual.widgets import Label as ModalLabel, Switch

        skill_list = self.query_one("#skill-toggle-list", VerticalScroll)
        agent = self._home.agent
        if not agent:
            return

        skills = agent.skills.list_skills()

        if not skills:
            skill_list.mount(ModalLabel("[dim]No skills found.[/dim]"))
            return

        # Group by category
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for s in skills:
            cat = s.get("category", "Uncategorized")
            groups[cat].append(s)

        # Sort: enabled-first categories first
        sorted_cats = sorted(groups.keys(), key=lambda c: (
            -sum(1 for s in groups[c] if s.get("enabled", True)),
            c,
        ))

        for cat in sorted_cats:
            items = groups[cat]
            # Category header
            enabled_count = sum(1 for s in items if s.get("enabled", True))
            skill_list.mount(ModalLabel(
                f"\n[bold #5c9cf5]{cat}[/bold #5c9cf5]  [dim]({enabled_count}/{len(items)})[/dim]"
            ))
            # Sort items: enabled first
            items.sort(key=lambda s: (not s.get("enabled", True), s.get("name", "")))
            for s in items:
                name = s.get("name", "")
                desc = s.get("description", "")[:80]
                enabled = s.get("enabled", True)

                switch = Switch(value=enabled, id=f"skill-switch-{name}")
                self._skill_switches[name] = switch

                status_icon = "[green]✓[/green]" if enabled else "[dim]✗[/dim]"
                label_text = f"  {status_icon} {name}"
                if desc:
                    label_text += f"\n     [dim italic]{desc[:60]}[/dim italic]"

                # Mount row first, then children
                row = Horizontal(classes="skill-row")
                skill_list.mount(row)
                row.mount(ModalLabel(label_text))
                row.mount(switch)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save-btn":
            self._save_settings()
            self.dismiss(None)
        elif event.button.id == "settings-cancel-btn":
            self.dismiss(None)
        elif event.button.id and event.button.id.startswith("edit-provider-btn-"):
            provider_name = event.button.id.replace("edit-provider-btn-", "")
            self._edit_provider(provider_name)

    @on(Select.Changed)
    def _on_provider_model_changed(self, event: Select.Changed) -> None:
        """Handle model change in provider list."""
        if not event.value or event.value is Select.NULL or event.value is Select.BLANK:
            return
        agent = self._home.agent
        if not agent or not event.control.id or not event.control.id.startswith("model-"):
            return
        provider_name = event.control.id.replace("model-", "")
        cfg = agent.config.providers.get(provider_name)
        if cfg:
            cfg.default_model = str(event.value)
        if provider_name == agent.config.active_provider:
            agent.config.active_model = str(event.value)

    def _edit_provider(self, name: str) -> None:
        """Open dialog to edit provider endpoint & API key."""
        agent = self._home.agent
        if not agent:
            return
        cfg = agent.config.providers.get(name)
        if not cfg:
            return
        self.run_worker(self._show_edit_provider(name, cfg), exclusive=True)

    async def _show_edit_provider(self, name: str, cfg: 'ProviderConfig') -> None:
        """Show edit dialog for a provider."""
        from textual.containers import Vertical, Horizontal
        from textual.widgets import Label as ModalLabel, Button as ModalButton, Input
        from textual.screen import ModalScreen

        class EditProviderDialog(ModalScreen[dict | None]):
            def compose(self2):
                with Vertical(id="edit-provider-dialog", classes="modal-container"):
                    yield ModalLabel(f" ✏ Edit Provider: {name} ", id="dialog-title")
                    yield ModalLabel("")
                    yield ModalLabel("[bold]Endpoint URL[/bold]")
                    yield Input(value=cfg.base_url or "", id="edit-endpoint")
                    yield ModalLabel("")
                    yield ModalLabel("[bold]API Key[/bold]")
                    yield Input(value=cfg.api_key or "", password=True, id="edit-apikey")
                    yield ModalLabel("")
                    with Horizontal():
                        yield ModalButton("💾 Save", variant="primary", id="edit-save-btn")
                        yield ModalButton("Cancel", variant="default", id="edit-cancel-btn")

            def on_button_pressed(self2, event: Button.Pressed):
                if event.button.id == "edit-save-btn":
                    endpoint = self2.query_one("#edit-endpoint", Input).value
                    apikey = self2.query_one("#edit-apikey", Input).value
                    self2.dismiss({"endpoint": endpoint, "apikey": apikey})
                elif event.button.id == "edit-cancel-btn":
                    self2.dismiss(None)

        dialog = EditProviderDialog()
        result = await self.app.push_screen_wait(dialog)
        if result:
            cfg.base_url = result["endpoint"]
            cfg.api_key = result["apikey"]
            # Persist to auth.json
            from ...config.auth import get_auth_manager
            auth = get_auth_manager()
            auth.set_key(name, result["apikey"], result["endpoint"])
            # Re-open settings to show changes
            self.dismiss(None)
            await asyncio.sleep(0.1)
            await self._home._show_settings()
            self._home.notify(f"✅ Provider '{name}' updated!")

    def _save_settings(self) -> None:
        """Write settings back to config."""
        agent = self._home.agent
        if not agent:
            return

        # Skills
        disabled = set()
        for name, switch in self._skill_switches.items():
            if not switch.value:
                disabled.add(name)
        agent.config.skills.disabled_skills = list(disabled)
        agent.skills.disabled_skills = disabled
        agent.skills._scanned = False

        # Persist to global config
        from pathlib import Path
        config_path = Path.home() / ".config" / "deBigBos" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        agent.config_manager.save(agent.config, config_path)

        self._home.notify("✅ Settings saved!", severity="success")

    def action_close(self) -> None:
        self.dismiss(None)


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
            "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2", "qwen-plus", "qwen-max"],
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
            yield Label(" ✨ Add Provider ", id="dialog-title")
            
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
        ("ctrl+a", "select_all", "Select All"),
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
        self._cancelled = False
        self._chat_task: asyncio.Task | None = None
        self._initialized = False
        self._git: GitWorkspace | None = None
        self._session_started = False
        self._stream_bubble_open = False
        self._stream_reasoning_shown = False
        self._reasoning_start = 0.0

    def compose(self) -> ComposeResult:
        """Build the layout."""
        yield Header(show_clock=True, name="de BigBos")

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
                with Horizontal(id="sidebar-header"):
                    yield Label("[bold #fab283]Sessions[/bold #fab283]", id="sidebar-session-label")
                    yield Button("⚙", variant="default", id="settings-btn", classes="icon-btn")
                with Horizontal(id="session-controls"):
                    yield Select([], id="session-select", prompt="Select session...")
                    yield Button("🗑", variant="error", id="delete-session-btn", classes="icon-btn")

                # Session
                yield SidebarWidget(id="sidebar-info")

                # Keyboard shortcuts reference
                yield ShortcutsWidget(id="sidebar-shortcuts")

                # Version — fixed at bottom, clickable
                yield VersionLabel(id="sidebar-version")

                # Git actions in sidebar
                with Horizontal(id="git-actions"):
                    yield Button("📦 Commit", variant="default", id="commit-btn")
                    yield Button("🚀 Push", variant="default", id="push-btn")

        # Prompt area — OpenCode-style: mode toggle | input field | send
        with Horizontal(id="prompt-area"):
            with Vertical(id="mode-toggle"):
                yield Button("BUILD", variant="primary", id="mode-toggle-btn", classes="mode-btn mode-build")
            yield ChatInput(
                id="prompt-input",
                classes="chat-input",
            )
            yield Button("⏎", variant="primary", id="send-btn")

        yield StatusBar(id="status-bar")

        self._session_started = False

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

    async def on_mount(self) -> None:
        """Called when screen is mounted. Initialize agent."""
        KeymapRegistry.apply_to_screen(self)

        # Init version display
        self._init_version_label()
        # Background update check
        asyncio.create_task(self._check_for_updates())

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
            response_area.write("[bold cyan]📂 Sessions:[/bold cyan]\n")
            response_area.write("  [dim]*[/dim] [@click=new_session][green]🆕 New Session...[/green][/]\n")
            if sessions:
                for i, s in enumerate(sessions[:10], 1):
                    title = (s.get("title") or "Untitled")[:40]
                    sid = s["id"][:8]
                    src = s.get("source", "")
                    tag = f" [dim]({src})[/dim]" if src else ""
                    response_area.write(f"  [dim]*[/dim] [@click=show_sessions][cyan]{title}[/cyan][/] [dim]{sid}{tag}[/dim]\n")
            else:
                response_area.write("  [dim](none yet — just start typing!)[/dim]\n")

            self._populate_session_select()
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
        """Handle agent events — bridge to Textual reactive system, OpenCode-style."""
        response_area = self.query_one("#response-area", ResponseArea)
        tool_log = self.query_one("#tool-log", ToolLogWidget)

        if event_type == "thinking":
            self._thinking = True
            self._stream_bubble_open = False
            self._stream_reasoning_shown = False
            self._reasoning_start = time.time()
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
            if data:
                elapsed = time.time() - self._reasoning_start if self._reasoning_start > 0 else 0
                if not self._stream_reasoning_shown:
                    response_area.write(f"[dim italic]  Thought [{elapsed:.1f}s][/dim italic]\n")
                    if len(data) > 200:
                        preview = data[:200] + "..."
                        response_area.write(f"[dim italic]  {preview}[/dim italic]\n")
                    self._stream_reasoning_shown = True
            self._update_sidebar()

        elif event_type == "response":
            # Content arriving during live stream — content handled by _run_chat
            self._update_sidebar()

        elif event_type == "done":
            self._thinking = False
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
                    args_str = json.dumps(tool.get("args", {}))
                    short_args = args_str[:80] + ("..." if len(args_str) > 80 else "")
                    response_area.write(f"[dim]  ⚙ {tool['name']}({short_args})[/dim]\n")
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
                suffix = f" [dim green]✔ {count} tool{'s' if count > 1 else ''} done[/dim green]"
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
                background: #212121;
                border: thick #5c9cf5;
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

    # ——— Chat message rendering (OpenCode-style) ———

    @staticmethod
    def _render_message(role: str, content: str) -> str:
        """Render a single message in OpenCode's clean terminal style."""
        if not content.strip():
            return ""

        limits = {"user": 1000, "assistant": 4000, "reasoning": 800, "tool": 200}
        limit = limits.get(role, 2000)
        text = content[:limit]

        if role == "user":
            escaped = _rich_escape(text)
            return f"[bold cyan]▸[/bold cyan] {escaped}"

        if role == "assistant":
            return _rich_escape(text)

        if role == "reasoning":
            escaped = _rich_escape(text)
            return f"[dim italic]  Thought: {escaped}[/dim italic]"

        if role == "tool":
            escaped = _rich_escape(text)
            return f"[dim]  ⚙ {escaped}[/dim]"

        return _rich_escape(text)

    def _load_history(self) -> None:
        """Display loaded session history — OpenCode-style clean rendering."""
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.clear()
        session = self.agent.sessions.active
        if not session or not session.messages:
            return

        resume_mode = session.metadata.get("_resume_mode", "full")
        prev_role = None

        for msg in session.messages:
            if msg.role == "system":
                content = msg.content
                if any(kw in content for kw in ("[Context compacted", "[Showing last", "[Session summary", "[skipped", "tool/reasoning")):
                    response_area.write(f"\n[dim italic]{_rich_escape(content)}[/dim italic]")
                continue

            if msg.role == "tool" and resume_mode == "clean":
                continue  # skip tools in clean resume mode

            # Add spacing between different user turns
            if msg.role == "user" and prev_role and prev_role != "user":
                response_area.write("\n")

            rendered = self._render_message(msg.role, msg.content)
            if rendered:
                response_area.write(f"{rendered}\n")

            prev_role = msg.role

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
                session.messages.insert(0, ProviderMessage(
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

        # Show user message — OpenCode style: bold cyan arrow
        response_area.write(f"[bold cyan]▸[/bold cyan] {text}\n")

        if self.agent:
            self._thinking = True
            self._toggle_send_button()
            self._response = ""
            self._tool_log = []
            self._update_sidebar()
            self._chat_task = asyncio.create_task(self._run_chat(text))

        # Always re-focus chat input after sending
        self.query_one("#prompt-input", ChatInput).focus()

    @on(Button.Pressed, "#send-btn")
    async def _on_send_btn(self) -> None:
        """Send button clicked."""
        if self._thinking:
            # Acting as stop button — cancel current chat
            self._cancelled = True
            if self._chat_task and not self._chat_task.done():
                self._chat_task.cancel()
            return
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
                    yield Label(" Commit ", id="dialog-title")
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
                """Use the active AI provider + session context to generate a commit message."""
                input_widget = self.query_one("#commit-msg-input", Input)
                auto_btn = self.query_one("#auto-commit-btn", Button)

                if self._generating:
                    return
                self._generating = True
                auto_btn.disabled = True
                auto_btn.label = "Generating..."

                # --- Build smart fallback from session context + porcelain ---
                files = git_ref.status_porcelain() if git_ref else []
                file_names = [line[3:] for line in files] if files else []

                # Infer commit type from porcelain status codes
                status_codes = set(line[:2] for line in files) if files else set()
                if any(c in ("A ", "AM", "??") for c in status_codes):
                    commit_type = "feat"
                elif any(c in ("M ", "MM", "R ") for c in status_codes):
                    commit_type = "fix"
                else:
                    commit_type = "chore"

                # Try session summary first (AI-generated, most descriptive)
                # Only use it if actually meaningful — NOT session title (just first user message)
                session_note = ""
                if agent_ref and agent_ref.sessions.active:
                    s = agent_ref.sessions.active
                    if s.summary and len(s.summary) > 20:
                        # Extract first sentence as topic hint
                        first_sentence = s.summary.split(".")[0].strip()[:80]
                        if first_sentence and len(first_sentence) > 10:
                            session_note = first_sentence.lower()

                # Build fallback: session summary > file list > generic
                # NOTE: session.title is NOT used — it's just the first chat message, not descriptive
                if session_note:
                    smart_fallback = f"{commit_type}: {session_note}"[:72]
                elif file_names:
                    file_list = ", ".join(f.rsplit("/", 1)[-1] for f in file_names[:3])
                    smart_fallback = f"{commit_type}: {file_list}"[:72]
                else:
                    smart_fallback = f"{commit_type}: update"

                try:
                    diff_text = git_ref.diff_summary() if git_ref else ""

                    # Fixed precedence: check diff emptiness properly
                    has_diff = bool(diff_text) and "\ndiff:" in diff_text
                    if not has_diff:
                        input_widget.value = smart_fallback
                        return

                    from debigbos.models.provider import Message, ModelOptions
                    provider = agent_ref.providers.active if agent_ref else None
                    if not provider:
                        input_widget.value = smart_fallback
                        return

                    # --- Gather session context (what we've been doing) ---
                    session_context = ""
                    if agent_ref and agent_ref.sessions.active:
                        session = agent_ref.sessions.active
                        # Use auto-generated summary if available
                        if session.summary:
                            session_context = f"Session context (what we've been working on):\n{session.summary[:400]}\n\n"
                        else:
                            # Fallback: last 3 user/assistant exchanges
                            recent = [
                                m for m in session.messages[-10:]
                                if m.role in ("user", "assistant") and m.content
                            ][-6:]
                            if recent:
                                parts = []
                                for m in recent:
                                    role = "User" if m.role == "user" else "BigBos"
                                    parts.append(f"[{role}]: {m.content[:200]}")
                                session_context = "Recent conversation:\n" + "\n".join(parts) + "\n\n"

                    prompt = (
                        "Write a SHORT, specific git commit message for the changes below.\n"
                        "Use conventional commits: feat:, fix:, chore:, refactor:, docs:, style:, test:.\n"
                        "Rules:\n"
                        "- Single line, max 72 chars, present tense, imperative mood.\n"
                        "- Be SPECIFIC — mention what changed, not 'update code' or 'fix bug'.\n"
                        "- Reference the intent from the session context above, not just the diff.\n"
                        "- Output ONLY the commit message, no quotes, no markdown, no explanation.\n\n"
                        f"{session_context}"
                        f"Git changes:\n{diff_text}"
                    )

                    response = await provider.chat(
                        [Message(role="user", content=prompt)],
                        [],
                        ModelOptions(
                            model=agent_ref.config.active_model if agent_ref else "deepseek-v4-flash",
                            max_tokens=80,
                        ),
                    )

                    # Don't use error responses as commit messages
                    if response.finish_reason == "error":
                        self.notify(
                            f"AI commit message failed: {response.content[:120]}",
                            severity="warning",
                        )
                        input_widget.value = smart_fallback
                        return

                    msg = response.content.strip().strip('"').strip("'")
                    # Also guard against error-prefixed content that snuck through
                    if msg.startswith("[") and "] " in msg[:30]:
                        input_widget.value = smart_fallback
                        return
                    msg = msg.split("\n")[0].strip()
                    if len(msg) > 100:
                        msg = msg[:97] + "..."
                    input_widget.value = msg or smart_fallback
                except Exception:
                    input_widget.value = smart_fallback
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

    @on(Button.Pressed, "#settings-btn")
    def _on_settings_btn(self) -> None:
        """Open the settings dialog."""
        if not self.agent:
            return
        asyncio.create_task(self._show_settings())

    async def _show_settings(self) -> None:
        """Push settings dialog (worker for push_screen_wait)."""
        dialog = SettingsDialog(self)
        worker = self.run_worker(
        self.app.push_screen_wait(dialog),
            exclusive=True
        )
        await worker.wait()
        
        # Refresh sidebar AFTER dialog closes
        self._update_sidebar()

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
        """Run chat with streaming response — OpenCode-style clean output."""
        response_area = self.query_one("#response-area", ResponseArea)
        sidebar = self.query_one("#sidebar-info", SidebarWidget)
        first_content = True
        try:
            async for chunk in self.agent.stream_chat(user_input):
                if self._cancelled:
                    response_area.write("\n[dim]── Cancelled[/dim]\n")
                    break
                if first_content and chunk.strip():
                    first_content = False
                response_area.write(chunk)
        except asyncio.CancelledError:
            response_area.write("\n[dim]── Cancelled[/dim]\n")
        except Exception as e:
            error = str(e)[:100]
            response_area.write(f"\n[red]Error: {error}[/red]")
            sidebar.error_msg = error
        finally:
            if not first_content:
                response_area.write("\n")
                if self.agent:
                    p = self.agent.config.active_provider
                    m = self.agent.config.active_model
                    mode = self.agent.config.mode.upper()
                    response_area.write(f"[dim]── {mode} · {p}/{m}[/dim]\n")
            self._thinking = False
            self._cancelled = False
            self._chat_task = None
            sidebar.error_msg = ""
            self._update_sidebar()
            try:
                self.query_one("#prompt-input", ChatInput).focus()
            except NoMatches:
                pass

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

        elif cmd == "/dump":
            await self._dump_session()

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
            response_area.write(f"  [cyan]max_short_term:[/cyan] {mem.max_short_term} messages\n")
            cc = mem.compaction
            response_area.write("[bold]  compaction:[/bold]\n")
            response_area.write(f"    [cyan]auto:[/cyan] {'✅ on' if cc.auto else '❌ off'}\n")
            response_area.write(f"    [cyan]threshold:[/cyan] {cc.threshold:.0%}\n")
            response_area.write(f"    [cyan]keep:[/cyan] {cc.keep} messages\n")
            response_area.write(f"    [cyan]prune:[/cyan] {'✅ on' if cc.prune else '❌ off'}\n")
            response_area.write(f"    [cyan]reserved:[/cyan] {cc.reserved:,} tokens\n")
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

        elif key == "compaction":
            sub_parts = val.split(maxsplit=1) if val else []
            sub_key = sub_parts[0].lower() if sub_parts else ""
            sub_val = sub_parts[1].strip().lower() if len(sub_parts) > 1 else ""
            cc = mem.compaction

            if sub_key in ("auto",):
                if sub_val in ("on", "true", "1", "yes"):
                    cc.auto = True
                elif sub_val in ("off", "false", "0", "no"):
                    cc.auto = False
                else:
                    response_area.write("[yellow]Usage: /config memory compaction auto on|off[/yellow]\n")
                    return
                response_area.write(f"[green]compaction.auto = {cc.auto}[/green]\n")

            elif sub_key in ("threshold",):
                try:
                    cc.threshold = float(sub_val)
                    response_area.write(f"[green]compaction.threshold = {cc.threshold:.0%}[/green]\n")
                except ValueError:
                    response_area.write("[yellow]Usage: /config memory compaction threshold <0.0-1.0>[/yellow]\n")

            elif sub_key in ("keep",):
                try:
                    cc.keep = int(sub_val)
                    response_area.write(f"[green]compaction.keep = {cc.keep} messages[/green]\n")
                except ValueError:
                    response_area.write("[yellow]Usage: /config memory compaction keep <number>[/yellow]\n")

            elif sub_key in ("prune",):
                if sub_val in ("on", "true", "1", "yes"):
                    cc.prune = True
                elif sub_val in ("off", "false", "0", "no"):
                    cc.prune = False
                else:
                    response_area.write("[yellow]Usage: /config memory compaction prune on|off[/yellow]\n")
                    return
                response_area.write(f"[green]compaction.prune = {cc.prune}[/green]\n")

            elif sub_key in ("reserved",):
                try:
                    cc.reserved = int(sub_val)
                    response_area.write(f"[green]compaction.reserved = {cc.reserved:,} tokens[/green]\n")
                except ValueError:
                    response_area.write("[yellow]Usage: /config memory compaction reserved <tokens>[/yellow]\n")

            else:
                response_area.write("[yellow]Usage: /config memory compaction <auto|threshold|keep|prune|reserved> <value>[/yellow]\n")

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

    def action_select_all(self) -> None:
        """Select all text in the focused widget (input or response area)."""
        # Try input first (usually focused)
        try:
            focused = self.focused
            if focused and hasattr(focused, 'select_all'):
                focused.select_all()
                return
        except Exception:
            pass
        # Fallback: select all in response area
        try:
            response = self.query_one("#response-area", ResponseArea)
            response.select_all()
        except Exception:
            pass

    def action_copy_text(self) -> None:
        """Copy selected text to clipboard, or fallback to last assistant response."""
        response_area = self.query_one("#response-area", ResponseArea)

        # 1. If there's a selection, copy via Textual's Screen-level copy
        try:
            sel = response_area.selection
            if sel and sel.start != sel.end:
                # Use the app's built-in copy (copies current selection)
                self.app.action_copy()
                return
        except Exception:
            pass

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

    async def _dump_session(self) -> None:
        """Dump current session to a temp file so user can read/copy errors."""
        import tempfile, pathlib
        if not self.agent or not self.agent.sessions.active:
            self.query_one("#response-area", ResponseArea).write("[yellow]No active session[/yellow]\n")
            return
        sess = self.agent.sessions.active
        lines = [f"Session: {sess.id}", f"Title: {sess.title}", f"Messages: {len(sess.messages)}", ""]
        for m in sess.messages:
            head = f"[{m.role}]"
            if m.name:
                head += f" ({m.name})"
            lines.append(head)
            lines.append(m.content)
            lines.append("")
        text = "\n".join(lines)
        dump_dir = pathlib.Path(tempfile.gettempdir()) / "deBigBos"
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / f"session-{sess.id[:12]}.txt"
        dump_path.write_text(text, encoding="utf-8")
        self.notify(f"Dumped to {dump_path}", timeout=5)
        self.query_one("#response-area", ResponseArea).write(
            f"[green]Session dumped:[/green] [dim]{dump_path}[/dim]\n"
        )

    def _show_help(self) -> None:
        """Show help in the response area."""
        help_text = """
## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show this help |
| `/exit`, `/q` | Quit de BigBos |
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
        try:
            sidebar = self.query_one("#sidebar-info", SidebarWidget)
        except NoMatches:
            return
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
        sidebar.refresh()

        # Update status bar
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.model = self.agent.config.active_model
        status_bar.provider = self.agent.config.active_provider
        status_bar.workspace = str(self.workspace) if self.workspace else ""
        if self._git and self._git.is_repo:
            try:
                branch = self._git.current_branch()
                status_bar.git_info = f"{self.workspace}:{branch}" if self.workspace else branch
            except Exception:
                status_bar.git_info = str(self.workspace) if self.workspace else ""
        else:
            status_bar.git_info = str(self.workspace) if self.workspace else ""
        if session:
            try:
                provider = self.agent.providers.active
                if provider:
                    tokens = provider.count_tokens(session.to_llm_format())
                    sidebar.context_tokens = tokens
                    status_bar.context_tokens = tokens
                    # Context limit from model
                    model = self.agent.config.active_model
                    limit = provider.get_context_window(model)
                    sidebar.context_limit = limit
                    status_bar.context_limit = limit
                    # Accumulated cost
                    cost = self.agent.state.accumulated_cost
                    sidebar.total_cost = cost
                    status_bar.total_cost = cost
            except Exception:
                pass
        status_bar.mode = sidebar.mode
        status_bar.thinking = self._thinking
        status_bar.refresh()

        # Toggle send/stop button
        self._toggle_send_button()

        # Determine mode — use config mode, not model name
        sidebar.mode = self.agent.config.mode

        # Update mode button visuals
        self._update_mode_buttons()

    def _toggle_send_button(self) -> None:
        """Toggle send button ↔ stop button based on thinking state."""
        try:
            btn = self.query_one("#send-btn", Button)
        except NoMatches:
            return
        if self._thinking:
            btn.label = "■"
            btn.variant = "error"
            btn.add_class("stop-btn")
            btn.tooltip = "Stop generation"
        else:
            btn.label = "⏎"
            btn.variant = "primary"
            btn.remove_class("stop-btn")
            btn.tooltip = "Send message"

    # ── Version & Update ──────────────────────────────────────

    def _init_version_label(self) -> None:
        """Set the version label with current build number."""
        try:
            label = self.query_one("#sidebar-version", VersionLabel)
            label.version = get_version_string()
        except NoMatches:
            pass

    async def _check_for_updates(self) -> None:
        """Check for updates in background, update version label dot."""
        try:
            from ...core.updater import Updater
            updater = Updater()
            # Run check in thread to avoid blocking
            new_version = await asyncio.to_thread(updater.check, force=False)
            if new_version:
                label = self.query_one("#sidebar-version", VersionLabel)
                label.update_available = True
                label._latest_version = new_version
                label._changelog = ""  # Fetching changelog async is optional
                label.tooltip = f"Update available: {new_version} — click for details"
        except Exception:
            pass  # Silently fail — updates are optional

    @on(UpdateAvailable)
    def _on_update_available(self, event: UpdateAvailable) -> None:
        """Handle version label click — show version info / update dialog."""
        event.stop()
        # Must run in a worker since push_screen_wait requires it
        import asyncio
        asyncio.create_task(self._show_update_dialog(event))

    async def _show_update_dialog(self, event: UpdateAvailable) -> None:
        """Show version/update dialog with fresh check + update flow (runs in worker)."""
        from textual.containers import Vertical, Horizontal
        from textual.widgets import Label as ModalLabel, Button as ModalButton

        # Phase 1: Show checking dialog (use push_screen_wait so it's properly mounted)
        class CheckingDialog(ModalScreen[bool]):
            BINDINGS = [("escape", "close", "Close")]
            def compose(self) -> ComposeResult:
                with Vertical(id="version-dialog", classes="modal-container"):
                    yield ModalLabel(" de BigBos Update ", id="dialog-title")
                    yield ModalLabel("")
                    yield ModalLabel("[bold yellow]⏳ Checking for updates...[/bold yellow]")
                    yield ModalLabel("[dim]Fetching latest from GitHub...[/dim]")
            def action_close(self) -> None:
                self.dismiss(False)

        # Show checking dialog, wait for it to fully mount
        checking = CheckingDialog()
        await self.app.push_screen(checking, wait_for_dismiss=False)

        # Run fresh check in background while dialog shows
        from ...core.updater import Updater
        updater = Updater()
        new_version = await asyncio.to_thread(updater.check, force=True)

        # Dismiss checking dialog
        checking.dismiss(False)

        # Phase 2: Show result dialog with update details
        is_update = bool(new_version)

        class ResultDialog(ModalScreen[bool]):
            BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

            def __init__(self, new_ver: str, is_update: bool):
                super().__init__()
                self.new_ver = new_ver
                self.is_update = is_update

            def compose(self) -> ComposeResult:
                with Vertical(id="version-dialog", classes="modal-container"):
                    yield ModalLabel(" de BigBos Update ", id="dialog-title")
                    yield ModalLabel("")
                    if self.is_update:
                        yield ModalLabel(f"[bold yellow]⬇ Update Available![/bold yellow]")
                        yield ModalLabel(f"[bold]{self.new_ver}[/bold] [dim]→ latest[/dim]")
                        yield ModalLabel("")
                        yield ModalLabel("[dim]Click 'Update Now' to pull & restart.[/dim]")
                    else:
                        yield ModalLabel("[bold green]✓ You're up to date![/bold green]")
                        yield ModalLabel("[dim]Running the latest version.[/dim]")
                    yield ModalLabel("")
                    with Horizontal():
                        if self.is_update:
                            yield ModalButton("⬇ Update Now", variant="primary", id="update-now-btn")
                        yield ModalButton("Close", variant="default", id="close-btn")

            def on_button_pressed(self, event: Button.Pressed) -> None:
                if event.button.id == "update-now-btn":
                    self.dismiss(True)
                else:
                    self.dismiss(False)

            def action_close(self) -> None:
                self.dismiss(False)

        result = ResultDialog(new_version or "", is_update)
        confirmed = await self.app.push_screen_wait(result)

        # Update label state
        label = self.query_one("#sidebar-version", VersionLabel)
        label.update_available = is_update
        if new_version:
            label._latest_version = new_version

        if confirmed:
            try:
                await self._do_update()
            except Exception as e:
                self.notify(f"Update error: {e}", severity="error")

    async def _do_update(self) -> None:
        """Pull latest code with live progress log and restart."""
        from textual.containers import Vertical, Horizontal
        from textual.widgets import Label as ModalLabel, RichLog as DialogLog, Button as ModalButton
        from ...core.updater import Updater

        updater = Updater()
        repo_path = updater.repo_path or ""
        home_screen = self

        # Diagnostic file log (kept across crashes)
        import os as _os
        log_file = Path.home() / ".config" / "deBigBos" / "update.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n--- update started {__import__('time').time()} ---\n")
            lf.write(f"repo_path={repo_path}\n")
            lf.write(f"sys.argv={__import__('sys').argv}\n")
            lf.write(f"sys.executable={__import__('sys').executable}\n")

        def _file_log(msg: str) -> None:
            try:
                with open(log_file, "a", encoding="utf-8") as lf:
                    lf.write(msg + "\n")
            except Exception:
                pass

        class UpdatingDialog(ModalScreen[None]):
            BINDINGS = [("escape", "close", "Close")]

            def compose(self) -> ComposeResult:
                with Vertical(id="version-dialog", classes="modal-container"):
                    yield ModalLabel(" de BigBos Update ", id="dialog-title")
                    yield ModalLabel("")
                    yield ModalLabel("[bold yellow]⬇ Updating de BigBos...[/bold yellow]")
                    yield ModalLabel("[dim]Pulling latest from GitHub:[/dim]")
                    yield DialogLog(id="update-log", max_lines=200, min_height=10)
                    with Horizontal():
                        yield ModalButton("Close", variant="default", id="close-btn")

            def on_button_pressed(self, event: Button.Pressed) -> None:
                self.dismiss(None)

            def action_close(self) -> None:
                self.dismiss(None)

            async def on_mount(self) -> None:
                """Dialog mounted — start update in background worker."""
                log = self.query_one("#update-log", DialogLog)
                log.can_focus = False
                self.run_worker(self._run_update(log))

            async def _run_update(self, log: DialogLog) -> None:
                try:
                    if not repo_path:
                        log.write("[red]No repo path found![/red]")
                        _file_log("ERROR: no repo_path")
                        return

                    log.write("[dim]Fetching origin...[/dim]")
                    _file_log("fetch origin")
                    proc = await asyncio.create_subprocess_exec(
                        "git", "-C", repo_path, "fetch", "origin",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                    )
                    stdout, _ = await proc.communicate()
                    if stdout:
                        for line in stdout.decode(errors="replace").splitlines()[:10]:
                            log.write(f"[dim]{line}[/dim]")
                            _file_log(f"fetch: {line}")

                    if proc.returncode != 0:
                        log.write("[red]Fetch failed! Check network.[/red]")
                        _file_log(f"ERROR fetch returncode={proc.returncode}")
                        return

                    log.write("")
                    log.write("[bold]Incoming changes:[/bold]")
                    proc2 = await asyncio.create_subprocess_exec(
                        "git", "-C", repo_path, "log", "HEAD..origin/main", "--oneline",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                    )
                    stdout2, _ = await proc2.communicate()
                    if stdout2 and stdout2.strip():
                        for line in stdout2.decode(errors="replace").splitlines():
                            log.write(f"  [yellow]{line}[/yellow]")
                            _file_log(f"incoming: {line}")
                    else:
                        log.write("  [dim](no new commits)[/dim]")
                        log.write("")
                        log.write("[green]Already up to date — no restart needed.[/green]")
                        _file_log("already up to date")
                        return

                    log.write("")
                    log.write("[bold]Pulling...[/bold]")
                    _file_log("pull origin main")
                    proc3 = await asyncio.create_subprocess_exec(
                        "git", "-C", repo_path, "pull", "origin", "main",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                    )
                    stdout3, _ = await proc3.communicate()
                    pull_output = stdout3.decode(errors="replace") if stdout3 else ""
                    for line in pull_output.splitlines():
                        log.write(f"  {line}")
                        _file_log(f"pull: {line}")

                    if proc3.returncode != 0:
                        log.write("[red]Pull failed![/red]")
                        _file_log(f"ERROR pull returncode={proc3.returncode}")
                        return

                    # Sync skills
                    log.write("")
                    log.write("[dim]Syncing skills...[/dim]")
                    _file_log("sync skills")
                    try:
                        updater._sync_skills(show_output=False)
                        log.write("[green]Skills synced.[/green]")
                        _file_log("skills synced")
                    except Exception:
                        log.write("[dim]Skills already up to date.[/dim]")
                        _file_log("skills sync skipped")

                    log.write("")
                    log.write("[bold green]✅ Update complete![/bold green]")
                    log.write("[bold yellow]⚠ Restarting in 3 seconds...[/bold yellow]")
                    _file_log("update complete, restarting in 3s")

                    await asyncio.sleep(3.0)

                    # Safer restart: use deBigBos launcher if available
                    import sys, shutil
                    launcher = shutil.which("deBigBos")
                    args = sys.argv[1:]
                    if launcher:
                        _file_log(f"restart via launcher: {launcher} {args}")
                        _os.execv(launcher, [launcher] + args)
                    else:
                        _file_log(f"restart via python -m debigbos {args}")
                        _os.execv(sys.executable, [sys.executable, "-m", "debigbos"] + args)

                except Exception as e:
                    import traceback
                    err = f"Error: {e}"
                    log.write(f"[red]{err}[/red]")
                    _file_log(err)
                    for line in traceback.format_exc().splitlines()[-5:]:
                        log.write(f"[dim red]{line}[/dim red]")
                        _file_log(line)

        await self.app.push_screen(UpdatingDialog())

    # ── Clickable session actions (from welcome page) ──────────

    def action_new_session(self) -> None:
        """Create a new session (triggered from welcome click)."""
        import asyncio
        asyncio.create_task(self._on_new_session_click())

    async def _on_new_session_click(self) -> None:
        """Start a fresh session."""
        self.agent.sessions.active_session_id = None
        response_area = self.query_one("#response-area", ResponseArea)
        response_area.clear()
        response_area.write("[green]🆕 New session started! Just type your first message.[/green]\n")
        self._update_sidebar()

    def action_switch_session(self, session_id: str) -> None:
        """Switch to an existing session (triggered from welcome click)."""
        import asyncio
        asyncio.create_task(self._on_switch_session_click(session_id))

    async def _on_switch_session_click(self, session_id: str) -> None:
        """Load and display a session."""
        if self.agent.continue_session(session_id):
            response_area = self.query_one("#response-area", ResponseArea)
            response_area.clear()
            response_area.write(f"[green]📂 Loaded session: [bold]{session_id[:8]}[/bold][/green]\n")
            # Replay session messages
            replay_messages = self.agent.sessions.history(session_id)
            for msg in replay_messages:
                if msg.role == "assistant":
                    response_area.write(msg.content)
            self._update_sidebar()
            self.notify(f"Switched to session {session_id[:8]}", severity="success")
        else:
            self.notify(f"Failed to load session {session_id[:8]}", severity="error")

    # ── Keybinding actions ────────────────────────────────────
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
                background: #212121;
                border: thick #5c9cf5;
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
        btn.label = "BUILD" if mode == "build" else "PLAN"
        btn.variant = "primary" if mode == "build" else "warning"
        
        # Build = blue, Plan = orange
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
