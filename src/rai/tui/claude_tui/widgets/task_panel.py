"""TaskMonitorPanel — right-side live panel for Task and Monitor events.

Slides in from the right (same cubic-easing animation as SessionSidebar).
Auto-opens on first TaskCreate or Monitor (local_bash) event.
Toggleable via ctrl+t or the [x] close button.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.markup import escape
from textual.widget import Widget
from textual.widgets import Static

_BLOOM = ("·", ".", "+", "✶", "✻", "✽", "✶", "+", "·", ".")

# Full Rich markup per status — colors embedded directly, no CSS class dependency
_STATUS_FMT = {
    "pending":     ("[red]○[/red]",      "$text"),
    "in_progress": ("[yellow]▶[/yellow]", "yellow"),
    "completed":   ("[green]✓[/green]",  "green"),
    "deleted":     ("[red]✗[/red]",      "red"),
}


# ─────────────────────────────────────────────────────────────────────────────
# TaskRow
# ─────────────────────────────────────────────────────────────────────────────

class TaskRow(Static):
    """One row per task — colors embedded in Rich markup, no CSS class dependency."""

    DEFAULT_CSS = """
    TaskRow {
        height: auto;
        padding: 0 1;
        margin: 0;
    }
    TaskRow:hover { background: $primary 8%; }
    """

    def __init__(self, tmp_id: str, subject: str) -> None:
        self._tmp_id  = tmp_id
        self._task_id = ""
        self._subject = subject
        self._status  = "pending"
        super().__init__("", id=f"tpr-{tmp_id}")

    def on_mount(self) -> None:
        self.update(self._markup())

    def set_real_id(self, task_id: str) -> None:
        self._task_id = task_id
        self.update(self._markup())

    def set_status(self, status: str, subject: str = "") -> None:
        self._status = status
        if subject:
            self._subject = subject
        self.update(self._markup())

    def _markup(self) -> str:
        icon, text_color = _STATUS_FMT.get(self._status, ("[dim]?[/dim]", "dim"))
        subj = escape(self._subject[:34])
        tid  = f"[dim]{self._task_id[:8]}[/dim]" if self._task_id else "[dim]…[/dim]"
        # Wrap subject in status color so entire row reflects state
        if text_color not in ("$text",):
            return f"  {icon}  [{text_color}]{subj}[/{text_color}]  {tid}"
        return f"  {icon}  [bold]{subj}[/bold]  {tid}"


# ─────────────────────────────────────────────────────────────────────────────
# MonitorRow
# ─────────────────────────────────────────────────────────────────────────────

class MonitorRow(Static):
    """One row per Monitor watch — braille spinner, never auto-removes."""

    DEFAULT_CSS = """
    MonitorRow {
        height: auto;
        padding: 0 1;
        margin: 0;
        color: $text-muted;
    }
    MonitorRow.--done-ok  { color: $success; }
    MonitorRow.--done-err { color: $error;   }
    """

    def __init__(self, task_id: str, description: str) -> None:
        self._task_id      = task_id
        self._description  = description
        self._stdout_line: str  = ""   # latest stdout line from Monitor
        self._tokens:      int  = 0
        self._done:        bool = False
        self._status:      str  = ""
        self._output_file: str  = ""
        self._spin_pos:    int  = 0
        self._timer:       Any  = None
        super().__init__("", id=f"mpr-{task_id}")

    def on_mount(self) -> None:
        self.update(self._markup())
        self._timer = self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        if self._done:
            if self._timer:
                self._timer.stop()
                self._timer = None
            return
        self._spin_pos += 1
        self.update(self._markup())

    def _markup(self) -> str:
        desc  = escape(self._description[:28])
        if self._done:
            icon = "[green]✓[/green]" if self._status == "completed" else "[red]✗[/red]"
            # Show output file path hint if available
            file_hint = f"  [dim]{escape(self._output_file[-30:])}[/dim]" if self._output_file else ""
            return f"  {icon} 👁 [bold]{desc}[/bold]{file_hint}"
        frame = _BLOOM[self._spin_pos % len(_BLOOM)]
        tok   = f"  [dim]{self._tokens} tok[/dim]" if self._tokens else ""
        # Show latest stdout line if available, otherwise just description
        if self._stdout_line:
            line = escape(self._stdout_line[:32])
            return f"  [yellow]{frame}[/yellow] 👁 [dim]{desc}[/dim]  [cyan]{line}[/cyan]{tok}"
        return f"  [yellow]{frame}[/yellow] 👁 [bold]{desc}[/bold]{tok}"

    def update_progress(self, description: str, last_tool: str, tokens: int) -> None:
        # For Monitor: description is the latest stdout line
        if description:
            self._stdout_line = description
        self._tokens = tokens

    def mark_done(self, status: str, summary: str, output_file: str = "") -> None:
        self._done        = True
        self._status      = status
        self._output_file = output_file
        self.add_class("--done-ok" if status == "completed" else "--done-err")
        self.update(self._markup())


# ─────────────────────────────────────────────────────────────────────────────
# TaskMonitorPanel
# ─────────────────────────────────────────────────────────────────────────────

class TaskMonitorPanel(Widget):
    """Right-side panel showing live task and monitor rows.

    Docks to the right edge, hidden off-screen until .-visible is applied.
    Same slide animation pattern as SessionSidebar.
    """

    DEFAULT_CSS = """
    TaskMonitorPanel {
        width: 42;
        layer: tasks;
        dock: right;
        offset-x: 100%;
        background: $surface;
        border-left: solid $primary 25%;
        transition: offset 200ms in_out_cubic;
    }

    TaskMonitorPanel.-visible {
        offset-x: 0;
    }

    #tp-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 15%;
        padding: 0 2;
        align: left middle;
    }

    #tp-header-title {
        width: 1fr;
        color: $primary;
        text-style: bold;
    }

    #tp-ctrl-hint {
        color: $text-muted;
        width: auto;
        margin-right: 1;
    }

    #tp-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #tp-tasks-hdr, #tp-monitors-hdr {
        color: $accent;
        text-style: bold;
        margin: 1 1 0 1;
    }

    .tp-section-rule {
        color: $primary 20%;
        margin: 0 1 0 1;
    }

    #tp-tasks-scroll, #tp-monitors-scroll {
        height: auto;
        max-height: 12;
        margin: 0 0 1 0;
        scrollbar-size: 0 0;
        background: transparent;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="tp-header"):
            yield Static("📋 Tasks & Monitors", id="tp-header-title")
            yield Static("[dim]ctrl+t[/dim]", id="tp-ctrl-hint")
            yield Static("[bold red]\\[x][/bold red]", id="tp-close")
        yield Static("Tasks", id="tp-tasks-hdr")
        yield Static("─" * 38, classes="tp-section-rule", id="tp-tasks-rule")
        yield VerticalScroll(id="tp-tasks-scroll")
        yield Static("Monitors", id="tp-monitors-hdr")
        yield Static("─" * 38, classes="tp-section-rule", id="tp-monitors-rule")
        yield VerticalScroll(id="tp-monitors-scroll")

    def on_mount(self) -> None:
        # task_id → [(status, subject)] buffered before confirm_task fires
        self._buffered_updates: dict[str, list[tuple[str, str]]] = {}
        self.query_one("#tp-tasks-hdr").display    = False
        self.query_one("#tp-tasks-rule").display   = False
        self.query_one("#tp-monitors-hdr").display = False
        self.query_one("#tp-monitors-rule").display = False

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "tp-close":
            self.app.show_task_panel = False  # triggers watch_show_task_panel in app

    # ── public API ────────────────────────────────────────────────────────────

    def add_task(self, tool_use_id: str, subject: str) -> None:
        """Pre-create a pending row keyed by tool_use_id before real task ID arrives."""
        row = TaskRow(tool_use_id, subject)
        self.query_one("#tp-tasks-scroll").mount(row)
        self.query_one("#tp-tasks-hdr").display  = True
        self.query_one("#tp-tasks-rule").display = True

    def confirm_task(self, tool_use_id: str, task_id: str, subject: str) -> None:
        """tool_result for TaskCreate arrived — set real ID and flush buffered updates."""
        if not task_id:
            return
        tid = str(task_id).strip()
        try:
            row = self.query_one(f"#tpr-{tool_use_id}", TaskRow)
            row.set_real_id(tid)
            # Flush any TaskUpdate calls that arrived before this confirm
            for status, subj in self._buffered_updates.pop(tid, []):
                row.set_status(status, subj)
        except Exception:
            pass

    def update_task(self, task_id: str, status: str, subject: str = "") -> None:
        """Update a task row by real task_id.

        If the row's real ID isn't confirmed yet (TaskCreate tool_result pending),
        buffer the update — it will be flushed in confirm_task.
        """
        tid = str(task_id).strip()
        for row in self.query(TaskRow):
            if str(row._task_id).strip() == tid:
                row.set_status(status, subject)
                return
        # Row not confirmed yet — buffer for when confirm_task fires
        self._buffered_updates.setdefault(tid, []).append((status, subject))

    def add_monitor(self, task_id: str, description: str) -> None:
        """ClaudeSubagentStartEvent with task_type='local_bash'."""
        row = MonitorRow(task_id, description)
        self.query_one("#tp-monitors-scroll").mount(row)
        self.query_one("#tp-monitors-hdr").display  = True
        self.query_one("#tp-monitors-rule").display = True

    def update_monitor(self, task_id: str, description: str, last_tool: str, tokens: int) -> None:
        try:
            self.query_one(f"#mpr-{task_id}", MonitorRow).update_progress(description, last_tool, tokens)
        except Exception:
            pass

    def done_monitor(self, task_id: str, status: str, summary: str, output_file: str = "") -> None:
        try:
            self.query_one(f"#mpr-{task_id}", MonitorRow).mark_done(status, summary, output_file)
        except Exception:
            pass

    def clear(self) -> None:
        """Called from action_new_session — remove all rows and hide section headers."""
        self._buffered_updates.clear()
        self.query_one("#tp-tasks-scroll").remove_children()
        self.query_one("#tp-monitors-scroll").remove_children()
        self.query_one("#tp-tasks-hdr").display    = False
        self.query_one("#tp-tasks-rule").display   = False
        self.query_one("#tp-monitors-hdr").display = False
        self.query_one("#tp-monitors-rule").display = False
