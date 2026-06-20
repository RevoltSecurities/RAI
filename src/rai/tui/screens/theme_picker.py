"""ThemePickerScreen — Ctrl+T modal to switch Textual themes."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, SelectionList, Static


class ThemePickerScreen(ModalScreen):
    DEFAULT_CSS = """
    ThemePickerScreen {
        align: center middle;
    }
    #theme-modal-shell {
        width: 50;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    #theme-header {
        height: 1;
        margin-bottom: 1;
    }
    #theme-header-title {
        width: 1fr;
    }
    #theme-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }
    #theme-modal-shell SelectionList {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, current_theme: str = "rai", **kwargs) -> None:
        super().__init__(**kwargs)
        self._current = current_theme

    def compose(self) -> ComposeResult:
        with Container(id="theme-modal-shell"):
            with Horizontal(id="theme-header"):
                yield Label("Theme Picker  [dim][enter] apply[/dim]", id="theme-header-title")
                yield Static("[bold red]\\[x][/bold red]", id="theme-close")
            # Build list from registered themes only — avoids InvalidThemeError
            available = sorted(self.app.available_themes.keys())
            if not available:
                available = ["rai", "github-dark", "glass", "textual-dark", "textual-light", "monokai", "tokyo-night"]
            options = [(t, t, t == self._current) for t in available]
            yield SelectionList(*options, id="theme-list")
            yield Footer()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        selected = event.selection_list.selected
        if selected:
            theme = selected[-1]
            if theme in self.app.available_themes:
                self.app.theme = theme  # type: ignore[attr-defined]
                self.dismiss(theme)

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "theme-close":
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
