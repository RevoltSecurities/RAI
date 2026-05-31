"""RAI approval menu — extends deepagents' ApprovalMenu with an Edit option.

Adds a 4th option "Edit" that opens a modal JSON editor for tool call
arguments.  The SDK's ``HumanInTheLoopMiddleware`` already supports
``EditDecision``; this widget exposes that capability in the TUI.

Also provides ``RAIWriteFileApprovalWidget``: a Static-only replacement for
deepagents' ``WriteFileApprovalWidget``.  The upstream widget uses Textual's
``Markdown`` widget, which runs ``parser.parse()`` in a thread pool via
``run_in_executor``.  The subsequent ``mount_all`` + ``batch_update`` fires
*after* ``_update_options()`` and wipes the option-text via a layout
recalculation, leaving the menu blank.  Using ``Static`` widgets avoids all
async mounting and renders synchronously inside ``compose()``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from deepagents_cli.config import get_glyphs
from deepagents_cli.widgets.approval import ApprovalMenu
from deepagents_cli.widgets.tool_widgets import ToolApprovalWidget

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)

# 4 options: Approve / Edit / Auto-approve / Reject
_NUM_OPTIONS = 4

# Match deepagents' display limits
_MAX_LINES = 30
_MAX_VALUE_LEN = 200


# ---------------------------------------------------------------------------
# Static-only write_file approval widget (no Markdown → no async mounting)
# ---------------------------------------------------------------------------


class RAIWriteFileApprovalWidget(ToolApprovalWidget):
    """Write-file approval widget that uses only Static widgets.

    Deepagents' ``WriteFileApprovalWidget`` yields a ``Markdown`` code-block
    widget whose ``_on_mount`` parses the content in a thread pool and then
    calls ``mount_all`` inside ``app.batch_update()``.  That async work fires
    after ``_update_options()`` and a DOM layout recalculation drops the
    option-text dirty regions, leaving the approval menu blank.

    This widget renders the same information (file path, line count, content
    preview, truncation notice) using only ``Static`` widgets, which compose
    synchronously and trigger no async DOM work.
    """

    def compose(self) -> ComposeResult:
        file_path = self.data.get("file_path", "")
        content = self.data.get("content", "")

        lines = content.split("\n") if content else []
        total_lines = len(lines)

        # File header: path + addition count
        additions = total_lines if content else 0
        yield Static(
            Content.assemble(
                Content.from_markup("[bold cyan]File:[/bold cyan] $path  +$n", path=file_path, n=additions),
            )
        )
        yield Static("")

        if not content:
            yield Static("(empty file)", classes="approval-description")
            return

        shown = lines[:_MAX_LINES]
        for line in shown:
            # Truncate very long individual lines to keep layout sane
            if len(line) > _MAX_VALUE_LEN:
                line = line[:_MAX_VALUE_LEN] + "…"
            yield Static(line, markup=False, classes="approval-description")

        if total_lines > _MAX_LINES:
            remaining = total_lines - _MAX_LINES
            yield Static(
                Content.styled(f"… ({remaining} more lines)", "dim"),
            )


# ---------------------------------------------------------------------------
# memory_write approval widget
# ---------------------------------------------------------------------------


class RAIMemoryWriteApprovalWidget(ToolApprovalWidget):
    """Approval widget for memory_write — mirrors RAIWriteFileApprovalWidget.

    Renders scope/file header + truncated content preview using only Static
    widgets so compose() is synchronous and never triggers async DOM work.
    """

    def compose(self) -> ComposeResult:
        import os as _os
        file_arg = self.data.get("file", "?")
        scope = self.data.get("scope", "agent")
        mode = self.data.get("mode", "append")
        content_file: str = self.data.get("content_file", "")
        content: str = self.data.get("content", "")

        if content_file:
            # Large-content path — show source path + size, no inline preview
            try:
                size = _os.path.getsize(content_file)
                size_str = f"{size:,} bytes"
            except OSError:
                size_str = "size unknown"
            yield Static(
                Content.from_markup(
                    "[bold cyan]memory_write:[/bold cyan] $scope/$file  [$mode]  (from file)",
                    scope=scope, file=file_arg, mode=mode,
                )
            )
            yield Static("")
            yield Static(
                Content.from_markup(
                    "[dim]Source:[/dim] $src  [dim]$sz[/dim]",
                    src=content_file, sz=size_str,
                ),
                classes="approval-description",
            )
            return

        lines = content.split("\n") if content else []
        total_lines = len(lines)

        yield Static(
            Content.from_markup(
                "[bold cyan]memory_write:[/bold cyan] $scope/$file  [$mode]  +$n lines",
                scope=scope, file=file_arg, mode=mode, n=total_lines,
            )
        )
        yield Static("")

        if not content:
            yield Static("(empty content)", classes="approval-description")
            return

        shown = lines[:_MAX_LINES]
        for line in shown:
            if len(line) > _MAX_VALUE_LEN:
                line = line[:_MAX_VALUE_LEN] + "…"
            yield Static(line, markup=False, classes="approval-description")

        if total_lines > _MAX_LINES:
            remaining = total_lines - _MAX_LINES
            yield Static(Content.styled(f"… ({remaining} more lines)", "dim"))


# ---------------------------------------------------------------------------
# memory_update approval widget
# ---------------------------------------------------------------------------


class RAIMemoryUpdateApprovalWidget(ToolApprovalWidget):
    """Approval widget for memory_update — shows old → new text truncated.

    Uses only Static widgets (synchronous compose, no async DOM work).
    """

    def compose(self) -> ComposeResult:
        file_arg = self.data.get("file", "?")
        scope = self.data.get("scope", "agent")
        old_text: str = self.data.get("old_text", "")
        new_text: str = self.data.get("new_text", "")

        yield Static(
            Content.from_markup(
                "[bold cyan]memory_update:[/bold cyan] $scope/$file",
                scope=scope, file=file_arg,
            )
        )
        yield Static("")

        yield Static(Content.styled("Remove:", "bold red"))
        yield from self._render_block(old_text)
        yield Static("")
        yield Static(Content.styled("Add:", "bold green"))
        yield from self._render_block(new_text)

    @staticmethod
    def _render_block(text: str) -> ComposeResult:
        if not text:
            yield Static("(empty)", classes="approval-description")
            return
        lines = text.split("\n")
        for line in lines[:_MAX_LINES]:
            if len(line) > _MAX_VALUE_LEN:
                line = line[:_MAX_VALUE_LEN] + "…"
            yield Static(line, markup=False, classes="approval-description")
        if len(lines) > _MAX_LINES:
            remaining = len(lines) - _MAX_LINES
            yield Static(Content.styled(f"… ({remaining} more lines)", "dim"))


# ---------------------------------------------------------------------------
# edit_file approval widget
# ---------------------------------------------------------------------------


class RAIEditFileApprovalWidget(ToolApprovalWidget):
    """Approval widget for edit_file — shows file path + old/new string truncated.

    Deepagents' GenericApprovalWidget inlines old_string/new_string verbatim.
    A 10 000-char old_string inside a single Static widget stalls Textual's
    layout engine and prevents all subsequent widgets from mounting.  This
    widget truncates both strings to _MAX_LINES × _MAX_VALUE_LEN before
    rendering, using only Static widgets (synchronous compose, no async work).
    """

    def compose(self) -> ComposeResult:
        file_path = self.data.get("file_path", "?")
        old_string: str = self.data.get("old_string", "")
        new_string: str = self.data.get("new_string", "")
        replace_all: bool = self.data.get("replace_all", False)

        yield Static(
            Content.from_markup(
                "[bold cyan]edit_file:[/bold cyan] $path",
                path=file_path,
            )
        )
        if replace_all:
            yield Static(Content.styled("(replace_all=True)", "dim"))
        yield Static("")

        yield Static(Content.styled("Find:", "bold red"))
        yield from self._render_block(old_string)
        yield Static("")
        yield Static(Content.styled("Replace with:", "bold green"))
        yield from self._render_block(new_string)

    @staticmethod
    def _render_block(text: str) -> ComposeResult:
        if not text:
            yield Static("(empty)", classes="approval-description")
            return
        lines = text.split("\n")
        for line in lines[:_MAX_LINES]:
            if len(line) > _MAX_VALUE_LEN:
                line = line[:_MAX_VALUE_LEN] + "…"
            yield Static(line, markup=False, classes="approval-description")
        if len(lines) > _MAX_LINES:
            remaining = len(lines) - _MAX_LINES
            yield Static(Content.styled(f"… ({remaining} more lines)", "dim"))


# ---------------------------------------------------------------------------
# Modal JSON editor
# ---------------------------------------------------------------------------


class EditArgsScreen(ModalScreen[dict[str, Any] | None]):
    """Full-screen modal for editing tool call arguments as JSON.

    Returns ``{"edited_actions": [...]}`` on save, or ``None`` on cancel.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "save", "Save", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    EditArgsScreen {
        align: center middle;
    }
    EditArgsScreen > Vertical {
        width: 90;
        max-width: 95%;
        height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    EditArgsScreen .edit-title {
        text-style: bold;
        margin-bottom: 1;
    }
    EditArgsScreen .edit-error {
        color: red;
        text-style: bold;
        margin-bottom: 1;
    }
    EditArgsScreen .edit-help {
        dock: bottom;
        color: $text-muted;
        margin-top: 1;
    }
    EditArgsScreen TextArea {
        height: 1fr;
    }
    """

    def __init__(self, action_requests: list[dict[str, Any]]) -> None:
        super().__init__()
        self._action_requests = action_requests
        self._error_widget: Static | None = None

    def _build_payload(self) -> str:
        if len(self._action_requests) == 1:
            req = self._action_requests[0]
            payload: Any = {"name": req.get("name", "unknown"), "args": req.get("args", {})}
        else:
            payload = [
                {"name": req.get("name", "unknown"), "args": req.get("args", {})}
                for req in self._action_requests
            ]
        return json.dumps(payload, indent=2)

    def compose(self) -> ComposeResult:
        with Vertical():
            count = len(self._action_requests)
            if count == 1:
                name = self._action_requests[0].get("name", "unknown")
                title_text = f"Edit tool call: {name}"
            else:
                title_text = f"Edit {count} tool calls"
            yield Static(title_text, classes="edit-title")

            self._error_widget = Static("", classes="edit-error")
            self._error_widget.display = False
            yield self._error_widget

            yield TextArea(
                self._build_payload(),
                language="json",
                theme="monokai",
                show_line_numbers=True,
                id="edit-area",
            )

            yield Static(
                "Ctrl+S  save and approve with edits  |  Escape  cancel",
                classes="edit-help",
            )

    def on_mount(self) -> None:
        try:
            self.query_one("#edit-area", TextArea).focus()
        except Exception:
            pass

    def action_save(self) -> None:
        try:
            area = self.query_one("#edit-area", TextArea)
        except Exception:
            return

        raw = area.text.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._show_error(f"JSON error: {exc}")
            return

        edited_actions: list[dict[str, Any]] = []

        if isinstance(parsed, dict):
            edited_actions.append({
                "name": parsed.get("name", self._action_requests[0].get("name", "unknown")),
                "args": parsed.get("args", {}),
            })
        elif isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    self._show_error("Each item must be a JSON object with 'name' and 'args'.")
                    return
                edited_actions.append({"name": item.get("name", "unknown"), "args": item.get("args", {})})
        else:
            self._show_error("Expected a JSON object or array.")
            return

        self.dismiss({"edited_actions": edited_actions})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _show_error(self, message: str) -> None:
        if self._error_widget is not None:
            self._error_widget.update(
                Content.from_markup("[bold red]Error:[/bold red] $msg", msg=message)
            )
            self._error_widget.display = True


# ---------------------------------------------------------------------------
# Extended approval menu
# ---------------------------------------------------------------------------


class RAIApprovalMenu(ApprovalMenu):
    """Approval menu with an additional *Edit* option.

    Options:
        1. Approve  (y)
        2. Edit     (e)  — opens a modal JSON editor
        3. Auto-approve for this thread (a)
        4. Reject   (n)
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("j", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_approve", "Approve", show=False),
        Binding("y", "select_approve", "Approve", show=False),
        Binding("2", "select_edit", "Edit", show=False),
        Binding("e", "select_edit", "Edit", show=False),
        Binding("3", "select_auto", "Auto-approve", show=False),
        Binding("a", "select_auto", "Auto-approve", show=False),
        Binding("4", "select_reject", "Reject", show=False),
        Binding("n", "select_reject", "Reject", show=False),
        # 'x' expands command; 'e' is now Edit
        Binding("x", "toggle_expand", "Expand command", show=False),
    ]

    async def on_mount(self) -> None:
        # Let base class set up tool info and populate the 3 existing option
        # widgets.  Our _update_options override guards against the missing 4th
        # widget so no IndexError is raised here.
        await super().on_mount()
        # Base compose() created 3 option widgets.  Add the 4th (Edit) now that
        # super().on_mount() has finished — Widget.mount() must be awaited.
        if len(self._option_widgets) < _NUM_OPTIONS:
            widget = Static("", classes="approval-option")
            self._option_widgets.append(widget)
            options_container = self.query_one(".approval-options-container")
            await options_container.mount(widget)
        # Refresh all 4 option labels now that every widget is in the DOM.
        self._update_options()
        # Re-apply after the next render cycle in case any pending DOM work
        # (e.g. other async children) batches with the refresh above.
        self.call_after_refresh(self._update_options)

    def _update_options(self) -> None:
        count = len(self._action_requests)
        glyphs = get_glyphs()

        if count == 1:
            options = [
                "1. Approve (y)",
                "2. Edit (e)",
                "3. Auto-approve for this thread (a)",
                "4. Reject (n)",
            ]
        else:
            options = [
                f"1. Approve all {count} (y)",
                "2. Edit (e)",
                "3. Auto-approve for this thread (a)",
                f"4. Reject all {count} (n)",
            ]

        for i, text in enumerate(options):
            if i >= len(self._option_widgets):
                break  # 4th widget not yet mounted; on_mount calls again after
            cursor = f"{glyphs.cursor} " if i == self._selected else "  "
            w = self._option_widgets[i]
            w.update(f"{cursor}{text}")
            w.remove_class("approval-option-selected")
            if i == self._selected:
                w.add_class("approval-option-selected")

    def action_move_up(self) -> None:
        self._selected = (self._selected - 1) % _NUM_OPTIONS
        self._update_options()

    def action_move_down(self) -> None:
        self._selected = (self._selected + 1) % _NUM_OPTIONS
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self._selected)

    def action_select_approve(self) -> None:
        self._selected = 0
        self._update_options()
        self._handle_selection(0)

    def action_select_edit(self) -> None:
        self._selected = 1
        self._update_options()
        self._open_edit_modal()

    def action_select_auto(self) -> None:
        self._selected = 2
        self._update_options()
        self._handle_selection(2)

    def action_select_reject(self) -> None:
        self._selected = 3
        self._update_options()
        self._handle_selection(3)

    def _handle_selection(self, option: int) -> None:
        if option == 1:
            self._open_edit_modal()
            return

        decision_map = {0: "approve", 2: "auto_approve_all", 3: "reject"}
        decision = {"type": decision_map[option]}

        if self._future and not self._future.done():
            self._future.set_result(decision)

        self.post_message(self.Decided(decision))

    def _open_edit_modal(self) -> None:
        screen = EditArgsScreen(self._action_requests)
        self.app.push_screen(screen, self._on_edit_result)

    def _on_edit_result(self, result: dict[str, Any] | None) -> None:
        if result is None:
            self.focus()
            return

        decision = {"type": "edit", "edited_actions": result["edited_actions"]}

        if self._future and not self._future.done():
            self._future.set_result(decision)

        self.post_message(self.Decided(decision))
