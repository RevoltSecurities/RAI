"""AssistantMessage — streaming assistant response: spinner then incremental Markdown."""

from __future__ import annotations

import time
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Markdown, Static


_BLOOM = (
    ("[dim]·[/dim]",          "dim"),
    ("[dim].[/dim]",          "dim"),
    ("[yellow]+[/yellow]",    "mid"),
    ("[yellow]✶[/yellow]",    "bright"),
    ("[bold yellow]✻[/bold yellow]", "peak"),
    ("[yellow]✽[/yellow]",    "bright"),
    ("[yellow]✶[/yellow]",    "mid"),
    ("[dim]+[/dim]",          "dim"),
    ("[dim]·[/dim]",          "dim"),
    ("[dim].[/dim]",          "dim"),
)
_PHRASES = (
    "Thinking", "Reasoning", "Analyzing", "Working",
    "Processing", "Generating", "Composing", "Planning",
)


class AssistantMessage(Widget):
    """Streaming assistant response.

    While waiting for the first token: animated spinner with elapsed time.
    Once tokens arrive: batched via a periodic timer calling Markdown.append().
    On finalize: Markdown.update(full_text) — guarantees content is visible
    regardless of whether the timer had a chance to fire (fast responses).
    For history restore: set_content() sets the full text in one update() call.
    """

    DEFAULT_CSS = """
    AssistantMessage {
        margin: 1 0;
        padding: 0 0;
        background: transparent;
        height: auto;
    }
    AssistantMessage #asst-rule {
        color: $success 35%;
        height: 1;
        margin-bottom: 0;
    }
    AssistantMessage #asst-spinner {
        height: auto;
        color: $primary;
        padding: 0 0;
    }
    AssistantMessage Markdown {
        margin: 0;
        padding: 0 0;
        background: transparent;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._buf: str = ""
        self._flushed_len: int = 0
        self._is_final: bool = False
        self._spin_pos: int = 0
        self._start_time: float = time.monotonic()
        self._spinner_timer: Any = None
        self._flush_timer: Any = None

    def compose(self) -> ComposeResult:
        yield Static("─" * 120, id="asst-rule")
        yield Static("", id="asst-spinner")
        md = Markdown("", id="assistant-md")
        md.display = False
        yield md

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(0.12, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._buf:
            if self._spinner_timer:
                self._spinner_timer.stop()
                self._spinner_timer = None
            return
        elapsed = int(time.monotonic() - self._start_time)
        bloom_frame, _ = _BLOOM[self._spin_pos % len(_BLOOM)]
        phrase = _PHRASES[(self._spin_pos // len(_BLOOM)) % len(_PHRASES)]
        self._spin_pos += 1
        try:
            self.query_one("#asst-spinner", Static).update(
                f" {bloom_frame} {phrase}…"
            )
        except Exception:
            pass

    def _swap_to_markdown(self) -> None:
        try:
            self.query_one("#asst-spinner", Static).display = False
            self.query_one("#assistant-md", Markdown).display = True
        except Exception:
            pass

    def _flush_markdown(self) -> None:
        """Timer callback — appends new chunks to Markdown widget.

        Markdown.append() returns AwaitComplete; gather() in its __init__
        schedules the coroutine. It runs on the next event-loop iteration
        (which is fine since the timer fires when the loop is idle).
        """
        new_len = len(self._buf)
        if self._flushed_len >= new_len:
            return
        chunk = self._buf[self._flushed_len:]
        self._flushed_len = new_len
        try:
            self.query_one("#assistant-md", Markdown).append(chunk)
        except Exception:
            pass

    def append_token(self, chunk: str) -> None:
        first = not self._buf
        self._buf += chunk
        if first:
            self._swap_to_markdown()
            self._flush_timer = self.set_interval(0.06, self._flush_markdown)

    def finalize(self) -> None:
        """Finalize rendering — uses update(full_text) for guaranteed repaint.

        For fast responses where the entire stream completes before the 60ms
        timer fires, append() never runs. Using update(full_text) — the same
        path as set_content()/history restore — guarantees the content becomes
        visible immediately.
        """
        self._is_final = True
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._flush_timer:
            self._flush_timer.stop()
            self._flush_timer = None
        if not self._buf:
            self._swap_to_markdown()
            return
        self._swap_to_markdown()
        self._flushed_len = len(self._buf)
        try:
            self.query_one("#assistant-md", Markdown).update(self._buf)
        except Exception:
            pass

    def set_content(self, full_text: str) -> None:
        """Instant history restore — one update() call, no streaming."""
        self._buf = full_text
        self._flushed_len = len(full_text)
        self._is_final = True
        self._swap_to_markdown()
        try:
            self.query_one("#assistant-md", Markdown).update(full_text)
        except Exception:
            pass

    @property
    def text(self) -> str:
        return self._buf
