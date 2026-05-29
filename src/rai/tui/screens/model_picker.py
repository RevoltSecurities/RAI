"""ModelPickerScreen — scrollable, fuzzy model selector for /model command."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static


# ---------------------------------------------------------------------------
# Static model catalogue — grouped by provider
# ---------------------------------------------------------------------------

_MODEL_CATALOGUE: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-opus-4-5-20251101",
        "claude-opus-4-20250514",
        "claude-opus-4-1",
        "claude-opus-4-1-20250805",
        "claude-opus-4-0",
        "claude-3-opus-20240229",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-sonnet-4-0",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20240620",
        "claude-3-sonnet-20240229",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-3-5-haiku-20241022",
        "claude-3-5-haiku-latest",
        "claude-3-haiku-20240307",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4o-2024-11-20",
        "gpt-4o-2024-08-06",
        "gpt-4o-2024-05-13",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-pro",
        "gpt-5-nano",
        "gpt-5-codex",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.4-pro",
        "o1",
        "o1-pro",
        "o3",
        "o3-mini",
        "o3-pro",
        "o4-mini",
        "bedrock-claude-sonnet-4.6-(US)",
        "bedrock-claude-sonnet-4.5-(US)",
        "bedrock-claude-opus-4.6-(US)",
    ],
    "google_genai": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
        "gemma-4-31b-it",
        "gemma-4-26b-it",
        "gemma-3-27b-it",
    ],
    "azure_openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "o3",
        "o3-mini",
        "o4-mini",
    ],
    "litellm": [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-7",
        "vertex_ai/gemini-2.5-pro",
    ],
}

# Flat list: (spec, provider)  — insertion order = display order
_ALL_MODELS: list[tuple[str, str]] = [
    (f"{provider}:{model}", provider)
    for provider, models in _MODEL_CATALOGUE.items()
    for model in models
]


# ---------------------------------------------------------------------------
# Row widget
# ---------------------------------------------------------------------------

class _ModelRow(Static):
    """Single model row — clickable, highlight via CSS class."""

    class Clicked(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, spec: str, index: int, *, current: bool = False) -> None:
        super().__init__("", classes="model-row")
        self.model_spec = spec
        self.row_index = index
        self._current = current
        self._render_label(selected=False)

    def _render_label(self, *, selected: bool) -> None:
        cursor = "❯ " if selected else "  "
        spec_part = self.model_spec
        suffix = "  [dim](current)[/dim]" if self._current else ""
        if selected:
            self.update(f"[bold reverse] {cursor}{spec_part}{suffix} [/bold reverse]")
        else:
            self.update(f" {cursor}{spec_part}{suffix}")

    def set_selected(self, selected: bool) -> None:
        self._render_label(selected=selected)
        if selected:
            self.add_class("model-row-selected")
        else:
            self.remove_class("model-row-selected")

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.row_index))


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class ModelPickerScreen(ModalScreen[str | None]):
    """Full-screen modal model selector with fuzzy search and keyboard nav."""

    DEFAULT_CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    ModelPickerScreen > Vertical {
        width: 82;
        max-width: 95%;
        height: 85%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    ModelPickerScreen #model-title {
        text-style: bold;
        color: $accent;
        text-align: center;
        margin-bottom: 1;
    }
    ModelPickerScreen #model-filter {
        border: round $accent 40%;
        margin-bottom: 1;
    }
    ModelPickerScreen #model-filter:focus {
        border: round $accent;
    }
    ModelPickerScreen .model-scroll {
        height: 1fr;
        border: round $accent 20%;
        background: $background;
    }
    ModelPickerScreen #model-options {
        height: auto;
    }
    ModelPickerScreen .provider-header {
        color: $accent;
        text-style: bold;
        padding: 0 1;
        margin-top: 1;
    }
    ModelPickerScreen #model-options > .provider-header:first-child {
        margin-top: 0;
    }
    ModelPickerScreen .model-row {
        height: 1;
        padding: 0 1;
    }
    ModelPickerScreen .model-row:hover {
        background: $surface-lighten-1;
    }
    ModelPickerScreen .model-row-selected {
        background: $accent 30%;
    }
    ModelPickerScreen #model-help {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("up",       "move_up",       "Up",       show=False, priority=True),
        Binding("down",     "move_down",     "Down",     show=False, priority=True),
        Binding("pageup",   "page_up",       "PgUp",     show=False, priority=True),
        Binding("pagedown", "page_down",     "PgDn",     show=False, priority=True),
        Binding("tab",      "tab_complete",  "Complete", show=False, priority=True),
        Binding("enter",    "select_model",  "Select",   show=False, priority=True),
        Binding("escape",   "cancel",        "Cancel",   show=False),
    ]

    def __init__(self, current_override: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_override = current_override
        self._filter_text = ""
        self._filtered: list[tuple[str, str]] = list(_ALL_MODELS)
        self._selected_index = 0
        self._row_widgets: list[_ModelRow] = []
        self._options_container: Container | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            if self._current_override:
                yield Static(
                    f"Select Model  [dim]override: [yellow]{self._current_override}[/yellow][/dim]",
                    id="model-title",
                )
            else:
                yield Static("Select Model", id="model-title")
            yield Input(
                placeholder="Type to filter or enter provider:model…",
                id="model-filter",
            )
            with VerticalScroll(classes="model-scroll"):
                self._options_container = Container(id="model-options")
                yield self._options_container
            yield Static(
                "↑↓ navigate  ·  enter select  ·  tab copy spec  ·  esc cancel",
                id="model-help",
            )
            yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#model-filter", Input).focus()
        await self._rebuild_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter_text = event.value
        self._apply_filter()
        self.call_after_refresh(self._rebuild_list)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_select_model()

    def on__model_row_clicked(self, event: _ModelRow.Clicked) -> None:
        if event.index != self._selected_index:
            self._move_to(event.index)
        else:
            # double-click or single click on already-selected → select
            if self._filtered:
                spec = self._filtered[self._selected_index][0]
                self.dismiss(spec)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        q = self._filter_text.strip().lower()
        if not q:
            self._filtered = list(_ALL_MODELS)
        else:
            scored: list[tuple[int, str, str]] = []
            for spec, provider in _ALL_MODELS:
                s = spec.lower()
                if q in s:
                    scored.append((0 if s.startswith(q) else 1, spec, provider))
                elif any(t in s for t in q.split()):
                    scored.append((2, spec, provider))
            scored.sort(key=lambda x: x[0])
            self._filtered = [(spec, prov) for _, spec, prov in scored]
        self._selected_index = 0

    # ------------------------------------------------------------------
    # DOM rebuild
    # ------------------------------------------------------------------

    async def _rebuild_list(self) -> None:
        if not self._options_container:
            return
        await self._options_container.remove_children()
        self._row_widgets = []

        if not self._filtered:
            val = self._filter_text.strip()
            if val and (":" in val or "/" in val):
                msg = f"No match — press [bold]enter[/bold] to use [cyan]{val}[/cyan] as custom spec"
            elif val:
                msg = f"No match — try [cyan]provider:{val}[/cyan]"
            else:
                msg = "[dim]No models[/dim]"
            await self._options_container.mount(Static(msg))
            return

        # Group by provider preserving order
        by_provider: dict[str, list[tuple[str, str]]] = {}
        for spec, prov in self._filtered:
            by_provider.setdefault(prov, []).append((spec, prov))

        widgets: list[Static | _ModelRow] = []
        flat_idx = 0
        for provider, entries in by_provider.items():
            widgets.append(Static(provider, classes="provider-header"))
            for spec, _ in entries:
                is_current = spec == self._current_override
                row = _ModelRow(spec, flat_idx, current=is_current)
                if flat_idx == self._selected_index:
                    row.set_selected(True)
                self._row_widgets.append(row)
                widgets.append(row)
                flat_idx += 1

        await self._options_container.mount(*widgets)
        self._scroll_selected_into_view()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _move_to(self, new_idx: int) -> None:
        if not self._filtered or not self._row_widgets:
            return
        old_idx = self._selected_index
        new_idx = max(0, min(new_idx, len(self._filtered) - 1))
        if old_idx == new_idx:
            return
        if 0 <= old_idx < len(self._row_widgets):
            self._row_widgets[old_idx].set_selected(False)
        self._selected_index = new_idx
        if 0 <= new_idx < len(self._row_widgets):
            self._row_widgets[new_idx].set_selected(True)
            self._scroll_selected_into_view()

    def _scroll_selected_into_view(self) -> None:
        if 0 <= self._selected_index < len(self._row_widgets):
            self._row_widgets[self._selected_index].scroll_visible(animate=False)

    def _page_size(self) -> int:
        try:
            return max(1, self.query_one(".model-scroll", VerticalScroll).size.height - 2)
        except Exception:
            return 10

    def action_move_up(self) -> None:
        self._move_to(self._selected_index - 1)

    def action_move_down(self) -> None:
        self._move_to(self._selected_index + 1)

    def action_page_up(self) -> None:
        self._move_to(max(0, self._selected_index - self._page_size()))

    def action_page_down(self) -> None:
        self._move_to(min(len(self._filtered) - 1, self._selected_index + self._page_size()))

    def action_tab_complete(self) -> None:
        if self._filtered:
            spec = self._filtered[self._selected_index][0]
            inp = self.query_one("#model-filter", Input)
            inp.value = spec
            inp.cursor_position = len(spec)

    def action_select_model(self) -> None:
        if self._filtered:
            spec = self._filtered[self._selected_index][0]
            self.dismiss(spec)
            return
        val = self._filter_text.strip()
        if val:
            if ":" not in val and "/" not in val:
                self.app.notify(
                    f"Use format [bold]provider:model[/bold]  (e.g. [cyan]anthropic:{val}[/cyan])",
                    timeout=4,
                )
                return
            self.dismiss(val)
            return
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
