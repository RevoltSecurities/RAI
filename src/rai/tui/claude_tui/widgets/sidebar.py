"""SessionSidebar — animated slide-in panel with recent sessions."""

from __future__ import annotations

from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.markup import escape
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rai.tui.claude_tui.screens.history import _human_date


class SessionSidebar(Widget):
    """Animated sidebar — slides from left, shows recent sessions for quick switch."""

    DEFAULT_CSS = """
    SessionSidebar {
        width: 38;
        layer: sidebar;
        dock: left;
        offset-x: -100%;
        background: $surface;
        border-right: solid $primary 20%;
        transition: offset 200ms in_out_cubic;

        &.-visible {
            offset-x: 0;
        }

        #sidebar-title {
            text-style: bold;
            color: $primary;
            margin: 1 1 0 1;
            border-bottom: solid $primary 15%;
            padding-bottom: 1;
        }

        #sidebar-list {
            margin: 0;
            background: transparent;
            scrollbar-size: 0 0;
            border: none;
        }
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._client: Any = None
        self._loaded: bool = False

    def compose(self) -> ComposeResult:
        yield Static("Sessions", id="sidebar-title")
        yield OptionList(id="sidebar-list")

    @work
    async def load_sessions(self) -> None:
        if self._client is None:
            return
        try:
            result = await self._client.history.list(limit=20)
            sessions = result.get("sessions", [])
        except Exception:
            sessions = []

        option_list = self.query_one("#sidebar-list", OptionList)
        option_list.clear_options()

        if not sessions:
            option_list.add_option(Option("No sessions found", id="__none__", disabled=True))
            self._loaded = True
            return

        for s in sessions:
            title    = s.get("custom_title") or s.get("first_prompt") or s.get("summary") or "untitled"
            title    = title[:32]
            modified = _human_date(s.get("last_modified"))
            label    = f"[bold]{escape(title)}[/bold]  [white]{escape(modified)}[/white]"
            option_list.add_option(Option(label, id=s.get("session_id", "")))

        self._loaded = True

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id and event.option_id != "__none__":
            self.app.action_switch_session(event.option_id)
