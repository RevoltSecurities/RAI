"""HITLScreen — modern tool approval modal for ClaudeHITLRequestEvent."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from rai.client.claude import ClaudeHITLRequestEvent


def _tool_icon(tool_name: str) -> str:
    tn = tool_name.lower()
    if tn == "bash" or tn == "execute":            return "⬢"
    if tn in ("read", "read_file"):                return "📖"
    if tn in ("write", "write_file"):              return "✍"
    if tn in ("edit", "edit_file", "multiedit"):   return "📝"
    if tn in ("web_search", "websearch"):          return "🔍"
    if tn in ("webfetch", "web_fetch"):            return "🌐"
    if tn.startswith("mcp__"):                                    return "🔌"
    if tn in ("taskcreate", "taskupdate", "taskget", "tasklist"): return "📋"
    if tn == "monitor":                                           return "👁"
    return "⚙"


def _esc(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _render_value(val: Any, indent: int = 0, max_str: int = 120) -> str:
    """Recursively render a value as readable Rich markup.

    - dicts   → key: value rows, indented with tree-style │ prefix
    - lists   → numbered or bulleted items, indented
    - strings → inline (short) or block-quoted (multiline)
    - scalars → coloured literals
    """
    pad = "  " * indent

    if isinstance(val, dict):
        if not val:
            return "[dim]{}[/dim]"
        lines = []
        items = list(val.items())
        for i, (k, v) in enumerate(items):
            connector = "└─" if i == len(items) - 1 else "├─"
            child = _render_value(v, indent + 1, max_str)
            # Single-line child → inline; multiline → next line indented
            if "\n" in child:
                lines.append(f"{pad}[dim]{connector}[/dim] [bold $primary]{_esc(str(k))}[/bold $primary]:")
                lines.append(child)
            else:
                lines.append(f"{pad}[dim]{connector}[/dim] [bold $primary]{_esc(str(k))}[/bold $primary]: {child}")
        return "\n".join(lines)

    if isinstance(val, list):
        if not val:
            return "[dim][][/dim]"
        lines = []
        for i, item in enumerate(val):
            bullet = f"[dim]{i + 1}.[/dim]"
            child  = _render_value(item, indent + 1, max_str)
            if "\n" in child:
                lines.append(f"{pad}  {bullet}")
                lines.append(child)
            else:
                lines.append(f"{pad}  {bullet} {child}")
        return "\n".join(lines)

    if isinstance(val, bool):
        color = "green" if val else "red"
        return f"[{color}]{val}[/{color}]"

    if isinstance(val, (int, float)):
        return f"[cyan]{_esc(str(val))}[/cyan]"

    if val is None:
        return "[dim]null[/dim]"

    # String — multiline or long → block
    s = str(val)
    lines = s.splitlines()
    if len(lines) > 1:
        shown  = lines[:12]
        hidden = len(lines) - 12
        block  = "\n".join(f"{pad}  [dim]│[/dim] {_esc(ln[:max_str])}" for ln in shown)
        if hidden > 0:
            block += f"\n{pad}  [dim]│ … +{hidden} lines[/dim]"
        return "\n" + block
    if len(s) > max_str:
        return f"[yellow]{_esc(s[:max_str])}…[/yellow]"
    return f"[yellow]{_esc(s)}[/yellow]"


def _kv(label: str, value: str) -> str:
    return f"[bold $primary]{label}:[/bold $primary] [yellow]{_esc(value)}[/yellow]"


def _render_tool_info(tool_name: str, tool_input: dict[str, Any]) -> str:
    tn = tool_name.lower()

    # ── Bash ─────────────────────────────────────────────────────────────────
    if tn in ("bash", "execute"):
        cmd   = str(tool_input.get("command", tool_input.get("input", "")))
        lines = cmd.splitlines()
        shown  = lines[:20]
        hidden = len(lines) - 20
        header = f"[dim]$[/dim] [bold]{_esc(shown[0][:200] if shown else '')}[/bold]"
        rest   = ("\n" + "\n".join(f"[dim]│[/dim] {_esc(ln[:200])}" for ln in shown[1:])) if len(shown) > 1 else ""
        tail   = f"\n[dim]│ … +{hidden} more lines[/dim]" if hidden > 0 else ""
        return header + rest + tail

    # ── Web ──────────────────────────────────────────────────────────────────
    if tn in ("web_search", "websearch"):
        q = tool_input.get("query", "")
        return _kv("query", str(q)[:300])

    if tn in ("webfetch", "web_fetch"):
        url = tool_input.get("url", tool_input.get("query", ""))
        return _kv("url", str(url)[:300])

    # ── Glob ─────────────────────────────────────────────────────────────────
    if tn == "glob":
        pattern = tool_input.get("pattern", "")
        path    = tool_input.get("path") or "[dim](cwd)[/dim]"
        return (
            f"[bold $primary]pattern:[/bold $primary] [yellow]{_esc(str(pattern))}[/yellow]\n"
            f"[bold $primary]path:[/bold $primary]    [dim]{_esc(str(path))}[/dim]"
        )

    # ── Grep ─────────────────────────────────────────────────────────────────
    if tn == "grep":
        pattern = tool_input.get("pattern", "")
        path    = tool_input.get("path") or "[dim](cwd)[/dim]"
        flags   = []
        if tool_input.get("-i"):  flags.append("-i")
        if tool_input.get("-n"):  flags.append("-n")
        if tool_input.get("multiline"): flags.append("--multiline")
        for flag in ("-B", "-A", "-C"):
            v = tool_input.get(flag)
            if v is not None:
                flags.append(f"{flag} {v}")
        glob_filter = tool_input.get("glob")
        mode        = tool_input.get("output_mode", "content")
        parts = [
            f"[bold $primary]pattern:[/bold $primary] [yellow]{_esc(str(pattern))}[/yellow]",
            f"[bold $primary]path:[/bold $primary]    [dim]{_esc(str(path))}[/dim]",
        ]
        if glob_filter:
            parts.append(f"[bold $primary]glob:[/bold $primary]    [dim]{_esc(str(glob_filter))}[/dim]")
        if flags:
            parts.append(f"[bold $primary]flags:[/bold $primary]   [dim]{_esc(' '.join(flags))}[/dim]")
        if mode != "content":
            parts.append(f"[bold $primary]mode:[/bold $primary]    [dim]{_esc(mode)}[/dim]")
        return "\n".join(parts)

    # ── LS ───────────────────────────────────────────────────────────────────
    if tn == "ls":
        path = tool_input.get("path", tool_input.get("file_path", ""))
        return _kv("path", str(path) or "(cwd)")

    # ── TodoWrite ────────────────────────────────────────────────────────────
    if tn == "todowrite":
        todos = tool_input.get("todos", [])
        if isinstance(todos, list):
            lines = []
            for t in todos[:20]:
                if isinstance(t, dict):
                    status = t.get("status", "pending")
                    icon = "[green]✓[/green]" if status == "completed" else (
                           "[yellow]▶[/yellow]" if status == "in_progress" else "[dim]○[/dim]")
                    pri  = t.get("priority", "")
                    pri_markup = f" [dim]{_esc(str(pri))}[/dim]" if pri else ""
                    lines.append(f"  {icon} [yellow]{_esc(str(t.get('content', ''))[:120])}[/yellow]{pri_markup}")
                else:
                    lines.append(f"  [dim]•[/dim] [yellow]{_esc(str(t)[:120])}[/yellow]")
            hidden = len(todos) - 20
            if hidden > 0:
                lines.append(f"  [dim]… +{hidden} more[/dim]")
            return "\n".join(lines)
        return _kv("todos", str(todos)[:200])

    # ── NotebookEdit ─────────────────────────────────────────────────────────
    if tn in ("notebookedit", "notebook_edit"):
        nb   = tool_input.get("notebook_path", "")
        cell = tool_input.get("cell_id", "")
        src  = tool_input.get("new_source", "")
        mode = tool_input.get("edit_mode", "replace")
        parts = [
            _kv("notebook", str(nb)),
            f"[bold $primary]mode:[/bold $primary]    [dim]{_esc(str(mode))}[/dim]",
        ]
        if cell:
            parts.append(f"[bold $primary]cell:[/bold $primary]    [dim]{_esc(str(cell))}[/dim]")
        if src:
            src_lines = str(src).splitlines()[:5]
            parts.append("[bold $primary]source:[/bold $primary]")
            for ln in src_lines:
                parts.append(f"  [dim]│[/dim] [yellow]{_esc(ln[:160])}[/yellow]")
            if len(str(src).splitlines()) > 5:
                parts.append(f"  [dim]│ … +{len(str(src).splitlines()) - 5} lines[/dim]")
        return "\n".join(parts)

    # ── TaskCreate ────────────────────────────────────────────────────────────
    if tn == "taskcreate":
        subj = tool_input.get("subject", "")
        desc = tool_input.get("description", "")
        af   = tool_input.get("activeForm", "")
        parts = [_kv("subject", str(subj)[:200])]
        if desc:
            desc_lines = str(desc).splitlines()
            shown = desc_lines[:6]
            parts.append("[bold $primary]description:[/bold $primary]")
            for ln in shown:
                parts.append(f"  [dim]│[/dim] [yellow]{_esc(ln[:160])}[/yellow]")
            if len(desc_lines) > 6:
                parts.append(f"  [dim]│ … +{len(desc_lines)-6} lines[/dim]")
        if af:
            parts.append(f"[bold $primary]activeForm:[/bold $primary]  [dim]{_esc(str(af)[:80])}[/dim]")
        return "\n".join(parts)

    # ── TaskUpdate ────────────────────────────────────────────────────────────
    if tn == "taskupdate":
        tid    = tool_input.get("taskId", "")
        status = tool_input.get("status")
        subj   = tool_input.get("subject")
        parts  = [_kv("taskId", str(tid)[:40])]
        if status:
            col = {"completed": "green", "in_progress": "yellow",
                   "deleted": "red", "pending": "dim"}.get(str(status), "yellow")
            parts.append(f"[bold $primary]status:[/bold $primary]  [{col}]{_esc(str(status))}[/{col}]")
        if subj:
            parts.append(_kv("subject", str(subj)[:160]))
        for extra in ("activeForm", "owner"):
            v = tool_input.get(extra)
            if v:
                parts.append(f"[bold $primary]{extra}:[/bold $primary]  [dim]{_esc(str(v)[:80])}[/dim]")
        for list_field in ("addBlocks", "addBlockedBy"):
            ids = tool_input.get(list_field)
            if ids:
                parts.append(
                    f"[bold $primary]{list_field}:[/bold $primary]  "
                    f"[dim]{_esc(', '.join(str(x)[:12] for x in ids[:5]))}[/dim]"
                )
        return "\n".join(parts)

    # ── TaskGet ───────────────────────────────────────────────────────────────
    if tn == "taskget":
        return _kv("taskId", str(tool_input.get("taskId", ""))[:60])

    # ── TaskList ──────────────────────────────────────────────────────────────
    if tn == "tasklist":
        return "[dim](returns full task list)[/dim]"

    # ── Monitor ───────────────────────────────────────────────────────────────
    if tn == "monitor":
        cmd     = str(tool_input.get("command", ""))
        desc    = tool_input.get("description", "")
        timeout = tool_input.get("timeout_ms")
        persist = tool_input.get("persistent", False)
        lines   = cmd.splitlines()
        shown   = lines[:10]
        hidden  = len(lines) - 10
        parts   = []
        if desc:
            parts.append(_kv("description", str(desc)[:160]))
        parts.append("[bold $primary]command:[/bold $primary]")
        for ln in shown:
            parts.append(f"  [dim]│[/dim] [yellow]{_esc(ln[:200])}[/yellow]")
        if hidden > 0:
            parts.append(f"  [dim]│ … +{hidden} more lines[/dim]")
        if persist:
            parts.append("[bold $primary]persistent:[/bold $primary] [green]yes[/green]")
        elif timeout:
            parts.append(f"[bold $primary]timeout:[/bold $primary]  [dim]{timeout // 1000}s[/dim]")
        return "\n".join(parts)

    # ── Generic fallback: readable tree ──────────────────────────────────────
    return _render_value(tool_input)


class HITLScreen(ModalScreen[str]):
    """Tool approval modal — blocks stream worker until user decides."""

    BINDINGS = [
        Binding("y", "allow",         "Allow",   show=False),
        Binding("s", "allow_session", "Session", show=False),
        Binding("n", "deny",          "Deny",    show=False),
        Binding("escape", "deny",     "Deny",    show=False, priority=True),
    ]

    CSS = """
    HITLScreen {
        align: center middle;
        background: $background 65%;
    }

    #hitl-container {
        width: 80%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $warning 55%;
        background: $surface;
        padding: 0 0 0 0;
    }

    #hitl-header {
        height: 3;
        background: $panel;
        border-bottom: solid $warning 20%;
        padding: 0 2;
        align: left middle;
    }

    #hitl-icon {
        width: 3;
        color: $warning;
    }

    #hitl-title {
        width: 1fr;
        color: $warning;
        text-style: bold;
    }

    #hitl-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #hitl-tool-scroll {
        height: auto;
        max-height: 16;
        padding: 1 2;
    }

    #hitl-tool-info {
        height: auto;
        background: $panel;
        border: round $primary 20%;
        padding: 1 2;
    }

    #hitl-footer {
        height: 3;
        padding: 0 2;
        align: right middle;
        border-top: solid $warning 20%;
    }

    #btn-allow {
        margin-left: 1;
        min-width: 12;
    }

    #btn-session {
        margin-left: 1;
        min-width: 14;
    }

    #btn-deny {
        margin-left: 1;
        min-width: 10;
    }
    """

    def __init__(self, event: ClaudeHITLRequestEvent) -> None:
        super().__init__()
        self._event = event
        self._icon    = _tool_icon(event.tool_name)
        self._display = event.display_name or event.tool_name
        self._info    = _render_tool_info(event.tool_name, event.input)

    def compose(self) -> ComposeResult:
        with Container(id="hitl-container"):
            with Horizontal(id="hitl-header"):
                yield Static(self._icon, id="hitl-icon")
                yield Static(f"[bold]{_esc(self._display)}[/bold]", id="hitl-title")
                yield Static("[bold red]\\[x][/bold red]", id="hitl-close")

            with VerticalScroll(id="hitl-tool-scroll"):
                yield Static(self._info, id="hitl-tool-info")

            with Horizontal(id="hitl-footer"):
                yield Button("✓ Allow", id="btn-allow", variant="success")
                yield Button("◇ Session", id="btn-session", variant="primary")
                yield Button("✗ Deny", id="btn-deny", variant="error")

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "hitl-close":
            self.action_deny()

    @on(Button.Pressed, "#btn-allow")
    def on_allow(self) -> None:
        self.action_allow()

    @on(Button.Pressed, "#btn-session")
    def on_allow_session(self) -> None:
        self.action_allow_session()

    @on(Button.Pressed, "#btn-deny")
    def on_deny(self) -> None:
        self.action_deny()

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_allow_session(self) -> None:
        self.dismiss("allow_session")

    def action_deny(self) -> None:
        self.dismiss("deny")
