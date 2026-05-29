"""SubagentTreePanel — live tree with bloom animation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_BLOOM_DURATION = 1.5
_HIDE_DELAY = 3.0


@dataclass
class _TaskRow:
    task_id: str
    agent_name: str
    start_time: float = field(default_factory=time.monotonic)
    done_time: float | None = None
    status: str = "running"
    token_preview: str = ""


class SubagentTreePanel(Widget):
    DEFAULT_CSS = """
    SubagentTreePanel {
        display: none;
        height: auto;
        max-height: 8;
        background: $surface 25%;
        border-left: thick $success 40%;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tasks: dict[str, _TaskRow] = {}
        self._collapsed = False

    def compose(self) -> ComposeResult:
        yield Static("", id="tree-content")

    def _update_tree(self) -> None:
        if not self._tasks:
            return
        running = sum(1 for t in self._tasks.values() if t.status == "running")
        header = f"[bold]◆ Subagents ({running} running)[/bold]" if running else "[bold]◆ Subagents[/bold]"
        if self._collapsed:
            try:
                self.query_one("#tree-content", Static).update(header)
            except Exception:
                pass
            return

        lines = [header]
        now = time.monotonic()
        for task in self._tasks.values():
            elapsed = int((task.done_time or now) - task.start_time)
            if task.status == "running":
                frame = _SPINNER[int(now * 8) % len(_SPINNER)]
                glyph = f"[yellow]{frame}[/yellow]"
                age = f"[dim][{elapsed}s][/dim]"
            elif task.status in ("completed", "done"):
                # bloom or settled
                if task.done_time and (now - task.done_time) < _BLOOM_DURATION:
                    glyph = "[bold green]✔[/bold green]"
                else:
                    glyph = "[green]✔[/green]"
                age = ""
            else:
                glyph = "[red]✗[/red]"
                age = ""

            preview = task.token_preview[-50:] if task.token_preview else ""
            lines.append(f"  {glyph} [cyan]{task.agent_name}[/cyan]  {age}  [dim]{preview}[/dim]")

        try:
            self.query_one("#tree-content", Static).update("\n".join(lines))
        except Exception:
            pass

    def add_task(self, task_id: str, agent_name: str) -> None:
        self._tasks[task_id] = _TaskRow(task_id=task_id, agent_name=agent_name)
        self.styles.display = "block"
        self._update_tree()

    def append_token(self, task_id: str, content: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].token_preview += content
            self._update_tree()

    def mark_done(self, task_id: str, status: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = status
            self._tasks[task_id].done_time = time.monotonic()
            self._update_tree()

    def tick(self) -> None:
        """Called by app timer every 0.5s to animate spinners + auto-hide."""
        if not self._tasks:
            return
        self._update_tree()
        # auto-hide if all tasks done and hide delay passed
        all_done = all(t.status != "running" for t in self._tasks.values())
        if all_done:
            latest_done = max(
                (t.done_time or 0) for t in self._tasks.values()
            )
            if time.monotonic() - latest_done > _HIDE_DELAY:
                self.styles.display = "none"

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._update_tree()

    def reset(self) -> None:
        self._tasks.clear()
        self.styles.display = "none"
