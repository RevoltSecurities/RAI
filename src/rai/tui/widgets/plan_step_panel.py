"""PlanStepPanel — right-docked panel tracking plan step execution progress."""

from __future__ import annotations

import re

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.markup import escape
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

_STEP_RE = re.compile(r"\*\*(\d+)\.\s+(.+?)\*\*")


_STATUS_ICON = {
    "pending": "[dim]○[/dim]",
    "in_progress": "[yellow]▶[/yellow]",
    "done": "[green]✓[/green]",
    "blocked": "[red]✗[/red]",
}


class StepRow(Widget, can_focus=False):
    """Single plan step row with status icon, number, and title."""

    DEFAULT_CSS = """
    StepRow {
        height: 2;
        padding: 0 1;
    }

    StepRow.-active {
        background: $accent 8%;
    }
    """

    def __init__(self, step_number: int, description: str, status: str = "pending") -> None:
        super().__init__(id=f"step-row-{step_number}")
        self._step_number = step_number
        self._description = description
        self._status = status
        self._notes = ""
        self._reason = ""

    def compose(self) -> ComposeResult:
        yield Static(self._markup(), id=f"step-label-{self._step_number}")

    def set_status(self, status: str, notes: str = "", reason: str = "") -> None:
        self._status = status
        if notes:
            self._notes = notes
        if reason:
            self._reason = reason
        self.set_class(status == "in_progress", "-active")
        try:
            self.query_one(f"#step-label-{self._step_number}", Static).update(self._markup())
        except NoMatches:
            pass

    def _markup(self) -> str:
        icon = _STATUS_ICON.get(self._status, _STATUS_ICON["pending"])
        title = escape(self._description[:32]) if self._description else "—"
        line1 = f" {icon} [bold]Step {self._step_number}[/bold]  {title}"
        if self._status == "done" and self._notes:
            line1 += f"\n     [dim green]{escape(self._notes[:36])}[/dim green]"
        elif self._status == "blocked" and self._reason:
            line1 += f"\n     [dim red]{escape(self._reason[:36])}[/dim red]"
        else:
            line1 += "\n"
        return line1


class PlanStepPanel(Widget):
    """Right-docked panel showing live plan step execution progress."""

    DEFAULT_CSS = """
    PlanStepPanel {
        width: 42;
        layer: plansteps;
        dock: right;
        offset-x: 100%;
        background: $surface;
        border-left: solid $primary 25%;
        transition: offset 200ms in_out_cubic;
        layout: vertical;
    }

    PlanStepPanel.-visible {
        offset-x: 0;
    }

    #psp-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 15%;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
    }

    #psp-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
        content-align: left middle;
        height: 3;
    }

    #psp-progress {
        width: auto;
        min-width: 8;
        color: $text-muted;
        content-align: right middle;
        height: 3;
        margin-right: 1;
    }

    #psp-close {
        width: 5;
        text-align: right;
        color: $error;
        text-style: bold;
        content-align: right middle;
        height: 3;
    }

    #psp-steps {
        height: 1fr;
        padding: 0;
        scrollbar-size: 1 1;
        scrollbar-color: $accent 25%;
    }
    """

    class Closed(Message):
        """Posted when user clicks close button."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: dict[int, StepRow] = {}
        self._total: int = 0
        self._done: int = 0
        self._blocked: int = 0

    def compose(self) -> ComposeResult:
        with Static(id="psp-header"):
            pass
        yield Static("[bold]◈ Plan Steps[/bold]", id="psp-title")
        yield Static("", id="psp-progress")
        yield Static("[bold red]\\[x][/bold red]", id="psp-close")
        yield VerticalScroll(id="psp-steps")

    def on_mount(self) -> None:
        pass

    def on_click(self, event: events.Click) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "psp-close":
            self.remove_class("-visible")
            self.post_message(self.Closed())

    def load_steps(self, plan_markdown: str) -> None:
        """Parse steps from canonical plan markdown and pre-populate as pending."""
        self.clear()
        for m in _STEP_RE.finditer(plan_markdown):
            num = int(m.group(1))
            title = m.group(2).strip().rstrip("⬜🔄✅🚫").strip()
            if num not in self._rows:
                row = StepRow(num, title, "pending")
                self._rows[num] = row
                self._total += 1
                try:
                    self.query_one("#psp-steps", VerticalScroll).mount(row)
                except NoMatches:
                    pass
        self._update_progress()

    def step_started(self, step_number: int, description: str) -> None:
        row = self._rows.get(step_number)
        if row is None:
            row = StepRow(step_number, description, "in_progress")
            self._rows[step_number] = row
            self._total += 1
            try:
                self.query_one("#psp-steps", VerticalScroll).mount(row)
            except NoMatches:
                pass
        else:
            row.set_status("in_progress")
        self._update_progress()

    def step_done(self, step_number: int, description: str, notes: str = "") -> None:
        row = self._rows.get(step_number)
        if row is None:
            row = StepRow(step_number, description, "done")
            row._notes = notes
            self._rows[step_number] = row
            self._total += 1
            try:
                self.query_one("#psp-steps", VerticalScroll).mount(row)
            except NoMatches:
                pass
        else:
            row.set_status("done", notes=notes)
        self._done += 1
        self._update_progress()

    def step_blocked(self, step_number: int, description: str, reason: str = "") -> None:
        row = self._rows.get(step_number)
        if row is None:
            row = StepRow(step_number, description, "blocked")
            row._reason = reason
            self._rows[step_number] = row
            self._total += 1
            try:
                self.query_one("#psp-steps", VerticalScroll).mount(row)
            except NoMatches:
                pass
        else:
            row.set_status("blocked", reason=reason)
        self._blocked += 1
        self._update_progress()

    def step_escalated(self, step_number: int, priority: str) -> None:
        row = self._rows.get(step_number)
        if row:
            # Strip any existing priority prefix before adding new one
            desc = row._description
            for p in ("CRITICAL: ", "HIGH: ", "NORMAL: ", "LOW: "):
                if desc.startswith(p):
                    desc = desc[len(p):]
                    break
            tag = priority.upper()
            row._description = f"{tag}: {desc}" if priority != "normal" else desc
            row.set_status(row._status)

    def step_added(self, step_number: int, description: str, priority: str = "normal") -> None:
        if step_number in self._rows:
            return
        label = description
        if priority in ("critical", "high"):
            label = f"{priority.upper()}: {description}"
        row = StepRow(step_number, label, "pending")
        self._rows[step_number] = row
        self._total += 1
        try:
            self.query_one("#psp-steps", VerticalScroll).mount(row)
        except NoMatches:
            pass
        self._update_progress()

    def clear(self) -> None:
        self._rows.clear()
        self._total = 0
        self._done = 0
        self._blocked = 0
        try:
            self.query_one("#psp-steps", VerticalScroll).remove_children()
        except NoMatches:
            pass
        self._update_progress()

    def _update_progress(self) -> None:
        if self._total == 0:
            text = ""
        else:
            text = f"[dim]{self._done}/{self._total}[/dim]"
        try:
            self.query_one("#psp-progress", Static).update(text)
        except NoMatches:
            pass
