"""HITLPanel — tool approval panel for agent and subagent interrupts."""

from __future__ import annotations

import difflib
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static, TextArea

from rai.tui.widgets.messages import _render_args_lines



def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")

# ---------------------------------------------------------------------------
# Rendering constants  (mirror deepagents limits)
# ---------------------------------------------------------------------------

_MAX_LINES     = 30    # write_file content lines shown
_MAX_DIFF_LINES = 50   # edit_file diff lines shown
_MAX_LINE_LEN  = 200   # per-line char truncation — avoids layout stalls

# LangChain dark diff palette (same as deepagents LC_GREEN_BG / LC_PINK_BG)
_ADD_FG = "#9ECE6A"
_ADD_BG = "#1C2A38"
_DEL_FG = "#F7768E"
_DEL_BG = "#2A1F32"

_WRITE_TOOLS        = frozenset({"write_file", "write", "create_file"})
_EDIT_TOOLS         = frozenset({"edit_file",  "edit"})
_MEMORY_WRITE_TOOLS = frozenset({"memory_write"})
_MEMORY_EDIT_TOOLS  = frozenset({"memory_update"})


# ---------------------------------------------------------------------------
# Respond modal
# ---------------------------------------------------------------------------


class _RespondModal(ModalScreen[str | None]):
    """Simple modal for sending a text message back to the agent."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "submit", "Send", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    _RespondModal { align: center middle; }
    _RespondModal > Vertical {
        width: 80;
        max-width: 95%;
        height: 14;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    _RespondModal TextArea { height: 1fr; }
    _RespondModal .modal-help { dock: bottom; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Message to agent:")
            yield TextArea("", id="respond-input")
            yield Static("Ctrl+S  send  |  Escape  cancel", classes="modal-help")

    def on_mount(self) -> None:
        self.query_one("#respond-input", TextArea).focus()

    def action_submit(self) -> None:
        self.dismiss(self.query_one("#respond-input", TextArea).text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Per-tool Rich markup renderers
# ---------------------------------------------------------------------------


def _render_write_file(path: str, content: str) -> str:
    """Deepagents-style write_file preview: header + green '+' lines."""
    lines = content.splitlines() if content else []
    total = len(lines)
    header = f"[bold cyan]{_escape(path)}[/bold cyan]  [green]+{total}[/green]"
    if not content:
        return header + "\n[dim](empty file)[/dim]"
    parts = [header]
    for ln in lines[:_MAX_LINES]:
        if len(ln) > _MAX_LINE_LEN:
            ln = ln[:_MAX_LINE_LEN] + "…"
        parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(ln)}[/]")
    if total > _MAX_LINES:
        parts.append(f"[dim]… ({total - _MAX_LINES} more lines)[/dim]")
    return "\n".join(parts)


def _render_edit_file(path: str, old_string: str, new_string: str) -> str:
    """Deepagents-style edit_file diff: unified diff with colored +/- lines."""
    old_lines = old_string.splitlines() if old_string else []
    new_lines = new_string.splitlines() if new_string else []

    raw_diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    # Skip the --- / +++ file header lines (first two)
    diff_lines = [l for l in raw_diff if not l.startswith(("---", "+++"))]

    adds = sum(1 for l in diff_lines if l.startswith("+"))
    dels = sum(1 for l in diff_lines if l.startswith("-"))
    header = (
        f"[bold cyan]{_escape(path)}[/bold cyan]  "
        f"[green]+{adds}[/green] [red]-{dels}[/red]"
    )

    if not diff_lines:
        # No unified diff (e.g. identical strings) — fall back to raw blocks
        parts = [header]
        for ln in old_lines[:_MAX_LINES]:
            if len(ln) > _MAX_LINE_LEN:
                ln = ln[:_MAX_LINE_LEN] + "…"
            parts.append(f"[{_DEL_FG} on {_DEL_BG}]- {_escape(ln)}[/]")
        for ln in new_lines[:_MAX_LINES]:
            if len(ln) > _MAX_LINE_LEN:
                ln = ln[:_MAX_LINE_LEN] + "…"
            parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(ln)}[/]")
        return "\n".join(parts)

    parts = [header]
    shown = 0
    for line in diff_lines:
        if shown >= _MAX_DIFF_LINES:
            remaining = len(diff_lines) - shown
            parts.append(f"[dim]… ({remaining} more lines)[/dim]")
            break
        if line.startswith("@@"):
            parts.append(f"[dim]{_escape(line)}[/dim]")
            shown += 1
            continue
        raw = line[1:] if len(line) > 1 else ""
        if len(raw) > _MAX_LINE_LEN:
            raw = raw[:_MAX_LINE_LEN] + "…"
        if line.startswith("-"):
            parts.append(f"[{_DEL_FG} on {_DEL_BG}]- {_escape(raw)}[/]")
        elif line.startswith("+"):
            parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(raw)}[/]")
        else:
            parts.append(f"[dim]  {_escape(raw)}[/dim]")
        shown += 1

    return "\n".join(parts)


def _render_memory_write(scope: str, file_arg: str, mode: str, content: str, content_file: str) -> str:
    """memory_write preview: header (scope/file [mode]) + green '+' lines."""
    header = (
        f"[bold cyan]{_escape(scope)}/{_escape(file_arg)}[/bold cyan]  "
        f"[dim][{_escape(mode)}][/dim]"
    )
    if content_file:
        import os as _os
        try:
            size = _os.path.getsize(content_file)
            size_str = f"{size:,} bytes"
        except OSError:
            size_str = "size unknown"
        return f"{header}\n[dim]from {_escape(content_file)}  ({size_str})[/dim]"

    lines = content.splitlines() if content else []
    total = len(lines)
    header += f"  [green]+{total}[/green]"
    if not content:
        return header + "\n[dim](empty content)[/dim]"

    parts = [header]
    for ln in lines[:_MAX_LINES]:
        if len(ln) > _MAX_LINE_LEN:
            ln = ln[:_MAX_LINE_LEN] + "…"
        parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(ln)}[/]")
    if total > _MAX_LINES:
        parts.append(f"[dim]… ({total - _MAX_LINES} more lines)[/dim]")
    return "\n".join(parts)


def _render_memory_update(scope: str, file_arg: str, old_text: str, new_text: str) -> str:
    """memory_update diff: unified diff of old_text → new_text with colored +/- lines."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []

    raw_diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=3))
    diff_lines = [l for l in raw_diff if not l.startswith(("---", "+++"))]

    adds = sum(1 for l in diff_lines if l.startswith("+"))
    dels = sum(1 for l in diff_lines if l.startswith("-"))
    header = (
        f"[bold cyan]{_escape(scope)}/{_escape(file_arg)}[/bold cyan]  "
        f"[green]+{adds}[/green] [red]-{dels}[/red]"
    )

    if not diff_lines:
        parts = [header]
        for ln in old_lines[:_MAX_LINES]:
            if len(ln) > _MAX_LINE_LEN:
                ln = ln[:_MAX_LINE_LEN] + "…"
            parts.append(f"[{_DEL_FG} on {_DEL_BG}]- {_escape(ln)}[/]")
        for ln in new_lines[:_MAX_LINES]:
            if len(ln) > _MAX_LINE_LEN:
                ln = ln[:_MAX_LINE_LEN] + "…"
            parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(ln)}[/]")
        return "\n".join(parts)

    parts = [header]
    shown = 0
    for line in diff_lines:
        if shown >= _MAX_DIFF_LINES:
            parts.append(f"[dim]… ({len(diff_lines) - shown} more lines)[/dim]")
            break
        if line.startswith("@@"):
            parts.append(f"[dim]{_escape(line)}[/dim]")
        else:
            raw = line[1:] if len(line) > 1 else ""
            if len(raw) > _MAX_LINE_LEN:
                raw = raw[:_MAX_LINE_LEN] + "…"
            if line.startswith("-"):
                parts.append(f"[{_DEL_FG} on {_DEL_BG}]- {_escape(raw)}[/]")
            elif line.startswith("+"):
                parts.append(f"[{_ADD_FG} on {_ADD_BG}]+ {_escape(raw)}[/]")
            else:
                parts.append(f"[dim]  {_escape(raw)}[/dim]")
        shown += 1
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool text dispatcher
# ---------------------------------------------------------------------------


def _build_tool_text(action_requests: list[dict]) -> str:
    parts = []
    for req in action_requests[:3]:
        tool = req.get("name", req.get("tool_name", req.get("type", "?")))
        inp  = req.get("args", req.get("tool_input", req.get("input", {}))) or {}
        tn   = tool.lower()

        if tn in _WRITE_TOOLS:
            parts.append(
                f"[bold]{_escape(tool)}[/bold]\n"
                + _render_write_file(
                    inp.get("file_path", inp.get("path", "?")),
                    inp.get("content", ""),
                )
            )
        elif tn in _EDIT_TOOLS:
            parts.append(
                f"[bold]{_escape(tool)}[/bold]\n"
                + _render_edit_file(
                    inp.get("file_path", inp.get("path", "?")),
                    inp.get("old_string", inp.get("old_text", "")),
                    inp.get("new_string", inp.get("new_text", "")),
                )
            )
        elif tn in _MEMORY_WRITE_TOOLS:
            parts.append(
                f"[bold]{_escape(tool)}[/bold]\n"
                + _render_memory_write(
                    inp.get("scope", "agent"),
                    inp.get("file", "?"),
                    inp.get("mode", "append"),
                    inp.get("content", ""),
                    inp.get("content_file", ""),
                )
            )
        elif tn in _MEMORY_EDIT_TOOLS:
            parts.append(
                f"[bold]{_escape(tool)}[/bold]\n"
                + _render_memory_update(
                    inp.get("scope", "agent"),
                    inp.get("file", "?"),
                    inp.get("old_text", ""),
                    inp.get("new_text", ""),
                )
            )
        elif inp:
            parts.append(f"[bold]{_escape(tool)}[/bold]\n{_render_args_lines(tool, inp)}")
        else:
            parts.append(f"[bold]{_escape(tool)}[/bold]")

    if len(action_requests) > 3:
        parts.append(f"[dim]… and {len(action_requests) - 3} more[/dim]")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# HITLPanel
# ---------------------------------------------------------------------------


class HITLPanel(Widget):
    """Handles both thread-level and subagent-level HITL interrupts."""

    can_focus = True
    can_focus_children = False

    DEFAULT_CSS = """
    HITLPanel {
        height: auto;
        max-height: 50%;
        background: $surface 92%;
        border: round $warning 80%;
        padding: 1 2;
    }
    HITLPanel:focus {
        border: round $warning;
    }
    #hitl-title {
        color: $warning;
        text-style: bold;
        margin-bottom: 1;
        text-align: center;
    }
    #hitl-tool-scroll {
        height: auto;
        max-height: 20;
        margin-bottom: 1;
    }
    #hitl-tool-info {
        height: auto;
        color: $text;
        margin: 0 1;
    }
    #hitl-bindings {
        color: $text-muted;
        margin-top: 1;
        text-align: center;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "approve", "Approve", show=True),
        Binding("y", "approve", "Approve", show=False),
        Binding("2", "edit", "Edit Args", show=True),
        Binding("e", "edit", "Edit Args", show=False),
        Binding("3", "session_approve", "Session", show=True),
        Binding("s", "session_approve", "Session", show=False),
        Binding("4", "reject", "Reject", show=True),
        Binding("n", "reject", "Reject", show=False),
        Binding("5", "respond", "Respond", show=True),
        Binding("r", "respond", "Respond", show=False),
        Binding("escape", "dismiss", "Dismiss", show=True),
    ]

    class Decision(Message):
        def __init__(
            self,
            thread_id: str,
            task_id: str,
            decisions: list[dict],
            is_subagent: bool,
            run_id: str = "",
        ) -> None:
            super().__init__()
            self.thread_id = thread_id
            self.task_id = task_id
            self.decisions = decisions
            self.is_subagent = is_subagent
            self.run_id = run_id

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.add_class("hitl-hidden")
        self._thread_id = ""
        self._task_id = ""
        self._interrupt_id = ""
        self._run_id = ""
        self._action_requests: list[dict] = []
        self._is_subagent = False

    def compose(self) -> ComposeResult:
        yield Static("⚠ TOOL APPROVAL", id="hitl-title")
        with VerticalScroll(id="hitl-tool-scroll"):
            yield Static("", id="hitl-tool-info")
        yield Static(
            "[dim][1/y] Approve  [2/e] Edit  [3/s] Session  [4/n] Reject  [5/r] Respond  [esc] Dismiss[/dim]",
            id="hitl-bindings",
        )

    def show(
        self,
        action_requests: list[dict],
        *,
        thread_id: str = "",
        task_id: str = "",
        interrupt_id: str = "",
        is_subagent: bool = False,
        run_id: str = "",
        is_background: bool = False,
    ) -> None:
        self._thread_id = thread_id
        self._task_id = task_id
        self._interrupt_id = interrupt_id
        self._run_id = run_id
        self._action_requests = action_requests or []
        self._is_subagent = is_subagent

        if is_subagent:
            target = f"subagent:{task_id[:12]}"
        elif is_background:
            target = f"bg run:{run_id[:8]}"
        else:
            target = f"thread:{thread_id[:12]}"

        count = len(self._action_requests)
        noun = "request" if count == 1 else "requests"
        try:
            self.query_one("#hitl-title", Static).update(
                f"⚠ TOOL APPROVAL  [dim]({count} {noun}) {target}[/dim]"
            )
        except Exception:
            pass

        try:
            self.query_one("#hitl-tool-info", Static).update(
                _build_tool_text(self._action_requests)
            )
        except Exception:
            pass

        self.remove_class("hitl-hidden")
        self.focus()

    def hide(self) -> None:
        self.add_class("hitl-hidden")

    def _n_decisions(self, decision_type: str, first: dict | None = None) -> list[dict]:
        n = len(self._action_requests)
        if not n:
            return [first or {"type": decision_type}]
        if first:
            rest = [{"type": decision_type}] * (n - 1)
            return [first] + rest
        return [{"type": decision_type}] * n

    def _post_decision(self, decisions: list[dict]) -> None:
        self.post_message(
            self.Decision(
                thread_id=self._thread_id,
                task_id=self._task_id,
                decisions=decisions,
                is_subagent=self._is_subagent,
                run_id=self._run_id,
            )
        )
        self.hide()

    def action_approve(self) -> None:
        self._post_decision(self._n_decisions("approve"))

    def action_session_approve(self) -> None:
        self._post_decision(self._n_decisions("approve_for_session"))

    def action_reject(self) -> None:
        self._post_decision(self._n_decisions("reject"))

    def action_edit(self) -> None:
        from rai.tui.widgets.approval import EditArgsScreen

        self.app.push_screen(EditArgsScreen(self._action_requests), self._on_edit_done)

    def _on_edit_done(self, result: dict | None) -> None:
        if result is None:
            return
        edited_actions = result.get("edited_actions", [])
        if not edited_actions:
            return
        first = {"type": "edit", "edited_action": edited_actions[0]}
        self._post_decision(self._n_decisions("approve", first=first))

    def action_respond(self) -> None:
        self.app.push_screen(_RespondModal(), self._on_respond_done)

    def _on_respond_done(self, message: str | None) -> None:
        if message is None:
            return
        decisions = [{"type": "respond", "message": message}] * max(1, len(self._action_requests))
        self._post_decision(decisions)

    def action_dismiss(self) -> None:
        self.hide()

    def on_click(self) -> None:
        self.focus()
