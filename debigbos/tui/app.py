"""de BigBos TUI Application — main Textual App.

Architecture (OpenCode-inspired):
  - Plugin system with lifecycle hooks
  - Route-based screen navigation
  - Slot system for widget injection
  - Keymap layers for keybindings
  - Theme management with JSON tokens
  - Dialog components (Alert, Confirm, Prompt, Select)

Usage:
    from debigbos.tui.app import BigBosApp, run_app
    await run_app("/path/to/workspace")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from .plugin import PluginManager, TuiPlugin, TuiPluginApi
from .routes import RouteRegistry
from .slots import SlotRegistry
from .keymap import KeymapRegistry
from .theme import ThemeManager
from .screens.home import HomeScreen
from .screens.welcome import WelcomeScreen


class BigBosApp(App[Any]):
    """Main de BigBos Textual application."""
    TITLE = "de BigBos.agent"
    
    CSS = """
    Screen {
        background: #0a0a0a;
    }

    #main-area {
        width: 3fr;
        height: 100%;
        border-right: solid #4b4c5c;
    }

    #sidebar {
        width: 1fr;
        height: 100%;
        padding: 1;
        background: #212121;
    }

    #sidebar-info {
        height: auto;
        color: #e0e0e0;
    }

    #response-area {
        height: 5fr;
        padding: 1 2;
        background: #0a0a0a;
        color: #e0e0e0;
        border: none;
    }

    #tool-log {
        height: auto;
        max-height: 6;
        padding: 1 2;
        background: #252525;
        border-top: solid #4b4c5c;
        color: #6a6a6a;
    }

    #tool-log.hidden {
        display: none;
    }

    #prompt-area {
        height: auto;
        padding: 2 1 1 1;
        background: #212121;
        border-top: solid #4b4c5c;
    }

    /* Mode toggle — vertical, left of chatbox */
    #mode-toggle {
        width: auto;
        min-width: 8;
        height: auto;
        align: center middle;
    }

    .mode-btn {
        width: 100%;
        height: auto;
        min-height: 3;
        margin-bottom: 0;
    }

    /* Build mode (blue — code action, execution) */
    .mode-build {
        background: #5c9cf5 80%;
        border: solid #5c9cf5;
        color: #ffffff;
    }

    /* Plan mode (orange — thinking, strategy) */
    .mode-plan {
        background: #fab283 80%;
        border: solid #fab283;
        color: #000000;
    }

    #prompt-input {
        width: 1fr;
        height: auto;
        min-height: 3;
        max-height: 12;
        border: none;
        border-left: solid #fab283;
        background: #0a0a0a;
        color: #e0e0e0;
        padding: 0 1;
    }

    #send-btn {
        min-width: 10;
        height: 3;
        background: #fab283;
        color: #000000;
    }

    /* Sidebar widget */
    SidebarWidget {
        padding: 1;
    }

    #sidebar-session-label {
        padding: 0 1;
        margin-top: 1;
    }

    #session-select {
        margin: 0 1;
        width: 100%;
    }

    #session-controls {
        width: 100%;
        height: auto;
    }

    #session-controls Select {
        width: 1fr;
    }

    #delete-session-btn {
        min-width: 4;
        height: 3;
        margin-right: 1;
    }

    .icon-btn {
        padding: 0;
    }

    #git-actions {
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center middle;
    }

    #git-actions Button {
        width: 1fr;
    }

    #sidebar-provider-label, #sidebar-model-label {
        padding: 0 1;
        margin-top: 1;
    }

    #provider-select, #model-select {
        margin: 0 1;
        width: 100%;
    }

    #provider-controls {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #provider-controls Select {
        width: 1fr;
    }

    #provider-controls Button {
        min-width: 4;
    }

    /* Modal dialog containers */
    .modal-container {
        width: 70%;
        height: auto;
        max-height: 90%;
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
        align: center middle;
    }

    #dialog-title {
        width: 100%;
        text-align: center;
        padding: 1;
        margin-bottom: 1;
    }

    #dialog-body {
        width: 100%;
        height: auto;
        padding: 1 0;
    }

    #dialog-body Label {
        padding: 0 1;
        margin-top: 1;
    }

    #dialog-body Input {
        width: 100%;
        margin: 0 1;
    }

    #dialog-body Select {
        width: 100%;
        margin: 0 1;
    }

    #dialog-actions {
        width: 100%;
        height: auto;
        align: center middle;
        padding: 1 0;
        margin-top: 1;
    }

    #dialog-actions Button {
        margin: 0 1;
        min-width: 16;
    }

    /* Commit dialog — centered popup with transparent overlay */
    #commit-dialog {
        width: 65%;
        height: auto;
        min-height: 14;
        background: #0f0f23 95%;
        border: thick #5c9cf5;
        padding: 2 3;
        align: center middle;
    }

    /* Add Provider dialog */
    #add-provider-dialog {
        width: 55%;
        height: auto;
        max-height: 95%;
        background: #0f0f23 95%;
        border: thick #fab283;
        padding: 2 3;
        align: center middle;
    }

    /* Commit dialog in Plan mode */
    #commit-dialog.mode-plan {
        border: thick #fab283;
    }

    /* Commit dialog in Build mode */
    #commit-dialog.mode-build {
        border: thick #5c9cf5;
    }

    #commit-msg-input {
        width: 100%;
        margin: 1 0;
        height: 5;
        background: #0a0a0a;
        border: solid #4b4c5c;
        color: #e0e0e0;
    }

    /* Dialog styling — transparent overlay, centered */
    ModalScreen {
        background: transparent;
        align: center middle;
    }

    DialogAlert > Center,
    DialogConfirm > Center,
    DialogPrompt > Center,
    DialogSelect > Center {
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
    }

    /* Status bar */
    #status-bar {
        height: 1;
        padding: 0 2;
        background: #212121;
        color: #6a6a6a;
    }

    /* Header */
    Header {
        background: #0a0a0a;
        color: #fab283;
    }

    /* Scrollbar */
    Scrollbar {
        scrollbar-color: #4b4c5c;
        scrollbar-color-hover: #5c9cf5;
    }

    /* Keyboard shortcuts panel in sidebar */
    #sidebar-shortcuts {
        height: auto;
        padding: 1;
        margin-top: 1;
        border-top: dashed #4b4c5c;
        color: #6a6a6a;
    }

    /* Version label — clickable, subtle */
    #sidebar-version {
        height: auto;
        padding: 1 1 1 1;
        color: #6a6a6a;
        text-style: dim;
    }
    #sidebar-version:hover {
        color: #e0e0e0;
    }
    VersionLabel {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "command_palette", "Palette"),
    ]

    def __init__(
        self,
        workspace: Path | None = None,
        agent: Any = None,
        driver_class: Any = None,
        css_path: Any = None,
        watch_css: bool = False,
    ) -> None:
        super().__init__(driver_class=driver_class, css_path=css_path, watch_css=watch_css)
        self.workspace = workspace or Path.cwd()
        self.agent = agent

        # Plugin system
        self.plugin_manager = PluginManager()

        # Theme tokens (not to be confused with Textual's theme reactive)
        self._theme_tokens = ThemeManager.current()

    def compose(self) -> ComposeResult:
        """Build the app shell."""
        yield Header(show_clock=True, name="de BigBos", icon="🦾")
        yield Footer()

    async def on_mount(self) -> None:
        """Called when app is mounted. Init agent, show welcome, then home."""
        # Create and initialize agent
        if not self.agent:
            from ..core.agent import BigBosAgent
            self.agent = BigBosAgent(self.workspace)
            await self.agent.initialize()

        # Show welcome screen first
        welcome = WelcomeScreen(agent=self.agent, workspace=self.workspace)
        self.push_screen(welcome)

    async def _load_plugins(self) -> None:
        """Load plugins from config."""
        # Look for tui.json plugins config
        config_path = self.workspace / "deBigBos.json"
        if config_path.exists():
            import json
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                plugins_config = config.get("tui", {}).get("plugins", [])
                for plugin_cfg in plugins_config:
                    # For now, plugins are loaded via config
                    pass
            except Exception:
                pass

    def action_command_palette(self) -> None:
        """Show command palette."""
        self.notify("Command palette — coming soon!", title="Palette")


async def run_app(
    workspace: str | Path = ".",
    agent: Any = None,
) -> None:
    """Run the de BigBos TUI application.

    Args:
        workspace: Path to project workspace
        agent: BigBosAgent instance (or None for headless)
    """
    ws = Path(workspace).resolve()

    app = BigBosApp(workspace=ws, agent=agent)

    # Apply theme early
    ThemeManager._apply_theme(app)

    await app.run_async()


# Legacy compatibility wrapper
class BigBosTUI:
    """Legacy wrapper for the Textual-based TUI. Maintains the old interface."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace or Path.cwd()
        self.agent = None
        self.show_raw = False

    async def run(self) -> None:
        """Start the TUI."""
        await run_app(str(self.workspace), self.agent)
