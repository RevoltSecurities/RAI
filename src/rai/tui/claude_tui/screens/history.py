"""HistoryScreen — modern modal session picker for /resume command."""

from __future__ import annotations

from typing import Any

import time
from datetime import datetime, timezone

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from rai.client.claude import ClaudeServeClient


def _human_date(raw: str | int | float | None) -> str:
    """Convert Unix timestamp or ISO date string to human-readable relative time."""
    if not raw:
        return ""
    try:
        ts = float(raw)
        # Unix timestamps from Claude sessions are in seconds
        # Values > 1e10 are milliseconds
        if ts > 1e10:
            ts /= 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        # Try ISO string fallback
        try:
            s = str(raw)[:19].replace("T", " ")
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return str(raw)[:10]

    now   = datetime.now(tz=timezone.utc)
    delta = now - dt
    secs  = int(delta.total_seconds())

    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m}m ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h}h ago"
    if secs < 86400 * 2:
        return "yesterday"
    if secs < 86400 * 7:
        d = secs // 86400
        return f"{d}d ago"
    if secs < 86400 * 30:
        w = secs // (86400 * 7)
        return f"{w}w ago"
    # Older — show actual date
    return dt.strftime("%b %d")


class HistoryScreen(ModalScreen[str | None]):
    """Session history picker — select a session to resume."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", priority=True),
        Binding("enter",  "select_session", "Select", priority=True),
    ]

    CSS = """
    HistoryScreen {
        align: center middle;
        background: $background 65%;
    }

    #history-container {
        width: 92%;
        max-width: 140;
        height: 80%;
        border: round $primary 50%;
        background: $surface;
        padding: 0;
    }

    #history-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #history-title {
        text-style: bold;
        width: 1fr;
        color: $primary;
    }

    #history-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #history-status {
        height: 1;
        color: $text-muted;
        padding: 0 2;
    }

    #session-table {
        height: 1fr;
        margin: 0 1;
        scrollbar-size: 1 1;
    }

    #history-hint {
        height: 2;
        padding: 0 2;
        color: $text-muted;
        border-top: solid $primary 15%;
        content-align: left middle;
    }
    """

    def __init__(self, client: ClaudeServeClient) -> None:
        super().__init__()
        self._client = client
        self._session_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="history-container"):
            with Horizontal(id="history-header"):
                yield Static("Resume Session", id="history-title")
                yield Static("[bold red]\\[x][/bold red]", id="history-close")
            yield Static("[dim]Loading…[/dim]", id="history-status")
            table = DataTable(id="session-table", cursor_type="row", zebra_stripes=True)
            yield table
            yield Static(
                "[dim]↑↓[/dim] navigate  [dim]·[/dim]  [dim]enter[/dim] select  [dim]·[/dim]  [dim]esc[/dim] close",
                id="history-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#session-table", DataTable)
        table.add_columns("Title", "Date", "Session ID")
        self._load_sessions()

    @work
    async def _load_sessions(self) -> None:
        try:
            result = await self._client.history.list(limit=50)
            sessions = result.get("sessions", [])
        except Exception:
            sessions = []

        table  = self.query_one("#session-table", DataTable)
        status = self.query_one("#history-status", Static)
        self._session_ids = []

        if not sessions:
            status.update("[dim]No sessions found[/dim]")
            return

        for s in sessions:
            title    = s.get("custom_title") or s.get("first_prompt") or s.get("summary") or "untitled"
            title    = title[:70]
            modified = _human_date(s.get("last_modified"))
            sid      = s.get("session_id", "")
            short_id = sid[:16] + "…" if len(sid) > 16 else sid
            self._session_ids.append(sid)
            table.add_row(
                escape(title),
                f"[dim]{escape(modified)}[/dim]",
                f"[dim]{escape(short_id)}[/dim]",
            )

        count = len(sessions)
        status.update(f"[dim]{count} session{'s' if count != 1 else ''}[/dim]")

        # Auto-focus the table
        self.call_after_refresh(table.focus)

    def action_select_session(self) -> None:
        table = self.query_one("#session-table", DataTable)
        row   = table.cursor_row
        if 0 <= row < len(self._session_ids):
            self.dismiss(self._session_ids[row])

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if 0 <= row < len(self._session_ids):
            self.dismiss(self._session_ids[row])

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "history-close":
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
