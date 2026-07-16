"""Dialog components — Alert, Confirm, Prompt, Select.

Inspired by OpenCode's DialogAlert, DialogConfirm, DialogPrompt, DialogSelect.
Built as Textual ModalScreen subclasses.
"""

from __future__ import annotations

from typing import Any, Callable

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static


class DialogAlert(ModalScreen[bool]):
    """Simple alert dialog with a message and OK button."""

    DEFAULT_CSS = """
    DialogAlert {
        align: center middle;
    }
    DialogAlert > Center {
        width: 50;
        max-height: 20;
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
    }
    DialogAlert Label {
        width: 100%;
        text-align: center;
    }
    DialogAlert #alert-message {
        margin: 1 0;
    }
    DialogAlert Horizontal {
        width: 100%;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        title: str = "Alert",
        message: str = "",
        on_confirm: Callable[[], Any] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._message = message
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical():
                yield Label(self._title, classes="bold")
                yield Label(self._message, id="alert-message")
                with Horizontal():
                    yield Button("OK", variant="primary")

    @on(Button.Pressed)
    def _ok(self) -> None:
        if self._on_confirm:
            self._on_confirm()
        self.dismiss(True)


class DialogConfirm(ModalScreen[bool]):
    """Confirm dialog with Yes/No buttons."""

    DEFAULT_CSS = """
    DialogConfirm {
        align: center middle;
    }
    DialogConfirm > Center {
        width: 50;
        max-height: 22;
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
    }
    DialogConfirm Label {
        width: 100%;
        text-align: center;
    }
    DialogConfirm #confirm-message {
        margin: 1 0;
    }
    DialogConfirm Horizontal {
        width: 100%;
        align: center middle;
        margin-top: 1;
        column-span: 2;
    }
    """

    def __init__(
        self,
        title: str = "Confirm",
        message: str = "",
        on_confirm: Callable[[], Any] | None = None,
        on_cancel: Callable[[], Any] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._message = message
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical():
                yield Label(self._title, classes="bold")
                yield Label(self._message, id="confirm-message")
                with Horizontal():
                    yield Button("Yes", variant="primary", id="confirm-yes")
                    yield Button("No", variant="default", id="confirm-no")

    @on(Button.Pressed, "#confirm-yes")
    def _yes(self) -> None:
        if self._on_confirm:
            self._on_confirm()
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def _no(self) -> None:
        if self._on_cancel:
            self._on_cancel()
        self.dismiss(False)


class DialogPrompt(ModalScreen[str | None]):
    """Text input prompt dialog."""

    DEFAULT_CSS = """
    DialogPrompt {
        align: center middle;
    }
    DialogPrompt > Center {
        width: 50;
        max-height: 22;
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
    }
    DialogPrompt Label {
        width: 100%;
        text-align: center;
    }
    DialogPrompt Input {
        margin: 1 0;
        width: 100%;
    }
    DialogPrompt Horizontal {
        width: 100%;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        title: str = "Prompt",
        value: str = "",
        on_confirm: Callable[[str], Any] | None = None,
        on_cancel: Callable[[], Any] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._initial_value = value
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical():
                yield Label(self._title, classes="bold")
                yield Input(value=self._initial_value, id="prompt-input")
                with Horizontal():
                    yield Button("OK", variant="primary", id="prompt-ok")
                    yield Button("Cancel", variant="default", id="prompt-cancel")

    @on(Button.Pressed, "#prompt-ok")
    def _ok(self) -> None:
        value = self.query_one("#prompt-input", Input).value
        if self._on_confirm:
            self._on_confirm(value)
        self.dismiss(value)

    @on(Button.Pressed, "#prompt-cancel")
    def _cancel(self) -> None:
        if self._on_cancel:
            self._on_cancel()
        self.dismiss(None)


class DialogSelect(ModalScreen[dict[str, Any]]):
    """Selection dialog with a list of options."""

    DEFAULT_CSS = """
    DialogSelect {
        align: center middle;
    }
    DialogSelect > Center {
        width: 50;
        height: 20;
        background: #212121;
        border: thick #fab283;
        padding: 1 2;
    }
    DialogSelect Label {
        width: 100%;
        text-align: center;
    }
    DialogSelect ListView {
        height: 1fr;
        margin: 1 0;
    }
    DialogSelect Horizontal {
        width: 100%;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        title: str = "Select",
        options: list[dict[str, Any]] | None = None,
        current: int = 0,
        on_select: Callable[[dict[str, Any]], Any] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._options = options or []
        self._current = current
        self._on_select = on_select

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical():
                yield Label(self._title, classes="bold")
                yield ListView(
                    *[
                        ListItem(Label(opt.get("title", opt.get("label", str(opt)))))
                        for opt in self._options
                    ],
                    initial_index=self._current,
                )
                with Horizontal():
                    yield Button("Select", variant="primary", id="select-select")
                    yield Button("Cancel", variant="default", id="select-cancel")

    @on(Button.Pressed, "#select-select")
    def _select(self) -> None:
        list_view = self.query_one(ListView)
        idx = list_view.index
        if 0 <= idx < len(self._options):
            item = self._options[idx]
            if self._on_select:
                self._on_select(item)
            self.dismiss(item)
        else:
            self.dismiss({})

    @on(Button.Pressed, "#select-cancel")
    def _cancel(self) -> None:
        self.dismiss({})
