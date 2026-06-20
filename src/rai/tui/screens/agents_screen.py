"""AgentsScreen — live subagent activity viewer with full rendering.

Two-pane layout:
  Left:  list of subagents (name, status, elapsed)
  Right: full activity rendering (thinking, messages, tool calls) for selected subagent
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from rai.tui.widgets.messages import AssistantMsg, ThinkingMsg, ToolCallMsg, _escape


# ---------------------------------------------------------------------------
# Per-subagent row for left pane
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "running": "[yellow]●[/yellow]",
    "interrupted": "[bold $warning]⚠[/bold $warning]",
    "completed": "[green]✓[/green]",
    "failed": "[red]✗[/red]",
    "cancelled": "[dim]○[/dim]",
    "timeout": "[red]⏱[/red]",
}


class AgentsScreen(ModalScreen[None]):
    """Full-screen live subagent activity viewer."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "dismiss_screen", "Close", show=False),
        Binding("r", "refresh_list", "Refresh", show=False),
        Binding("up", "select_prev", "Up", show=False, priority=True),
        Binding("down", "select_next", "Down", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    AgentsScreen {
        background: $background 60%;
        align: center middle;
    }

    #sa-shell {
        width: 94%;
        height: 90%;
        background: $surface;
        border: round $accent 50%;
        padding: 0;
        layout: vertical;
    }

    #sa-header {
        height: 3;
        background: $panel;
        border-bottom: solid $accent 20%;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
    }

    #sa-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
        content-align: left middle;
        height: 3;
    }

    #sa-close {
        width: 5;
        text-align: right;
        color: $error;
        text-style: bold;
        content-align: right middle;
        height: 3;
    }

    #sa-body {
        height: 1fr;
        layout: horizontal;
    }

    /* Left pane — subagent list */
    #sa-list-pane {
        width: 30;
        min-width: 26;
        border-right: solid $accent 20%;
        layout: vertical;
    }

    #sa-list-header {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $panel 50%;
    }

    #sa-list-scroll {
        height: 1fr;
        padding: 0;
    }

    .sa-row {
        height: 2;
        padding: 0 1;
    }

    .sa-row:hover {
        background: $accent 10%;
    }

    .sa-row.-selected {
        background: $accent 20%;
        border-left: tall $accent;
    }

    /* Right pane — activity rendering */
    #sa-detail-pane {
        width: 1fr;
        layout: vertical;
    }

    #sa-detail-header {
        height: 2;
        padding: 0 2;
        background: $panel 50%;
        content-align: left middle;
    }

    #sa-detail-chat {
        height: 1fr;
        padding: 0 1;
    }

    #sa-detail-messages {
        height: auto;
    }

    #sa-detail-empty {
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    #sa-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        border-top: solid $accent 15%;
    }
    """

    # ------------------------------------------------------------------
    # Messages for live event forwarding from app.py
    # ------------------------------------------------------------------

    class LiveEvent(Message):
        def __init__(self, task_id: str, event: dict) -> None:
            super().__init__()
            self.task_id = task_id
            self.event = event

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, client: Any, agent: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._agent = agent
        self._tasks: dict[str, dict] = {}
        self._selected_task_id: str = ""
        self._tool_widgets: dict[str, dict[int, ToolCallMsg]] = {}
        self._last_assistant: dict[str, AssistantMsg | None] = {}
        self._current_thinking: dict[str, ThinkingMsg | None] = {}
        self._tool_idx: dict[str, int] = {}
        self._rebuilding: bool = False

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Container(id="sa-shell"):
            with Horizontal(id="sa-header"):
                yield Static("Subagents — Live Activity", id="sa-title")
                yield Static("[bold red]\\[x][/bold red]", id="sa-close")
            with Horizontal(id="sa-body"):
                with Vertical(id="sa-list-pane"):
                    yield Static("[dim]Subagents[/dim]", id="sa-list-header")
                    yield VerticalScroll(id="sa-list-scroll")
                with Vertical(id="sa-detail-pane"):
                    yield Static("", id="sa-detail-header")
                    yield VerticalScroll(id="sa-detail-chat")
                    yield Static(
                        "[dim]Select a subagent to view its activity[/dim]",
                        id="sa-detail-empty",
                    )
            yield Static(
                "[dim]q[/dim] close  [dim]·[/dim]  "
                "[dim]↑↓[/dim] select  [dim]·[/dim]  "
                "[dim]r[/dim] refresh",
                id="sa-footer",
            )

    def on_mount(self) -> None:
        self._load_initial()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    @work
    async def _load_initial(self) -> None:
        try:
            tasks = await self._client.subagents.list(limit=50)
        except Exception:
            tasks = []

        for t in tasks:
            task_id = t.get("task_id", "")
            if task_id:
                self._tasks[task_id] = t
        await self._rebuild_list()

    def action_refresh_list(self) -> None:
        self._load_initial()

    # ------------------------------------------------------------------
    # Left pane rendering
    # ------------------------------------------------------------------

    async def _rebuild_list(self) -> None:
        if self._rebuilding:
            return
        self._rebuilding = True
        try:
            scroll = self.query_one("#sa-list-scroll", VerticalScroll)
            await scroll.remove_children()

            for task_id, t in self._tasks.items():
                agent_name = t.get("agent_name", "?")
                status = t.get("status", "?")
                icon = _STATUS_ICONS.get(status, "[dim]?[/dim]")
                selected = "-selected" if task_id == self._selected_task_id else ""
                row = Static(
                    f"{icon} [bold]{_escape(agent_name)}[/bold]\n"
                    f"  [dim]{_escape(task_id[:10])}  {_escape(status)}[/dim]",
                    classes=f"sa-row {selected}".strip(),
                    id=f"sa-row-{task_id[:12]}",
                )
                await scroll.mount(row)
        except Exception:
            pass
        finally:
            self._rebuilding = False

    async def _select_task(self, task_id: str) -> None:
        if task_id == self._selected_task_id:
            return
        self._selected_task_id = task_id
        await self._rebuild_list()
        self._render_detail()

    # ------------------------------------------------------------------
    # Right pane — full activity rendering
    # ------------------------------------------------------------------

    def _render_detail(self) -> None:
        task_id = self._selected_task_id
        t = self._tasks.get(task_id)
        if not t:
            return

        agent_name = t.get("agent_name", "?")
        status = t.get("status", "?")
        icon = _STATUS_ICONS.get(status, "")
        model = t.get("model", "") or ""

        try:
            self.query_one("#sa-detail-header", Static).update(
                f"  {icon} [bold]{_escape(agent_name)}[/bold]"
                f"  [dim]{_escape(status)}[/dim]"
                f"  [dim]{_escape(model[:30])}[/dim]"
            )
            self.query_one("#sa-detail-empty", Static).styles.display = "none"
            self.query_one("#sa-detail-chat", VerticalScroll).styles.display = "block"
        except Exception:
            pass

        if task_id not in self._tool_widgets:
            self._tool_widgets[task_id] = {}
            self._last_assistant[task_id] = None
            self._current_thinking[task_id] = None
            self._tool_idx[task_id] = 0
            self._stream_subagent_events(task_id)

    @work
    async def _stream_subagent_events(self, task_id: str) -> None:
        """Stream past + live events from the subagent's SSE bus."""
        try:
            chat_scroll = self.query_one("#sa-detail-chat", VerticalScroll)
            chat_scroll.remove_children()
        except Exception:
            return

        try:
            async for ev in self._client.subagents.stream(task_id, typed=True):
                if self._selected_task_id != task_id:
                    break
                await self._apply_subagent_event(task_id, ev)
        except Exception:
            pass

    async def _apply_subagent_event(self, task_id: str, ev: Any) -> None:
        """Render a typed SSE event into the detail pane."""
        from rai.client._events import (
            SubagentTokenEvent,
            SubagentThinkingEvent,
            SubagentToolStartEvent,
            SubagentToolEndEvent,
            SubagentCompletedEvent,
            SubagentErrorEvent,
        )

        try:
            chat_scroll = self.query_one("#sa-detail-chat", VerticalScroll)
        except Exception:
            return

        if isinstance(ev, SubagentThinkingEvent):
            if self._current_thinking.get(task_id) is None:
                tm = ThinkingMsg()
                self._current_thinking[task_id] = tm
                await chat_scroll.mount(tm)
            self._current_thinking[task_id].append(ev.content or "")

        elif isinstance(ev, SubagentTokenEvent):
            if self._current_thinking.get(task_id) is not None:
                t = self._current_thinking[task_id]
                if not t._done:
                    t.mark_done()
                self._current_thinking[task_id] = None

            am = self._last_assistant.get(task_id)
            if am is None or am._is_final:
                am = AssistantMsg()
                await chat_scroll.mount(am)
                self._last_assistant[task_id] = am
            am.append_text(ev.content or "")

        elif isinstance(ev, SubagentToolStartEvent):
            if self._current_thinking.get(task_id) is not None:
                t = self._current_thinking[task_id]
                if not t._done:
                    t.mark_done()
                self._current_thinking[task_id] = None
            if self._last_assistant.get(task_id) is not None:
                self._last_assistant[task_id].set_final()
                self._last_assistant[task_id] = None

            idx = self._tool_idx.get(task_id, 0)
            self._tool_idx[task_id] = idx + 1
            w = ToolCallMsg(ev.tool_name or "", ev.tool_input or {})
            await chat_scroll.mount(w)
            self._tool_widgets.setdefault(task_id, {})[idx] = w

        elif isinstance(ev, SubagentToolEndEvent):
            idx = self._tool_idx.get(task_id, 1) - 1
            w = self._tool_widgets.get(task_id, {}).get(idx)
            if w is not None:
                output = ev.tool_output
                if isinstance(output, str) and output.lower().startswith("error"):
                    w.set_error(output)
                else:
                    w.set_success(output)

        elif isinstance(ev, SubagentCompletedEvent):
            if self._last_assistant.get(task_id) is not None:
                self._last_assistant[task_id].set_final()
            if self._current_thinking.get(task_id) is not None:
                self._current_thinking[task_id].mark_done()
            status = getattr(ev, "status", "completed")
            icon = _STATUS_ICONS.get(status, "✓")
            await chat_scroll.mount(
                Static(f"\n{icon} [bold]Completed[/bold] — {_escape(status)}", classes="sa-row")
            )
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = status
                await self._rebuild_list()

        elif isinstance(ev, SubagentErrorEvent):
            msg = getattr(ev, "message", "")
            await chat_scroll.mount(
                Static(f"\n[red]✗ Error:[/red] {_escape(msg[:200])}", classes="sa-row")
            )
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "failed"
                await self._rebuild_list()

        try:
            chat_scroll.scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live event forwarding (called by app.py for real-time updates)
    # ------------------------------------------------------------------

    async def on_agents_screen_live_event(self, msg: "AgentsScreen.LiveEvent") -> None:
        """App forwards subagent events here for real-time rendering."""
        task_id = msg.task_id
        ev = msg.event
        ev_type = ev.get("type", "")

        if task_id not in self._tasks:
            self._tasks[task_id] = {
                "task_id": task_id,
                "agent_name": ev.get("agent_name", "?"),
                "status": "running",
            }
            await self._rebuild_list()
            if not self._selected_task_id:
                await self._select_task(task_id)

        if task_id != self._selected_task_id:
            if ev_type == "done":
                self._tasks[task_id]["status"] = ev.get("status", "completed")
                await self._rebuild_list()
            return

        try:
            chat_scroll = self.query_one("#sa-detail-chat", VerticalScroll)
        except Exception:
            return

        if ev_type == "thinking":
            if self._current_thinking.get(task_id) is None:
                tm = ThinkingMsg()
                self._current_thinking[task_id] = tm
                await chat_scroll.mount(tm)
            self._current_thinking[task_id].append(ev.get("text", ""))

        elif ev_type == "message":
            if self._current_thinking.get(task_id) is not None:
                t = self._current_thinking[task_id]
                if not t._done:
                    t.mark_done()
                self._current_thinking[task_id] = None

            am = self._last_assistant.get(task_id)
            if am is None or am._is_final:
                am = AssistantMsg()
                await chat_scroll.mount(am)
                self._last_assistant[task_id] = am
            am.append_text(ev.get("text", ""))

        elif ev_type == "tool_start":
            if self._current_thinking.get(task_id) is not None:
                t = self._current_thinking[task_id]
                if not t._done:
                    t.mark_done()
                self._current_thinking[task_id] = None
            if self._last_assistant.get(task_id) is not None:
                self._last_assistant[task_id].set_final()
                self._last_assistant[task_id] = None

            idx = ev.get("idx", self._tool_idx.get(task_id, 0))
            self._tool_idx[task_id] = idx + 1
            w = ToolCallMsg(ev.get("name", ""), ev.get("input") or {})
            await chat_scroll.mount(w)
            self._tool_widgets.setdefault(task_id, {})[idx] = w

        elif ev_type == "tool_end":
            idx = ev.get("idx", -1)
            w = self._tool_widgets.get(task_id, {}).get(idx)
            if w is not None:
                output = ev.get("output")
                if ev.get("is_error"):
                    w.set_error(output)
                else:
                    w.set_success(output)

        elif ev_type == "done":
            if self._last_assistant.get(task_id) is not None:
                self._last_assistant[task_id].set_final()
            if self._current_thinking.get(task_id) is not None:
                self._current_thinking[task_id].mark_done()
            status = ev.get("status", "completed")
            icon = _STATUS_ICONS.get(status, "✓")
            await chat_scroll.mount(
                Static(f"\n{icon} [bold]Completed[/bold] — {_escape(status)}")
            )
            self._tasks[task_id]["status"] = status
            await self._rebuild_list()

        try:
            chat_scroll.scroll_end(animate=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def action_select_prev(self) -> None:
        ids = list(self._tasks.keys())
        if not ids:
            return
        if self._selected_task_id in ids:
            idx = ids.index(self._selected_task_id)
            idx = max(0, idx - 1)
        else:
            idx = 0
        await self._select_task(ids[idx])

    async def action_select_next(self) -> None:
        ids = list(self._tasks.keys())
        if not ids:
            return
        if self._selected_task_id in ids:
            idx = ids.index(self._selected_task_id)
            idx = min(len(ids) - 1, idx + 1)
        else:
            idx = 0
        await self._select_task(ids[idx])

    async def on_click(self, event) -> None:
        widget_id = getattr(event.widget, "id", None) or ""
        if widget_id == "sa-close":
            self.dismiss(None)
            return
        if widget_id.startswith("sa-row-"):
            task_prefix = widget_id[7:]
            for tid in self._tasks:
                if tid[:12] == task_prefix:
                    await self._select_task(tid)
                    return

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
