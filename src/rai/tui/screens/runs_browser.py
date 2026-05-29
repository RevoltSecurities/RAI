"""RunsBrowserScreen — Ctrl+O modal to view/attach all runs."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label


class RunsBrowserScreen(ModalScreen):
    DEFAULT_CSS = """
    RunsBrowserScreen {
        align: center middle;
    }
    #runs-modal-shell {
        width: 92%;
        height: 80%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    #runs-modal-shell DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    class RunSelected(Message):
        def __init__(self, run_id: str, agent: str, status: str) -> None:
            super().__init__()
            self.run_id = run_id
            self.agent = agent
            self.status = status

    def __init__(self, runs: list[dict], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._runs = runs

    def compose(self) -> ComposeResult:
        with Container(id="runs-modal-shell"):
            yield Label("Runs Browser  [dim][enter] attach  [esc] close[/dim]")
            table = DataTable(cursor_type="row", id="runs-table")
            table.add_columns("run_id", "agent", "status", "duration", "created")
            for run in self._runs:
                full_run_id = str(run.get("run_id", ""))
                short_id = full_run_id[:12]
                agent = str(run.get("agent_name", run.get("agent", "")) or "")
                status = str(run.get("status", ""))
                dur_ms = run.get("duration_ms")
                duration = f"{dur_ms / 1000:.1f}s" if dur_ms else "—"
                raw_ts = str(run.get("created_at", "") or "")
                created = raw_ts[:16].replace("T", " ") if raw_ts else "—"
                table.add_row(short_id, agent, status, duration, created, key=full_run_id)
            yield table
            yield Footer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        full_run_id = str(event.row_key.value) if event.row_key else ""
        for run in self._runs:
            if str(run.get("run_id", "")) == full_run_id:
                self.dismiss(
                    RunsBrowserScreen.RunSelected(
                        full_run_id,
                        str(run.get("agent_name", run.get("agent", "")) or ""),
                        str(run.get("status", "")),
                    )
                )
                return
        self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
