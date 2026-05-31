"""RunDetailScreen — live detail overlay for a background run (Enter from bg panel)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from rai.tui.widgets.messages import AssistantMsg, ThinkingMsg, ToolCallMsg, _escape

if TYPE_CHECKING:
    from rai.tui.widgets.bg_panel import BackgroundRunsPanel


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


class RunDetailScreen(ModalScreen[None]):
    """Full-screen live detail view of a background run."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss",  show=False),
        Binding("left",   "dismiss",  show=False),
        Binding("x",      "stop_run", "Stop", show=True),
    ]

    DEFAULT_CSS = """
    RunDetailScreen {
        background: $background 50%;
        align: center middle;
    }
    RunDetailScreen #detail-shell {
        width: 92%;
        height: 92%;
        background: $surface 96%;
        border: round $accent 50%;
        padding: 0;
    }
    RunDetailScreen #detail-header {
        height: 1;
        padding: 0 2;
        background: $surface 80%;
    }
    RunDetailScreen #detail-chat {
        height: 1fr;
        padding: 0 1;
    }
    RunDetailScreen #detail-messages {
        height: auto;
    }
    RunDetailScreen #detail-input-area {
        height: auto;
        padding: 0 1 1 1;
        border-top: solid $accent 20%;
    }
    RunDetailScreen #detail-input {
        border: tall $accent 40%;
    }
    RunDetailScreen #detail-input:focus {
        border: tall $accent;
    }
    RunDetailScreen #detail-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    RunDetailScreen .detail-divider {
        height: 1;
        color: $text-muted 50%;
        content-align: center middle;
        margin: 1 0;
    }
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class Stop(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class LiveEvent(Message):
        """Posted by app.py to push a real-time chat event into the detail screen."""
        def __init__(self, event: dict) -> None:
            super().__init__()
            self.event = event

    class NewPrompt(Message):
        """Posted when user submits a new prompt from within the detail screen."""
        def __init__(self, run_id: str, text: str) -> None:
            super().__init__()
            self.run_id = run_id
            self.text   = text

    class SwitchRun(Message):
        """Posted by app.py when a new bg run starts on the same thread."""
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, run_id: str, panel: "BackgroundRunsPanel", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._run_id = run_id
        self._panel  = panel
        self._tool_widgets: dict[int, ToolCallMsg] = {}  # event idx → widget
        self._last_assistant: AssistantMsg | None = None
        self._current_thinking: ThinkingMsg | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="detail-shell"):
            yield Static("", id="detail-header")
            with VerticalScroll(id="detail-chat"):
                yield Container(id="detail-messages")
            with Container(id="detail-input-area"):
                yield Input(
                    placeholder="New prompt — press Enter to run…",
                    id="detail-input",
                )
            yield Static(
                "[dim]esc / ← close[/dim]  [dim]·[/dim]  [dim]x stop[/dim]  "
                "[dim]·[/dim]  [dim]enter send  (stays in bg thread)[/dim]",
                id="detail-footer",
            )

    async def on_mount(self) -> None:
        self._update_header()
        await self._replay()
        self.set_interval(0.1, self._tick)

    # ------------------------------------------------------------------
    # Header + tick
    # ------------------------------------------------------------------

    def _update_header(self) -> None:
        entry = self._panel.get_run(self._run_id)
        if entry is None:
            return
        elapsed    = int(time.monotonic() - entry.start_time)
        tokens_str = _fmt_tokens(entry.tokens_out)
        status_glyph = {
            "running": "[yellow]●[/yellow]",
            "hitl":    "[bold $warning]⚠[/bold $warning]",
            "done":    "[green]●[/green]",
            "error":   "[red]●[/red]",
        }.get(entry.status, "[dim]●[/dim]")
        short = (entry.prompt[:55] + "…") if len(entry.prompt) > 55 else entry.prompt
        try:
            self.query_one("#detail-header", Static).update(
                f"  {status_glyph} [bold $accent]{_escape(entry.agent)}[/bold $accent]"
                f"  [dim]{_escape(short)}[/dim]"
                f"  [dim]{elapsed}s · {tokens_str} tok[/dim]"
            )
        except Exception:
            pass

    def _tick(self) -> None:
        if self._current_thinking is not None:
            self._current_thinking.flush()
        self._update_header()

    # ------------------------------------------------------------------
    # Replay past events (called once on mount)
    # ------------------------------------------------------------------

    async def _replay(self) -> None:
        entry = self._panel.get_run(self._run_id)
        if entry is None:
            return
        for ev in entry.chat_events:
            await self._apply_event(ev, replay=True)
        try:
            self.query_one("#detail-chat", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event application (shared by replay and live)
    # ------------------------------------------------------------------

    async def _apply_event(self, ev: dict, replay: bool = False) -> None:
        ev_type   = ev.get("type", "")
        container = self.query_one("#detail-messages", Container)

        if ev_type == "thinking":
            if self._current_thinking is None:
                tm = ThinkingMsg()
                self._current_thinking = tm
                await container.mount(tm)
            self._current_thinking.append(ev.get("text", ""))
            if replay:
                self._current_thinking.mark_done()
                self._current_thinking.flush()

        elif ev_type == "message":
            if self._current_thinking is not None and not self._current_thinking._done:
                self._current_thinking.mark_done()
            text = ev.get("text", "")
            if self._last_assistant is None or self._last_assistant._is_final:
                am = AssistantMsg()
                await container.mount(am)
                self._last_assistant = am
            self._last_assistant.append_text(text)
            if replay:
                self._last_assistant.set_final()

        elif ev_type == "tool_start":
            if self._current_thinking is not None and not self._current_thinking._done:
                self._current_thinking.mark_done()
            if self._last_assistant is not None:
                self._last_assistant.set_final()
                self._last_assistant = None
            name = ev.get("name", "")
            inp  = ev.get("input") or {}
            idx  = ev.get("idx", -1)
            w    = ToolCallMsg(name, inp)
            await container.mount(w)
            if idx >= 0:
                self._tool_widgets[idx] = w
            if replay and ev.get("done"):
                output = ev.get("output")
                if ev.get("is_error"):
                    w.set_error(output)
                else:
                    w.set_success(output)

    async def _apply_tool_end(self, ev: dict) -> None:
        idx = ev.get("idx", -1)
        w   = self._tool_widgets.get(idx)
        if w is None:
            return
        output = ev.get("output")
        if ev.get("is_error"):
            w.set_error(output)
        else:
            w.set_success(output)

    # ------------------------------------------------------------------
    # Live event handler (posted by app.py)
    # ------------------------------------------------------------------

    async def on_run_detail_screen_live_event(self, msg: "RunDetailScreen.LiveEvent") -> None:
        ev      = msg.event
        ev_type = ev.get("type", "")

        if ev_type == "tool_end":
            await self._apply_tool_end(ev)
        elif ev_type == "done":
            if self._last_assistant is not None:
                self._last_assistant.set_final()
            if self._current_thinking is not None:
                self._current_thinking.mark_done()
        else:
            await self._apply_event(ev, replay=False)

        try:
            self.query_one("#detail-chat", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SwitchRun — app started a new run on this bg thread
    # ------------------------------------------------------------------

    async def on_run_detail_screen_switch_run(self, msg: "RunDetailScreen.SwitchRun") -> None:
        """App created a new run on the same thread — switch tracking to it."""
        if self._current_thinking is not None and not self._current_thinking._done:
            self._current_thinking.mark_done()
            self._current_thinking.flush()
        self._current_thinking = None
        self._last_assistant   = None
        self._tool_widgets.clear()
        self._run_id = msg.run_id

        container = self.query_one("#detail-messages", Container)
        await container.mount(
            Static("[dim]─── new message ───[/dim]", classes="detail-divider")
        )
        self._update_header()
        try:
            self.query_one("#detail-chat", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Input handler — user submits a new prompt
    # ------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""   # clear immediately; stay open in bg thread
        self.post_message(self.NewPrompt(self._run_id, text))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_stop_run(self) -> None:
        self.post_message(self.Stop(self._run_id))
        self.dismiss()
