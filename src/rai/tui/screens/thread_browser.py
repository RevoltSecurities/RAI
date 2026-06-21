"""ThreadBrowserScreen — Ctrl+R modal to resume a thread."""

from __future__ import annotations

import os
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, Static


class ThreadBrowserScreen(ModalScreen):
    DEFAULT_CSS = """
    ThreadBrowserScreen {
        align: center middle;
    }
    #thread-modal-shell {
        width: 90%;
        height: 75%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    #thread-header {
        height: 1;
        margin-bottom: 1;
    }
    #thread-header-title {
        width: 1fr;
    }
    #thread-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }
    #thread-modal-shell DataTable {
        height: 1fr;
    }
    """

    class ThreadSelected(Message):
        def __init__(self, thread_id: str, agent: str) -> None:
            super().__init__()
            self.thread_id = thread_id
            self.agent = agent

    def __init__(self, threads: list[dict], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._threads = threads

    def compose(self) -> ComposeResult:
        with Container(id="thread-modal-shell"):
            with Horizontal(id="thread-header"):
                yield Label("Thread Browser  [dim][enter] resume[/dim]", id="thread-header-title")
                yield Static("[bold red]\\[x][/bold red]", id="thread-close")
            table = DataTable(cursor_type="row", id="thread-table")
            table.add_columns("id", "agent", "branch", "cwd", "updated", "prompt")
            for t in self._threads:
                full_id = str(t.get("thread_id", ""))
                short_id = full_id[:8]
                agent = str(t.get("agent_name", t.get("agent", "")) or "")
                branch = str(t.get("git_branch", "") or "") or "—"
                raw_cwd = str(t.get("cwd", "") or "")
                cwd = os.path.basename(raw_cwd) if raw_cwd else "—"
                raw_ts = str(t.get("updated_at", "") or "")
                updated = raw_ts[:16].replace("T", " ") if raw_ts else "—"
                prompt = str(t.get("initial_prompt", "") or "")[:50]
                table.add_row(short_id, agent, branch, cwd, updated, prompt, key=full_id)
            yield table
            yield Footer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        full_id = str(event.row_key.value) if event.row_key else ""
        for t in self._threads:
            if str(t.get("thread_id", "")) == full_id:
                self.dismiss(
                    ThreadBrowserScreen.ThreadSelected(
                        full_id,
                        str(t.get("agent_name", t.get("agent", "")) or ""),
                    )
                )
                return
        self.dismiss(None)

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "thread-close":
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
