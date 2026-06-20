"""SettingsScreen — modern modal for model, theme, and permission mode."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option

_MODELS = [
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5",
]

_THEMES = ["rai", "github-dark", "glass", "claude"]

_MODES = [
    ("HITL (default)", "default"),
    ("Autonomous", "bypassPermissions"),
    ("Plan", "plan"),
]


class SettingsScreen(ModalScreen[None]):
    """Settings modal — apply changes immediately on selection."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Close", priority=True)]

    CSS = """
    SettingsScreen {
        align: center middle;
        background: $background 65%;
    }

    #settings-container {
        width: 62%;
        max-width: 72;
        height: auto;
        max-height: 85%;
        border: round $primary 50%;
        background: $surface;
        padding: 0 0 1 0;
    }

    #settings-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #settings-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #settings-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #settings-body {
        padding: 1 2;
        height: auto;
    }

    .section-label {
        color: $accent;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 0;
    }

    .section-rule {
        color: $primary 30%;
        margin-bottom: 0;
    }

    #model-list {
        height: 5;
        background: $panel;
        border: round $primary 30%;
        margin-top: 0;
    }

    #theme-set, #mode-set {
        height: auto;
        background: $panel;
        border: round $primary 30%;
        margin-top: 0;
    }

    RadioButton {
        background: transparent;
    }

    RadioButton:focus {
        background: $primary 15%;
    }

    #settings-applied {
        height: 1;
        color: $success;
        margin-top: 1;
        text-align: right;
    }
    """

    def __init__(
        self,
        current_model: str,
        current_theme: str,
        current_mode: str,
    ) -> None:
        super().__init__()
        self._selected_model = current_model
        self._current_theme  = current_theme
        self._selected_mode  = current_mode
        self._applied_timer  = None

    def compose(self) -> ComposeResult:
        with Container(id="settings-container"):
            with Horizontal(id="settings-header"):
                yield Static("Settings", id="settings-title")
                yield Static("[bold red]\\[x][/bold red]", id="settings-close")

            with Container(id="settings-body"):
                yield Static("Model", classes="section-label")
                yield Static("─" * 40, classes="section-rule")
                model_opts = [Option(m, id=m) for m in _MODELS]
                yield OptionList(*model_opts, id="model-list")

                yield Static("Theme", classes="section-label")
                yield Static("─" * 40, classes="section-rule")
                theme_buttons = [
                    RadioButton(t, value=(t == self._current_theme))
                    for t in _THEMES
                ]
                yield RadioSet(*theme_buttons, id="theme-set")

                yield Static("Mode", classes="section-label")
                yield Static("─" * 40, classes="section-rule")
                mode_buttons = [
                    RadioButton(label, value=(mode_id == self._selected_mode))
                    for label, mode_id in _MODES
                ]
                yield RadioSet(*mode_buttons, id="mode-set")

                yield Static("", id="settings-applied")

    def on_mount(self) -> None:
        option_list = self.query_one("#model-list", OptionList)
        for i, m in enumerate(_MODELS):
            if m == self._selected_model:
                option_list.highlighted = i
                break

    def _show_applied(self, text: str = "✓ Applied") -> None:
        try:
            st = self.query_one("#settings-applied", Static)
            st.update(text)
            if self._applied_timer:
                self._applied_timer.stop()
            self._applied_timer = self.set_timer(1.5, lambda: st.update(""))
        except Exception:
            pass

    @on(OptionList.OptionSelected, "#model-list")
    def on_model_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self._selected_model = event.option_id
            self.app._current_model = event.option_id
            self._apply_model(event.option_id)
            self._show_applied(f"✓ Model: {event.option_id}")

    @work
    async def _apply_model(self, model: str) -> None:
        client     = self.app._client
        session_id = self.app._session_id
        if client and session_id and session_id != "default":
            try:
                await client.sessions.set_model(session_id, model)
            except Exception:
                pass

    @on(RadioSet.Changed, "#theme-set")
    def on_theme_changed(self, event: RadioSet.Changed) -> None:
        idx = event.index
        if idx is not None and 0 <= idx < len(_THEMES):
            self.app.theme    = _THEMES[idx]
            self._current_theme = _THEMES[idx]
            self._show_applied(f"✓ Theme: {_THEMES[idx]}")

    @on(RadioSet.Changed, "#mode-set")
    def on_mode_changed(self, event: RadioSet.Changed) -> None:
        idx = event.index
        if idx is not None and 0 <= idx < len(_MODES):
            mode_id = _MODES[idx][1]
            self._selected_mode    = mode_id
            self.app._permission_mode = mode_id
            self._apply_mode(mode_id)
            self._show_applied(f"✓ Mode: {_MODES[idx][0]}")

    @work
    async def _apply_mode(self, mode: str) -> None:
        client     = self.app._client
        session_id = self.app._session_id
        if client and session_id and session_id != "default":
            try:
                await client.sessions.set_permission_mode(session_id, mode)
            except Exception:
                pass

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "settings-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
