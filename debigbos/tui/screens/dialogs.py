"""Dialog screens for de BigBos TUI.

Classes moved here from home.py:
- SettingsDialog (settings modal with AI Provider + Skills tabs)
- AddProviderDialog (add new provider form)
- EditProviderDialog (edit endpoint/API key)
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on, events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    Select,
    Switch,
)

from ...config.auth import get_auth_manager
from ...config.manager import ProviderConfig
from ... import get_version_string


class SettingsDialog(ModalScreen[None]):
    """Settings dialog with General + Skills tabs."""

    DEFAULT_CSS = """
    #settings-dialog {
        width: 80%;
        height: auto;
        border: solid green;
        background: $surface;
        padding: 1;
    }
    #settings-actions {
        width: 100%;
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    #settings-actions Button {
        margin-left: 1;
    }
    .provider-row {
        padding: 0 1;
    }
    """

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, home_screen: Any):
        super().__init__()
        self._home = home_screen
        self._skill_switches: dict[str, Switch] = {}

    def compose(self) -> ComposeResult:
        from textual.widgets import TabbedContent, TabPane

        with Vertical(id="settings-dialog", classes="modal-container"):
            yield Label(" ⚙ Settings ", id="dialog-title")
            with TabbedContent(id="settings-tabs"):
                # Tab 1: AI Provider
                with TabPane("🤖 AI Provider", id="tab-provider"):
                    yield Label("")
                    yield Label("[bold]Current Model[/bold]")
                    agent = self._home.agent
                    if agent:
                        yield Label(
                            f"  {agent.config.active_provider} / {agent.config.active_model}",
                            id="current-model-label",
                        )
                    else:
                        yield Label("  [dim]No agent loaded[/dim]", id="current-model-label")
                    yield Label("")
                    yield Label("[bold]Providers[/bold]")
                    if agent and agent.config.providers:
                        for name, cfg in agent.config.providers.items():
                            is_active = name == agent.config.active_provider
                            marker = "[green]●[/green]" if is_active else "○"
                            models = cfg.models if isinstance(cfg.models, list) else []
                            model_options = [(m, m) for m in models] if models else [("none", "")]
                            active_model = cfg.default_model or (models[0] if models else "")
                            with Horizontal(id=f"provider-row-{name}", classes="provider-row"):
                                yield Label(f"  {marker} [bold]{name}[/bold]", id=f"plabel-{name}")
                                yield Select(model_options, prompt="Model", id=f"model-{name}", value=active_model)
                                yield Button("✎", variant="default", id=f"edit-provider-btn-{name}", classes="icon-btn")
                    else:
                        yield Label("  [dim]No providers configured[/dim]")
                    yield Label("")
                    with Horizontal():
                        yield Button("+ Add Provider", variant="success", id="add-provider-btn")

                # Tab 2: Skills
                with TabPane("🛠 Skills", id="tab-skills"):
                    yield Label("")
                    yield Label("[bold]Enable / Disable Skills[/bold]")
                    yield Label("[dim]Toggle individual skills. Disabled skills are hidden from the AI.[/dim]")
                    yield Label("")
                    yield VerticalScroll(id="skill-toggle-list")

            yield Label("")
            with Horizontal(id="settings-actions"):
                yield Button("💾 Save & Close", variant="primary", id="settings-save-btn")
                yield Button("Cancel", variant="default", id="settings-cancel-btn")

    def on_mount(self) -> None:
        """Populate skill toggles."""
        self._populate_skill_toggles()

    @on(events.Click, ".provider-row")
    def _on_provider_row_click(self, event: events.Click) -> None:
        """Click provider row → switch active provider."""
        if isinstance(event.widget, (Button, Select, Input)):
            return
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

        try:
            current_label = self.query_one("#current-model-label", Label)
            current_label.update(f"  {name} / {agent.config.active_model}")
        except NoMatches:
            pass

        for row_name in agent.config.providers:
            try:
                label = self.query_one(f"#plabel-{row_name}", Label)
                marker = "[green]●[/green]" if row_name == name else "○"
                label.update(f"  {marker} [bold]{row_name}[/bold]")
            except NoMatches:
                pass

    @on(Button.Pressed, "#add-provider-btn")
    async def _on_add_provider_dialog(self) -> None:
        agent = self._home.agent
        if not agent:
            return
        existing = list(agent.config.providers.keys())
        dialog = AddProviderDialog(existing)
        worker = self.run_worker(self.app.push_screen_wait(dialog), exclusive=True)
        result = await worker.wait()
        if result:
            data = getattr(dialog, '_result', None)
            if data:
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
            worker2 = self.run_worker(self._home._show_settings(), exclusive=True)
            await worker2.wait()

    def _populate_skill_toggles(self) -> None:
        from collections import defaultdict
        from textual.widgets import Label as ModalLabel
        skill_list = self.query_one("#skill-toggle-list", VerticalScroll)
        agent = self._home.agent
        if not agent:
            return
        skills = agent.skills.list_skills()
        if not skills:
            skill_list.mount(ModalLabel("[dim]No skills found.[/dim]"))
            return
        groups: dict[str, list] = defaultdict(list)
        for s in skills:
            groups[s.get("category", "Uncategorized")].append(s)
        for cat in sorted(groups.keys(), key=lambda c: (
            -sum(1 for s in groups[c] if s.get("enabled", True)), c
        )):
            items = groups[cat]
            enabled_count = sum(1 for s in items if s.get("enabled", True))
            skill_list.mount(ModalLabel(
                f"\n[bold #5c9cf5]{cat}[/bold #5c9cf5]  [dim]({enabled_count}/{len(items)})[/dim]"
            ))
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
        agent = self._home.agent
        if not agent:
            return
        cfg = agent.config.providers.get(name)
        if not cfg:
            return
        self.run_worker(self._show_edit_provider(name, cfg), exclusive=True)

    async def _show_edit_provider(self, name: str, cfg: ProviderConfig) -> None:
        from textual.containers import Vertical as V, Horizontal as H
        from textual.widgets import Label as L, Button as B, Input as I
        from textual.screen import ModalScreen as MS

        class EditProviderDialog(MS[dict | None]):
            def compose(self2):
                with V(id="edit-provider-dialog", classes="modal-container"):
                    yield L(f" ✏ Edit Provider: {name} ", id="dialog-title")
                    yield L("")
                    yield L("[bold]Endpoint URL[/bold]")
                    yield I(value=cfg.base_url or "", id="edit-endpoint")
                    yield L("")
                    yield L("[bold]API Key[/bold]")
                    yield I(value=cfg.api_key or "", password=True, id="edit-apikey")
                    yield L("")
                    with H():
                        yield B("💾 Save", variant="primary", id="edit-save-btn")
                        yield B("Cancel", variant="default", id="edit-cancel-btn")

            def on_button_pressed(self2, event: B.Pressed):
                if event.button.id == "edit-save-btn":
                    endpoint = self2.query_one("#edit-endpoint", I).value
                    apikey = self2.query_one("#edit-apikey", I).value
                    self2.dismiss({"endpoint": endpoint, "apikey": apikey})
                elif event.button.id == "edit-cancel-btn":
                    self2.dismiss(None)

        dialog = EditProviderDialog()
        result = await self.app.push_screen_wait(dialog)
        if result:
            cfg.base_url = result["endpoint"]
            cfg.api_key = result["apikey"]
            get_auth_manager().set_key(name, result["apikey"], result["endpoint"])
            self.dismiss(None)
            await asyncio.sleep(0.1)
            await self._home._show_settings()
            self._home.notify(f"✅ Provider '{name}' updated!")

    def _save_settings(self) -> None:
        agent = self._home.agent
        if not agent:
            return
        disabled = set()
        for name, switch in self._skill_switches.items():
            if not switch.value:
                disabled.add(name)
        agent.config.skills.disabled_skills = list(disabled)
        agent.skills.disabled_skills = disabled
        agent.skills._scanned = False
        from pathlib import Path
        config_path = Path.home() / ".config" / "deBigBos" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        agent.config_manager.save(agent.config, config_path)
        self._home.notify("✅ Settings saved!", severity="success")

    def action_close(self) -> None:
        self.dismiss(None)


class AddProviderDialog(ModalScreen[str | None]):
    """Modal dialog to add a new model provider."""

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
                yield Label("[bold]Choose a preset or enter custom:[/bold]")
                preset_options = []
                for key, info in self.PRESETS.items():
                    label = info["label"]
                    if key in self._existing:
                        label += " ✓"
                    preset_options.append((label, key))
                yield Select(preset_options, id="preset-select", prompt="Select preset...", value="__custom__")
                yield Label("")
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
        preset = self.query_one("#preset-select", Select)
        preset.focus()
        self._on_preset_changed("__custom__")

    @on(Select.Changed, "#preset-select")
    def _on_preset_change(self, event: Select.Changed) -> None:
        if event.value and event.value is not Select.NULL and event.value is not Select.BLANK:
            self._on_preset_changed(event.value)

    def _on_preset_changed(self, preset_key: str) -> None:
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
            models_input.disabled = False
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
        name = self.query_one("#provider-name-input", Input).value.strip()
        base_url = self.query_one("#provider-url-input", Input).value.strip()
        api_key = self.query_one("#provider-key-input", Input).value.strip()
        models_str = self.query_one("#provider-models-input", Input).value.strip()
        default_model = self.query_one("#provider-default-model-input", Input).value.strip()

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

        if api_key:
            get_auth_manager().set_key(name, api_key, base_url)

        self._result = {
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
            "default_model": default_model,
        }
        self.dismiss(name)
