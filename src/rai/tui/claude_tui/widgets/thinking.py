"""ThinkingBlock — collapsible thinking with live streaming and elapsed timer.

Matches the RAI HTTP TUI's ThinkingMsg pattern:
- Collapsible widget always present in the tree (no re-mount/re-render)
- Title updates via periodic tick (elapsed timer)
- Content streams into a Static inside the collapsible — never causes layout thrash
- Click-to-expand shows full thinking content
- Finalize freezes the timer and marks done
"""

from __future__ import annotations

import time
from typing import Any

from textual.app import ComposeResult
from textual.markup import escape
from textual.widget import Widget
from textual.widgets import Collapsible, Static

_MAX_PREVIEW_LINES = 60
_MAX_LINE_LEN = 200


class ThinkingBlock(Widget):
    """Collapsible thinking block with live elapsed timer — no layout thrash."""

    DEFAULT_CSS = """
    ThinkingBlock {
        height: auto;
        margin: 0 0 0 2;
    }
    ThinkingBlock Collapsible {
        background: transparent;
    }
    ThinkingBlock #thinking-content {
        color: $text-muted 60%;
        text-style: italic;
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._buf: str = ""
        self._dirty: bool = True
        self._start_time: float = time.monotonic()
        self._done: bool = False
        self._timer: Any = None

    def compose(self) -> ComposeResult:
        with Collapsible(title="Thinking…", collapsed=True):
            yield Static("", id="thinking-content")

    def on_mount(self) -> None:
        self._timer = self.set_interval(1, self._tick)

    def _tick(self) -> None:
        self.flush()

    def append(self, text: str) -> None:
        self._buf += text
        self._dirty = True

    def finalize(self) -> None:
        self._done = True
        self._dirty = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.flush()

    def flush(self) -> None:
        """Push buffered content + update title. Safe to call from app ticker."""
        elapsed = time.monotonic() - self._start_time
        if not self._done:
            title = f"Thinking… ({elapsed:.0f}s)"
            self._dirty = True
        else:
            title = f"Thought for {elapsed:.0f}s"

        if not self._dirty:
            return
        self._dirty = False

        try:
            self.query_one(Collapsible).title = title
        except Exception:
            pass

        if self._buf:
            lines = self._buf.splitlines()[:_MAX_PREVIEW_LINES]
            parts: list[str] = []
            for ln in lines:
                if len(ln) > _MAX_LINE_LEN:
                    ln = ln[:_MAX_LINE_LEN] + "…"
                parts.append(escape(ln))
            total = len(self._buf.splitlines())
            if total > _MAX_PREVIEW_LINES:
                parts.append(f"… ({total - _MAX_PREVIEW_LINES} more lines)")
            try:
                self.query_one("#thinking-content", Static).update("\n".join(parts))
            except Exception:
                pass
