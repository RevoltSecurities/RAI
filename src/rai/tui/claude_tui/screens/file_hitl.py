"""Rich HITL screens for file tools: Write, Edit, Read.

Each screen shows a scrollable content/diff preview and provides the same
Allow / Session / Deny button pattern as HITLScreen.
"""

from __future__ import annotations

import difflib
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.markup import escape as _rich_escape
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from rai.client.claude import ClaudeHITLRequestEvent


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _line_numbered(content: str, max_lines: int = 25) -> str:
    """Return Rich markup: green line-numbered content, truncated with hint."""
    lines = content.splitlines()
    shown  = lines[:max_lines]
    hidden = len(lines) - max_lines
    parts  = [
        f"[dim]{i:4d}│[/dim] [green]{_escape(ln[:200])}[/green]"
        for i, ln in enumerate(shown, 1)
    ]
    if hidden > 0:
        parts.append(f"[dim]  ▼ {hidden} more lines (scroll)[/dim]")
    return "\n".join(parts) if parts else "[dim](empty)[/dim]"


def _diff_markup(old_str: str, new_str: str, max_lines: int = 25) -> str:
    """Return Rich markup unified diff. Uses stdlib difflib — no extra deps."""
    old_lines = old_str.splitlines(keepends=True)
    new_lines = new_str.splitlines(keepends=True)
    raw = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))
    # Strip ---/+++ path headers
    body = [l for l in raw if not l.startswith("---") and not l.startswith("+++")]

    if not body:
        return "[dim](no changes)[/dim]"

    shown  = body[:max_lines]
    hidden = len(body) - max_lines
    parts  = []
    for ln in shown:
        content = _escape(ln[1:].rstrip()[:200]) if len(ln) > 1 else ""
        if ln.startswith("-"):
            parts.append(f"[red]  - {content}[/red]")
        elif ln.startswith("+"):
            parts.append(f"[green]  + {content}[/green]")
        elif ln.startswith("@@"):
            parts.append(f"[dim]{_escape(ln.strip())}[/dim]")
        else:
            parts.append(f"[dim]    {content}[/dim]")
    if hidden > 0:
        parts.append(f"[dim]  ▼ {hidden} more diff lines (scroll)[/dim]")
    return "\n".join(parts)


def _fmt_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    return f"{n_bytes / 1024:.1f} KB"


# ─────────────────────────────────────────────────────────────────────────────
# Shared button / keyboard mixin behaviour (identical across all three screens)
# ─────────────────────────────────────────────────────────────────────────────

class _FileHITLBase(ModalScreen[str]):
    """Common bindings and button handlers for all file HITL screens."""

    BINDINGS = [
        Binding("y", "allow",         "Allow",   show=False),
        Binding("s", "allow_session", "Session", show=False),
        Binding("n", "deny",          "Deny",    show=False),
        Binding("escape", "deny",     "Deny",    show=False, priority=True),
    ]

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) in (
            "write-close", "edit-close", "read-close"
        ):
            self.action_deny()

    @on(Button.Pressed, "#btn-allow")
    def _on_allow(self) -> None:
        self.action_allow()

    @on(Button.Pressed, "#btn-session")
    def _on_session(self) -> None:
        self.action_allow_session()

    @on(Button.Pressed, "#btn-deny")
    def _on_deny(self) -> None:
        self.action_deny()

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_allow_session(self) -> None:
        self.dismiss("allow_session")

    def action_deny(self) -> None:
        self.dismiss("deny")

    def _footer_buttons(self) -> ComposeResult:
        yield Button("✓ Allow",    id="btn-allow",   variant="success")
        yield Button("◇ Session",  id="btn-session",  variant="primary")
        yield Button("✗ Deny",     id="btn-deny",     variant="error")


# ─────────────────────────────────────────────────────────────────────────────
# WriteHITLScreen
# ─────────────────────────────────────────────────────────────────────────────

class WriteHITLScreen(_FileHITLBase):
    """Shows a line-numbered preview of the file to be written."""

    CSS = """
    WriteHITLScreen {
        align: center middle;
        background: $background 65%;
    }

    #write-container {
        width: 88%;
        max-width: 120;
        height: auto;
        max-height: 88%;
        border: round $warning 55%;
        background: $surface;
        padding: 0;
    }

    #write-header {
        height: 3;
        background: $panel;
        border-bottom: solid $warning 20%;
        padding: 0 2;
        align: left middle;
    }

    #write-icon { width: 3; color: $warning; }

    #write-title {
        width: 1fr;
        color: $warning;
        text-style: bold;
    }

    #write-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #write-scroll {
        height: auto;
        max-height: 24;
        padding: 1 2;
    }

    #write-content {
        height: auto;
        background: $panel;
        border: round $primary 20%;
        padding: 1 2;
    }

    #write-meta {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #write-footer {
        height: 3;
        padding: 0 2;
        align: right middle;
        border-top: solid $warning 20%;
    }

    #write-footer Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self, event: ClaudeHITLRequestEvent) -> None:
        super().__init__()
        inp        = event.input or {}
        self._path = inp.get("file_path", inp.get("path", ""))
        content    = inp.get("content", "")
        self._markup = _line_numbered(content, max_lines=25)
        n_lines  = len(content.splitlines()) if content else 0
        n_bytes  = len(content.encode()) if content else 0
        self._meta = f"[dim]{_escape(self._path)}  ·  {n_lines} lines  ·  {_fmt_size(n_bytes)}[/dim]"

    def compose(self) -> ComposeResult:
        with Container(id="write-container"):
            with Horizontal(id="write-header"):
                yield Static("✍",                                   id="write-icon")
                yield Static(f"Write  [dim]{_escape(self._path)}[/dim]", id="write-title")
                yield Static("[bold red]\\[x][/bold red]",           id="write-close")

            with VerticalScroll(id="write-scroll"):
                yield Static(self._markup, id="write-content")

            yield Static(self._meta, id="write-meta")

            with Horizontal(id="write-footer"):
                yield from self._footer_buttons()


# ─────────────────────────────────────────────────────────────────────────────
# EditHITLScreen
# ─────────────────────────────────────────────────────────────────────────────

class EditHITLScreen(_FileHITLBase):
    """Shows a unified diff preview of the edit to be applied."""

    CSS = """
    EditHITLScreen {
        align: center middle;
        background: $background 65%;
    }

    #edit-container {
        width: 88%;
        max-width: 120;
        height: auto;
        max-height: 88%;
        border: round $warning 55%;
        background: $surface;
        padding: 0;
    }

    #edit-header {
        height: 3;
        background: $panel;
        border-bottom: solid $warning 20%;
        padding: 0 2;
        align: left middle;
    }

    #edit-icon  { width: 3; color: $warning; }

    #edit-title {
        width: 1fr;
        color: $warning;
        text-style: bold;
    }

    #edit-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #edit-scroll {
        height: auto;
        max-height: 24;
        padding: 1 2;
    }

    #edit-diff {
        height: auto;
        background: $panel;
        border: round $primary 20%;
        padding: 1 2;
    }

    #edit-meta {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #edit-footer {
        height: 3;
        padding: 0 2;
        align: right middle;
        border-top: solid $warning 20%;
    }

    #edit-footer Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self, event: ClaudeHITLRequestEvent) -> None:
        super().__init__()
        inp         = event.input or {}
        self._path  = inp.get("file_path", inp.get("path", ""))
        old_str     = inp.get("old_string", inp.get("old_text", ""))
        new_str     = inp.get("new_string", inp.get("new_text", ""))
        self._markup = _diff_markup(old_str, new_str, max_lines=25)
        self._meta   = f"[dim]{_escape(self._path)}[/dim]"

    def compose(self) -> ComposeResult:
        with Container(id="edit-container"):
            with Horizontal(id="edit-header"):
                yield Static("📝",                                    id="edit-icon")
                yield Static(f"Edit  [dim]{_escape(self._path)}[/dim]", id="edit-title")
                yield Static("[bold red]\\[x][/bold red]",             id="edit-close")

            with VerticalScroll(id="edit-scroll"):
                yield Static(self._markup, id="edit-diff")

            yield Static(self._meta, id="edit-meta")

            with Horizontal(id="edit-footer"):
                yield from self._footer_buttons()


# ─────────────────────────────────────────────────────────────────────────────
# ReadHITLScreen
# ─────────────────────────────────────────────────────────────────────────────

class ReadHITLScreen(_FileHITLBase):
    """Shows file path and optional range — non-destructive so uses $primary border."""

    CSS = """
    ReadHITLScreen {
        align: center middle;
        background: $background 65%;
    }

    #read-container {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 70%;
        border: round $primary 55%;
        background: $surface;
        padding: 0;
    }

    #read-header {
        height: 3;
        background: $panel;
        border-bottom: solid $primary 20%;
        padding: 0 2;
        align: left middle;
    }

    #read-icon  { width: 3; color: $primary; }

    #read-title {
        width: 1fr;
        color: $primary;
        text-style: bold;
    }

    #read-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #read-scroll {
        height: auto;
        max-height: 10;
        padding: 1 2;
    }

    #read-info {
        height: auto;
        background: $panel;
        border: round $primary 20%;
        padding: 1 2;
    }

    #read-footer {
        height: 3;
        padding: 0 2;
        align: right middle;
        border-top: solid $primary 20%;
    }

    #read-footer Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self, event: ClaudeHITLRequestEvent) -> None:
        super().__init__()
        inp         = event.input or {}
        self._path  = inp.get("file_path", inp.get("path", ""))
        offset      = inp.get("offset")
        limit       = inp.get("limit")
        parts       = [f"[bold]Path:[/bold]  [green]{_escape(self._path)}[/green]"]
        if offset is not None:
            parts.append(f"[bold]From:[/bold]  line {offset}")
        if limit is not None:
            parts.append(f"[bold]Lines:[/bold] {limit}")
        self._info = "\n".join(parts)

    def compose(self) -> ComposeResult:
        with Container(id="read-container"):
            with Horizontal(id="read-header"):
                yield Static("📖",                                    id="read-icon")
                yield Static(f"Read  [dim]{_escape(self._path)}[/dim]", id="read-title")
                yield Static("[bold red]\\[x][/bold red]",             id="read-close")

            with VerticalScroll(id="read-scroll"):
                yield Static(self._info, id="read-info")

            with Horizontal(id="read-footer"):
                yield from self._footer_buttons()
