"""BackgroundRunsPanel — Claude Code-style background runs overlay (Ctrl+B)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static





def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")

@dataclass
class _RunEntry:
    run_id:      str
    agent:       str
    status:      str        # "running" | "done" | "error" | "hitl"
    start_time:  float = field(default_factory=time.monotonic)
    tokens_in:   int   = 0
    tokens_out:  int   = 0
    prompt:      str   = ""
    thread_id:   str   = ""
    tools:       list  = field(default_factory=list)   # [(name, args_inline, status)]
    chat_events: list  = field(default_factory=list)
    # chat_events holds dicts:
    #   {"type": "thinking",  "text": str}
    #   {"type": "message",   "text": str}
    #   {"type": "tool_start","name": str, "input": dict,
    #    "idx": int, "output": Any, "is_error": bool, "done": bool}


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


class BackgroundRunsPanel(Widget):
    """Animated panel that slides up above the input bar showing all background runs.

    Activated by Ctrl+B.  Layout mirrors Claude Code's agent list:
        ● run-name   description…   elapsed   ↓ tokens
    """

    can_focus = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up",     "cursor_up",    show=False, priority=True),
        Binding("down",   "cursor_down",  show=False, priority=True),
        Binding("enter",  "view_run",     "View",     show=True),
        Binding("x",      "stop_run",     "Stop",     show=True),
        Binding("ctrl+x", "stop_all",     "Stop All", show=True),
        Binding("left",   "close_panel",  "Close",    show=True),
    ]

    DEFAULT_CSS = """
    BackgroundRunsPanel {
        height: 0;
        max-height: 14;
        overflow: hidden hidden;
        background: $surface 92%;
        border: round $accent 40%;
        padding: 0 1;
    }
    BackgroundRunsPanel.panel-open {
        height: auto;
    }
    BackgroundRunsPanel #bg-panel-header {
        color: $text-muted;
        margin-bottom: 0;
    }
    BackgroundRunsPanel #bg-panel-content {
        height: auto;
    }
    BackgroundRunsPanel #bg-panel-footer {
        color: $text-muted;
        margin-top: 0;
    }
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class ViewRun(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class StopRun(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class StopAll(Message):
        pass

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._runs:    dict[str, _RunEntry] = {}
        self._order:   list[str]            = []   # insertion order
        self._cursor:  int                  = 0
        self._visible: bool                 = False

    def compose(self) -> ComposeResult:
        yield Static("", id="bg-panel-header")
        yield Static("", id="bg-panel-content")
        yield Static(
            "[dim] enter[/dim] view  [dim]·[/dim]  [dim]x[/dim] stop  "
            "[dim]·[/dim]  [dim]ctrl+x[/dim] stop all  [dim]·[/dim]  [dim]←[/dim] close",
            id="bg-panel-footer",
        )

    # ------------------------------------------------------------------
    # Public API (called from app.py)
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        if self._visible:
            self._close()
        else:
            self._open()

    def show_panel(self) -> None:
        if not self._visible:
            self._open()

    def hide_panel(self) -> None:
        if self._visible:
            self._close()

    def add(self, run_id: str, agent: str, thread_id: str = "") -> None:
        if run_id not in self._runs:
            self._runs[run_id]  = _RunEntry(run_id=run_id, agent=agent, status="running", thread_id=thread_id)
            self._order.append(run_id)
        self._clamp_cursor()
        self._refresh()

    def remove(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        if run_id in self._order:
            self._order.remove(run_id)
        self._clamp_cursor()
        self._refresh()
        if not self._runs and self._visible:
            self._close()

    def update_tokens(self, run_id: str, tokens_in: int, tokens_out: int) -> None:
        if run_id in self._runs:
            self._runs[run_id].tokens_in  = tokens_in
            self._runs[run_id].tokens_out = tokens_out

    def set_prompt(self, run_id: str, prompt: str) -> None:
        if run_id in self._runs:
            self._runs[run_id].prompt = prompt

    def track_tool_start(self, run_id: str, tool_name: str, args_inline: str) -> None:
        if run_id in self._runs:
            self._runs[run_id].tools.append((tool_name, args_inline, "running"))

    def track_tool_finish(self, run_id: str, tool_name: str, error: bool = False) -> None:
        if run_id not in self._runs:
            return
        tools = self._runs[run_id].tools
        for i in range(len(tools) - 1, -1, -1):
            if tools[i][0] == tool_name and tools[i][2] == "running":
                tools[i] = (tool_name, tools[i][1], "error" if error else "done")
                break

    def get_run(self, run_id: str) -> _RunEntry | None:
        return self._runs.get(run_id)

    def set_status(self, run_id: str, status: str) -> None:
        if run_id in self._runs:
            self._runs[run_id].status = status
        self._refresh()

    def tick(self) -> None:
        """Called every 0.1s by app timer. Only re-renders if panel is open."""
        if self._visible and self._runs:
            self._refresh()

    # ------------------------------------------------------------------
    # Chat-event buffering (for RunDetailScreen replay)
    # ------------------------------------------------------------------

    def buffer_thinking(self, run_id: str, content: str) -> None:
        if run_id not in self._runs:
            return
        events = self._runs[run_id].chat_events
        if events and events[-1]["type"] == "thinking":
            events[-1]["text"] += content
        else:
            events.append({"type": "thinking", "text": content})

    def buffer_token(self, run_id: str, content: str) -> None:
        if run_id not in self._runs:
            return
        events = self._runs[run_id].chat_events
        if events and events[-1]["type"] == "message":
            events[-1]["text"] += content
        else:
            events.append({"type": "message", "text": content})

    def buffer_tool_start(self, run_id: str, name: str, inp: dict) -> int:
        """Appends a tool_start event and returns its index in chat_events."""
        if run_id not in self._runs:
            return -1
        events = self._runs[run_id].chat_events
        idx = len(events)
        events.append({
            "type": "tool_start", "name": name, "input": inp,
            "idx": idx, "output": None, "is_error": False, "done": False,
        })
        return idx

    def buffer_tool_end(self, run_id: str, tool_name: str, output: Any, is_error: bool) -> int:
        """Updates the last unfinished tool_start matching name. Returns its idx."""
        if run_id not in self._runs:
            return -1
        for ev in reversed(self._runs[run_id].chat_events):
            if ev["type"] == "tool_start" and ev["name"] == tool_name and not ev["done"]:
                ev["done"]     = True
                ev["output"]   = output
                ev["is_error"] = is_error
                return ev["idx"]
        return -1

    @property
    def count(self) -> int:
        return len(self._runs)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open(self) -> None:
        self._visible = True
        self.add_class("panel-open")
        self._refresh()
        self.focus()

    def _close(self) -> None:
        self._visible = False
        self.remove_class("panel-open")

    def _clamp_cursor(self) -> None:
        if not self._order:
            self._cursor = 0
        else:
            self._cursor = max(0, min(self._cursor, len(self._order) - 1))

    def _refresh(self) -> None:
        active  = sum(1 for r in self._runs.values() if r.status == "running")
        hitl    = sum(1 for r in self._runs.values() if r.status == "hitl")
        total   = len(self._runs)

        # Header
        parts = [f"[bold]bg runs[/bold]  [dim]·[/dim]  {total} run{'s' if total != 1 else ''}"]
        if active:
            parts.append(f"[yellow]{active} active[/yellow]")
        if hitl:
            parts.append(f"[bold $warning]⚠ {hitl} approval[/bold $warning]")
        try:
            self.query_one("#bg-panel-header", Static).update(
                "  ".join(parts)
            )
        except Exception:
            pass

        # Rows
        rows = []
        for i, run_id in enumerate(self._order):
            entry   = self._runs[run_id]
            elapsed = _fmt_elapsed(time.monotonic() - entry.start_time)
            tokens  = _fmt_tokens(entry.tokens_out) if entry.tokens_out else ""

            # Status glyph
            if entry.status == "hitl":
                glyph = "[bold $warning]⚠[/bold $warning]"
            elif entry.status == "error":
                glyph = "[red]✗[/red]"
            elif entry.status == "done":
                glyph = "[dim]○[/dim]"
            else:
                glyph = "[yellow]○[/yellow]"

            # Cursor highlight
            if i == self._cursor:
                glyph    = "[bold $accent]●[/bold $accent]"
                name_fmt = f"[bold]{_escape(entry.agent)}[/bold]"
            else:
                name_fmt = f"[dim]{_escape(entry.agent)}[/dim]"

            short_id = run_id[:8]
            tok_part = f"  [dim]↓ {tokens}[/dim]" if tokens else ""
            row = (
                f" {glyph}  {name_fmt}  "
                f"[dim]{short_id}[/dim]"
                f"  [dim]{elapsed}[/dim]{tok_part}"
            )
            rows.append(row)

        try:
            self.query_one("#bg-panel-content", Static).update("\n".join(rows))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cursor_up(self) -> None:
        if self._order:
            self._cursor = max(0, self._cursor - 1)
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._order:
            self._cursor = min(len(self._order) - 1, self._cursor + 1)
            self._refresh()

    def action_view_run(self) -> None:
        if self._order:
            run_id = self._order[self._cursor]
            self.post_message(self.ViewRun(run_id))

    def action_stop_run(self) -> None:
        if self._order:
            run_id = self._order[self._cursor]
            self.post_message(self.StopRun(run_id))

    def action_stop_all(self) -> None:
        self.post_message(self.StopAll())

    def action_close_panel(self) -> None:
        self._close()
