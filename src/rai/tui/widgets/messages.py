"""Message widgets: UserMsg, AssistantMsg, ThinkingMsg, ToolCallMsg, SubagentGroup, HistoryDivider."""

from __future__ import annotations

import itertools
import time
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static


def _escape(text: str) -> str:
    """Escape ALL [ and ] so Textual never mis-parses user text as markup tags."""
    return text.replace("[", "\\[").replace("]", "\\]")

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_spin_cycle = itertools.cycle(_SPINNER)

_WORKING_PHRASES = (
    "Thinking…", "Reasoning…", "Analyzing…", "Pondering…",
    "Processing…", "Investigating…", "Computing…", "Reflecting…",
    "Considering…", "Synthesizing…", "Evaluating…", "Deliberating…",
    "Examining…", "Formulating…", "Calibrating…", "Crunching data…",
)

_STATE_PENDING = "pending"
_STATE_RUNNING = "running"
_STATE_SUCCESS = "success"
_STATE_ERROR   = "error"
_STATE_DENIED  = "denied"

_TODO_TOOLS = {"write_todos", "TodoWrite", "todo_write", "create_todo"}

_STATUS_GLYPHS = {
    "completed":  ("[green]✓[/green]",   True),
    "in_progress": ("[yellow]→[/yellow]", False),
    "pending":    ("[dim]●[/dim]",        False),
}

_BASH_TOOLS         = frozenset({
    "bash", "shell", "run_bash", "execute_command", "terminal",
    "Bash", "run_command", "execute", "cmd", "Run",
})
_FILE_TOOLS         = frozenset({
    "read_file", "write_file", "edit_file", "view_file",
    "create_file", "glob", "grep",
    "Read", "Write", "Edit", "Glob", "Grep",
    "list_directory", "ls", "find", "NotebookRead", "NotebookEdit",
})
_WRITE_FILE_TOOLS   = frozenset({"write_file", "write", "create_file", "Write"})
_EDIT_FILE_TOOLS    = frozenset({"edit_file", "edit", "Edit", "NotebookEdit"})
_READ_FILE_TOOLS    = frozenset({"read_file", "view_file", "Read", "NotebookRead"})
_MEMORY_WRITE_TOOLS = frozenset({"memory_write"})
_MEMORY_EDIT_TOOLS  = frozenset({"memory_update"})
_PLAN_TOOLS         = frozenset({"write_plan"})
_WEB_TOOLS          = frozenset({
    "http_request", "web_fetch", "WebFetch", "fetch_url",
    "browser_navigate", "browser_click", "browser_snapshot",
    "browser_type", "browser_evaluate", "browser_take_screenshot",
})
_SEARCH_TOOLS       = frozenset({"web_search", "WebSearch", "search", "search_web"})
_AGENT_TOOLS        = frozenset({"spawn_agent", "create_agent", "Agent", "dispatch_agent"})


def _tool_icon(tool_name: str) -> str:
    """Return an emoji/glyph for the tool name, used in ToolCallMsg headers."""
    tn = tool_name.lower()
    t  = tool_name

    # MCP namespaced tools: mcp__server__tool — route by server name
    if tn.startswith("mcp__"):
        parts = tn.split("__")
        server = parts[1] if len(parts) > 1 else ""
        if any(x in server for x in ("docker", "container")):  return "🐳"
        if any(x in server for x in ("k8s", "kube", "helm")):  return "☸"
        if any(x in server for x in ("aws", "gcp", "azure", "cloud")): return "☁"
        if any(x in server for x in ("git", "github", "gitlab")): return "📦"
        if any(x in server for x in ("ldap", "ad", "active_directory", "okta")): return "🏢"
        if any(x in server for x in ("db", "sql", "postgres", "mysql", "mongo")): return "🗄"
        if any(x in server for x in ("slack", "email", "notify")): return "📨"
        if any(x in server for x in ("burp", "nmap", "nuclei", "scan",
                                      "apisec", "pentest", "vuln")): return "🎯"
        return "🔌"  # generic MCP

    # ── Shell / terminal ────────────────────────────────────────────────────
    if t in _BASH_TOOLS or tn in {x.lower() for x in _BASH_TOOLS}:
        return "⬢"

    # ── Memory / knowledge ──────────────────────────────────────────────────
    if t in _MEMORY_WRITE_TOOLS | _MEMORY_EDIT_TOOLS or "memory" in tn or "remember" in tn:
        return "🧠"

    # ── File operations ─────────────────────────────────────────────────────
    if t in _WRITE_FILE_TOOLS or tn in {x.lower() for x in _WRITE_FILE_TOOLS}:
        return "✍"
    if t in _EDIT_FILE_TOOLS or tn in {x.lower() for x in _EDIT_FILE_TOOLS}:
        return "📝"
    if t in _READ_FILE_TOOLS or tn in {x.lower() for x in _READ_FILE_TOOLS}:
        return "📖"

    # ── Active Directory / LDAP / Identity (before search — ldap_search → 🏢) ──
    if any(x in tn for x in ("ldap", "active_directory", "ad_", "_ad", "domain_user",
                              "domain_group", "okta", "saml", "kerberos", "ntlm")):
        return "🏢"

    # ── Database / SQL (before search — sql_query → 🗄) ─────────────────────
    if any(x in tn for x in ("sql", "database", "postgres", "mysql",
                              "mongo", "redis", "sqlite", "cassandra")):
        return "🗄"

    # ── Search / grep (before generic file tools to avoid 📁 for grep) ──────
    if t in _SEARCH_TOOLS or tn == "grep" or any(x in tn for x in ("search", "query")):
        return "🔍"
    if t in _FILE_TOOLS or tn in {x.lower() for x in _FILE_TOOLS}:
        return "📁"

    # ── Planning / todos ────────────────────────────────────────────────────
    if t in _PLAN_TOOLS:
        return "📋"
    if t in _TODO_TOOLS or tn in {x.lower() for x in _TODO_TOOLS}:
        return "☑"

    # ── Docker / containers ─────────────────────────────────────────────────
    if any(x in tn for x in ("docker", "container", "image", "registry", "compose")):
        return "🐳"

    # ── Kubernetes / Helm ───────────────────────────────────────────────────
    if any(x in tn for x in ("k8s", "kube", "kubectl", "helm", "pod", "deploy", "namespace",
                              "ingress", "service_account", "manifest")):
        return "☸"

    # ── Cloud (AWS / GCP / Azure) ───────────────────────────────────────────
    if any(x in tn for x in ("aws", "s3", "ec2", "lambda", "iam", "rds", "cloudwatch",
                              "gcp", "gke", "bigquery", "azure", "aks", "cloud")):
        return "☁"

    # ── Git / VCS ───────────────────────────────────────────────────────────
    if any(x in tn for x in ("git", "github", "gitlab", "commit", "branch",
                              "pull_request", "merge", "repo")):
        return "📦"

    # ── Messaging / notifications (before web — webhook_post → 📨 not 🌐) ───
    if any(x in tn for x in ("webhook", "send_email", "send_slack", "slack_msg",
                              "notify", "alert", "email_send", "teams", "discord")):
        return "📨"

    # ── Web / browser / HTTP ────────────────────────────────────────────────
    if t in _WEB_TOOLS or any(x in tn for x in ("web", "http", "browser", "fetch",
                                                  "url", "navigate", "screenshot", "playwright")):
        return "🌐"

    # ── Network / comms (before security — port_scan → 📡 not 🎯) ───────────
    if any(x in tn for x in ("ping", "traceroute", "dns", "whois", "netstat", "port",
                              "socket", "tcp", "udp", "ssl", "tls", "curl", "wget")):
        return "📡"

    # ── Security / pentest ──────────────────────────────────────────────────
    if any(x in tn for x in ("scan", "exploit", "nmap", "nuclei", "burp", "fuzz",
                              "payload", "inject", "xss", "sqli", "pentest", "vuln")):
        return "🎯"

    # ── Secrets / auth / crypto ─────────────────────────────────────────────
    if any(x in tn for x in ("secret", "vault", "cred", "auth", "token", "key",
                              "cert", "encrypt", "decrypt", "hash", "sign", "jwt")):
        return "🔑"

    # ── Messaging (general fallback) ────────────────────────────────────────
    if any(x in tn for x in ("send", "email", "slack", "message")):
        return "📨"

    # ── Agents / subagents ──────────────────────────────────────────────────
    if t in _AGENT_TOOLS or any(x in tn for x in ("agent", "spawn", "dispatch",
                                                    "worker", "subagent")):
        return "🤖"

    return "⚙"

_OUTPUT_MAX_LINES = 3   # collapsed inline preview cap
_OUTPUT_MAX_CHARS = 200  # per-line char cap


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _render_todos(todos: list) -> str:
    lines = []
    for item in todos:
        if isinstance(item, dict):
            content = item.get("content", str(item))
            status  = item.get("status", "pending")
        else:
            content = str(item)
            status  = "pending"
        glyph, dim = _STATUS_GLYPHS.get(status, ("[dim]●[/dim]", False))
        safe = _escape(str(content))
        lines.append(f"{glyph} [dim]{safe}[/dim]" if dim else f"{glyph} {safe}")
    return "\n".join(lines) if lines else "[dim](no todos)[/dim]"


def _truncate_input(val: Any, n: int = 80) -> str:
    if val is None:
        return ""
    s = str(val)
    return s[:n] + "…" if len(s) > n else s


def _args_inline(tool_name: str, tool_input: dict, max_len: int = 60) -> str:
    """Compact one-liner args for the tool header: ToolName(args_inline)."""
    if not tool_input:
        return ""
    tn = tool_name.lower()

    if tn in _BASH_TOOLS:
        cmd = str(tool_input.get("command", "")).strip()
        first = cmd.split("\n")[0]
        return first[:max_len] + "…" if len(first) > max_len else first

    if tn in _WRITE_FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("path") or "?"
        n = len((tool_input.get("content") or "").splitlines())
        return f"{path}  +{n} lines" if n else str(path)

    if tn in _EDIT_FILE_TOOLS:
        path   = tool_input.get("file_path") or tool_input.get("path") or "?"
        old_n  = len((tool_input.get("old_string") or tool_input.get("old_text") or "").splitlines())
        new_n  = len((tool_input.get("new_string") or tool_input.get("new_text") or "").splitlines())
        return f"{path}  +{new_n} -{old_n}"

    if tn in _MEMORY_WRITE_TOOLS:
        scope = tool_input.get("scope", "agent")
        file_arg = tool_input.get("file", "?")
        mode = tool_input.get("mode", "append")
        cf = tool_input.get("content_file", "")
        if cf:
            return f"{scope}/{file_arg}  [{mode}]  from file"
        n = len((tool_input.get("content") or "").splitlines())
        suffix = f"  +{n} lines" if n else ""
        return f"{scope}/{file_arg}  [{mode}]{suffix}"

    if tn in _MEMORY_EDIT_TOOLS:
        scope = tool_input.get("scope", "agent")
        file_arg = tool_input.get("file", "?")
        old_n = len((tool_input.get("old_text") or "").splitlines())
        new_n = len((tool_input.get("new_text") or "").splitlines())
        return f"{scope}/{file_arg}  +{new_n} -{old_n}"

    if tn in _PLAN_TOOLS:
        slug = tool_input.get("slug", "") or tool_input.get("title", "") or "plan"
        n = len((tool_input.get("content") or "").splitlines())
        return f"{_truncate_input(slug, 40)}  +{n} lines" if n else _truncate_input(slug, 60)

    if tn in _FILE_TOOLS:
        path = (tool_input.get("file_path") or tool_input.get("path")
                or tool_input.get("pattern") or "")
        if path:
            s = str(path)
            return s[:max_len] + "…" if len(s) > max_len else s

    # default: key=val pairs
    parts = []
    for k, v in list(tool_input.items())[:2]:
        vs = str(v)
        if len(vs) > 30:
            vs = vs[:30] + "…"
        parts.append(f"{k}={vs}")
    result = ", ".join(parts)
    return result[:max_len] + "…" if len(result) > max_len else result


def _format_output(raw: Any, expand: bool = False) -> str:
    """Tree-style output block:  └─ line1 / line2 / … +N more."""
    if raw is None:
        return ""

    import json as _json
    if isinstance(raw, (dict, list)):
        text = _json.dumps(raw, indent=2)
    else:
        text = str(raw)

    lines = text.splitlines()
    if not lines:
        return ""

    cap     = len(lines) if expand else _OUTPUT_MAX_LINES
    shown   = lines[:cap]
    hidden  = len(lines) - cap

    parts = []
    for i, ln in enumerate(shown):
        if len(ln) > _OUTPUT_MAX_CHARS:
            ln = ln[:_OUTPUT_MAX_CHARS] + "…"
        prefix = "  └─ " if i == 0 else "     "
        parts.append(f"{prefix}{_escape(ln)}")

    if hidden > 0 and not expand:
        parts.append(f"  [dim]… +{hidden} more lines[/dim]")

    return "\n".join(parts)


def _render_args_lines(tool_name: str, args: dict) -> str:
    """Rich markup for args display (used in HITLPanel and SubagentGroup preview)."""
    import json as _json
    if not args:
        return "[dim](no args)[/dim]"

    tn = tool_name.lower()

    if tn in _BASH_TOOLS:
        cmd = str(args.get("command", "")).strip()
        if cmd:
            lines = cmd.split("\n")
            shown = lines[:4]
            rest  = len(lines) - 4
            out   = "\n".join(f"[dim]$[/dim] {_escape(ln)}" for ln in shown)
            if rest > 0:
                out += f"\n[dim]… {rest} more lines[/dim]"
            return out

    if tn in _MEMORY_WRITE_TOOLS:
        scope    = args.get("scope", "agent")
        file_arg = args.get("file", "?")
        mode     = args.get("mode", "append")
        cf       = args.get("content_file", "")
        if cf:
            return (
                f"[cyan]{_escape(scope)}/{_escape(file_arg)}[/cyan]  "
                f"[dim][{mode}][/dim]  [dim]from {_escape(cf)}[/dim]"
            )
        n = len((args.get("content") or "").split("\n")) if args.get("content") else 0
        suffix = f"  [green]+{n} lines[/green]" if n else ""
        return f"[cyan]{_escape(scope)}/{_escape(file_arg)}[/cyan]  [dim][{mode}][/dim]{suffix}"

    if tn in _MEMORY_EDIT_TOOLS:
        scope    = args.get("scope", "agent")
        file_arg = args.get("file", "?")
        old_n = len((args.get("old_text") or "").split("\n"))
        new_n = len((args.get("new_text") or "").split("\n"))
        return (
            f"[cyan]{_escape(scope)}/{_escape(file_arg)}[/cyan]  "
            f"[green]+{new_n}[/green] [red]-{old_n}[/red]"
        )

    if tn in _FILE_TOOLS:
        path = (args.get("file_path") or args.get("path") or args.get("pattern") or "")
        if tn in _WRITE_FILE_TOOLS:
            n = len((args.get("content") or "").split("\n")) if args.get("content") else 0
            suffix = f"  [dim]+{n} lines[/dim]" if n else ""
            return f"[cyan]{_escape(str(path))}[/cyan]{suffix}"
        if tn in _EDIT_FILE_TOOLS:
            old_n = len((args.get("old_string") or args.get("old_text") or "").split("\n"))
            new_n = len((args.get("new_string") or args.get("new_text") or "").split("\n"))
            return (
                f"[cyan]{_escape(str(path))}[/cyan]  "
                f"[green]+{new_n}[/green] [red]-{old_n}[/red]"
            )
        if path:
            return f"[cyan]{_escape(str(path))}[/cyan]"

    out_lines = []
    for key, val in list(args.items())[:4]:
        vs = _json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        if len(vs) > 100:
            vs = vs[:100] + "…"
        out_lines.append(f"  [dim]{_escape(key)}:[/dim] {_escape(vs)}")
    if len(args) > 4:
        out_lines.append(f"  [dim]+{len(args) - 4} more[/dim]")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# UserMsg
# ---------------------------------------------------------------------------

class UserMsg(Static):
    DEFAULT_CSS = """
    UserMsg {
        padding: 0 1;
        margin: 0 0 1 0;
        color: $text;
    }
    """

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(f"[bold $accent]❯[/] {_escape(text)}", **kwargs)


# ---------------------------------------------------------------------------
# AssistantMsg
# ---------------------------------------------------------------------------

class AssistantMsg(Widget):
    """Streaming assistant response, batched at ~16fps."""

    DEFAULT_CSS = """
    AssistantMsg {
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        height: auto;
    }
    AssistantMsg .asst-spinner {
        height: auto;
        color: $primary;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._buf          = ""
        self._rendered_len = 0
        self._is_final     = False
        self._spin_pos     = 0
        self._start_time   = time.monotonic()
        self._spinner_timer = None
        self._flush_timer   = None

    def compose(self) -> ComposeResult:
        yield Static("", id="asst-spinner", classes="asst-spinner")
        md = Markdown("", id="assistant-md")
        md.display = False
        yield md

    def on_mount(self) -> None:
        self._spinner_timer = self.set_interval(0.25, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._buf:
            if self._spinner_timer:
                self._spinner_timer.stop()
                self._spinner_timer = None
            return
        elapsed = int(time.monotonic() - self._start_time)
        frame  = _SPINNER[self._spin_pos % len(_SPINNER)]
        phrase = _WORKING_PHRASES[(self._spin_pos // 20) % len(_WORKING_PHRASES)]
        self._spin_pos += 1
        try:
            self.query_one("#asst-spinner", Static).update(
                f"[yellow]{frame}[/yellow] {phrase} [dim]({elapsed}s, esc to interrupt)[/dim]"
            )
        except Exception:
            pass

    def _swap_to_markdown(self) -> None:
        try:
            self.query_one("#asst-spinner", Static).display = False
            self.query_one("#assistant-md",  Markdown).display = True
        except Exception:
            pass

    def _flush_markdown(self) -> None:
        if self._rendered_len >= len(self._buf):
            return
        try:
            self.query_one("#assistant-md", Markdown).update(self._buf)
            self._rendered_len = len(self._buf)
        except Exception:
            pass

    def append_text(self, chunk: str) -> None:
        first = not self._buf
        self._buf += chunk
        if first:
            self._swap_to_markdown()
            self._flush_timer = self.set_interval(0.06, self._flush_markdown)

    def set_final(self) -> None:
        self._is_final = True
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._flush_timer:
            self._flush_timer.stop()
            self._flush_timer = None
        if not self._buf:
            self._swap_to_markdown()
        else:
            self._flush_markdown()

    @property
    def text(self) -> str:
        return self._buf


# ---------------------------------------------------------------------------
# ThinkingMsg
# ---------------------------------------------------------------------------

class ThinkingMsg(Widget):
    """Extended-thinking collapsible with live elapsed timer in the title."""

    DEFAULT_CSS = """
    ThinkingMsg {
        height: auto;
        margin: 0 0 0 2;
    }
    ThinkingMsg Collapsible {
        background: transparent;
    }
    ThinkingMsg Static {
        color: $text-muted 60%;
        text-style: italic;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._buf        = ""
        self._dirty      = True
        self._start_time = time.monotonic()
        self._done       = False

    def compose(self) -> ComposeResult:
        with Collapsible(title="Thinking…", collapsed=True):
            yield Static("", id="thinking-content")

    def append(self, chunk: str) -> None:
        self._buf  += chunk
        self._dirty = True

    def mark_done(self) -> None:
        self._done  = True
        self._dirty = True

    def flush(self) -> None:
        """Push buffered content + update title. Called by app ticker (≤10fps)."""
        elapsed = time.monotonic() - self._start_time
        if not self._done:
            title       = f"Thinking… ({elapsed:.0f}s)"
            self._dirty = True   # keep ticking while active
        else:
            title = f"Thinking  ({elapsed:.0f}s)"

        if not self._dirty:
            return
        self._dirty = False

        try:
            self.query_one(Collapsible).title = title
            self.query_one("#thinking-content", Static).update(_escape(self._buf))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ToolCallMsg — Claude Code style
# ---------------------------------------------------------------------------

class ToolCallMsg(Widget):
    """Claude Code-style tool call: glyph + name(args) header + tree-style output."""

    DEFAULT_CSS = """
    ToolCallMsg {
        margin: 0 0 0 2;
        padding: 0;
        height: auto;
        background: transparent;
    }
    ToolCallMsg #tool-header { height: auto; }
    ToolCallMsg #tool-output {
        margin: 0 0 1 0;
        color: $text-muted;
        height: auto;
    }
    """

    def __init__(
        self,
        tool_name: str,
        tool_input: dict,
        tool_use_id: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.tool_name   = tool_name
        self.tool_input  = tool_input or {}
        self.tool_use_id = tool_use_id
        self._state      = _STATE_RUNNING
        self._start_time = time.monotonic()
        self._raw_output: Any = None
        self._expand     = False
        self._header_widget: Static | None = None

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), id="tool-header")
        if self.tool_name in _TODO_TOOLS:
            todos = self.tool_input.get("todos", [])
            yield Static(_render_todos(todos), id="tool-output")
        else:
            yield Static("", id="tool-output")

    # ------------------------------------------------------------------
    # Internal markup builders
    # ------------------------------------------------------------------

    def _header_markup(self) -> str:
        name = _escape(self.tool_name)
        args = _escape(_args_inline(self.tool_name, self.tool_input))
        icon = _tool_icon(self.tool_name)

        # ── write_plan (special: approval flow) ────────────────────────────
        if self.tool_name == "write_plan":
            if self._state == _STATE_RUNNING:
                elapsed = time.monotonic() - self._start_time
                frame   = _SPINNER[int(elapsed * 8) % len(_SPINNER)]
                return (
                    f"[yellow]{frame}[/yellow] [bold]📋 Plan[/bold]  "
                    f"[dim]{args}[/dim]  [dim]awaiting approval…[/dim]"
                )
            if self._state == _STATE_SUCCESS:
                result = str(self._raw_output or "").lower()
                label  = "approved" if "approved" in result else "done"
                return f"[green]✓[/green] [bold]📋 Plan[/bold]  [dim]{args}  {label}[/dim]"
            if self._state == _STATE_ERROR:
                return f"[red]✗[/red] [bold]📋 Plan[/bold]  [dim]{args}  rejected[/dim]"
            if self._state == _STATE_DENIED:
                return f"[dim]⊘ 📋 Plan  {args}[/dim]"

        # ── bash / shell tools (Kali Linux terminal style) ─────────────────
        if self.tool_name in _BASH_TOOLS or self.tool_name.lower() in {t.lower() for t in _BASH_TOOLS}:
            cmd   = str(self.tool_input.get("command", "")).strip()
            first = cmd.split("\n")[0] if cmd else ""
            disp  = (first[:80] + "…") if len(first) > 80 else first
            if self._state == _STATE_RUNNING:
                elapsed = time.monotonic() - self._start_time
                frame   = _SPINNER[int(elapsed * 8) % len(_SPINNER)]
                return (
                    f"[yellow]{frame}[/yellow] ⬢ [bold]{name}[/bold]  "
                    f"[dim]{_escape(disp)}[/dim]"
                )
            if self._state == _STATE_SUCCESS:
                return (
                    f"[green]✓[/green] ⬢ [bold]{name}[/bold]\n"
                    f"  [bright_blue]┌──([bold green]rai㉿rai[/bold green])-\\[~][/bright_blue]\n"
                    f"  [bright_blue]└─$[/bright_blue] [bold]{_escape(disp)}[/bold]"
                )
            if self._state == _STATE_ERROR:
                return (
                    f"[red]✗[/red] ⬢ [bold]{name}[/bold]\n"
                    f"  [bright_blue]┌──([bold green]rai㉿rai[/bold green])-\\[~][/bright_blue]\n"
                    f"  [bright_blue]└─$[/bright_blue] [bold red]{_escape(disp)}[/bold red]"
                )
            if self._state == _STATE_DENIED:
                return f"[dim]⊘ ⬢ {name}  {_escape(disp)}[/dim]"
            return f"⬢ [bold]{name}[/bold]  [dim]{_escape(disp)}[/dim]"

        # ── all other tools (icon + name(args)) ────────────────────────────
        if self._state == _STATE_RUNNING:
            elapsed = time.monotonic() - self._start_time
            frame   = _SPINNER[int(elapsed * 8) % len(_SPINNER)]
            return f"[yellow]{frame}[/yellow] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"
        if self._state == _STATE_SUCCESS:
            return f"[green]✓[/green] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"
        if self._state == _STATE_ERROR:
            return f"[red]✗[/red] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"
        if self._state == _STATE_DENIED:
            return f"[dim]⊘ {icon} {name}({args})[/dim]"
        return f"{icon} [bold]{name}[/bold]([dim]{args}[/dim])"

    def _header(self) -> Static | None:
        if self._header_widget is None:
            try:
                self._header_widget = self.query_one("#tool-header", Static)
            except Exception:
                return None
        return self._header_widget

    def _refresh_output(self) -> None:
        markup = _format_output(self._raw_output, self._expand)
        try:
            self.query_one("#tool-output", Static).update(markup)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def tick_spinner(self) -> None:
        if self._state != _STATE_RUNNING:
            return
        hw = self._header()
        if hw is None:
            return
        try:
            hw.update(self._header_markup())
        except Exception:
            self._header_widget = None

    def set_success(self, output: Any) -> None:
        self._state      = _STATE_SUCCESS
        self._raw_output = output
        self._header_widget = None
        try:
            self.query_one("#tool-header", Static).update(self._header_markup())
        except Exception:
            pass
        self._refresh_output()

    def set_error(self, output: Any) -> None:
        self._state      = _STATE_ERROR
        self._raw_output = output
        self._header_widget = None
        try:
            self.query_one("#tool-header", Static).update(self._header_markup())
        except Exception:
            pass
        # error output in red
        text  = str(output)[:500] if output else ""
        lines = text.splitlines()[:_OUTPUT_MAX_LINES]
        parts = []
        for i, ln in enumerate(lines):
            if len(ln) > _OUTPUT_MAX_CHARS:
                ln = ln[:_OUTPUT_MAX_CHARS] + "…"
            prefix = "  └─ " if i == 0 else "     "
            parts.append(f"[red]{prefix}{_escape(ln)}[/red]")
        try:
            self.query_one("#tool-output", Static).update("\n".join(parts))
        except Exception:
            pass

    def set_denied(self, reason: str = "") -> None:
        self._state      = _STATE_DENIED
        self._header_widget = None
        try:
            self.query_one("#tool-header", Static).update(self._header_markup())
            self.query_one("#tool-output", Static).update("")
        except Exception:
            pass

    def on_click(self) -> None:
        """Click toggles expanded output view."""
        if self._raw_output is not None:
            self._expand = not self._expand
            self._refresh_output()


# ---------------------------------------------------------------------------
# SubagentGroup
# ---------------------------------------------------------------------------

class SubagentGroup(Widget):
    """Container for a subagent's inline activity."""

    DEFAULT_CSS = """
    SubagentGroup {
        border-left: thick $success 35%;
        padding: 0 0 0 2;
        margin: 0 0 1 2;
        background: $surface 15%;
        height: auto;
    }
    """

    def __init__(self, task_id: str, agent_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.task_id       = task_id
        self.agent_name    = agent_name
        self._token_preview = ""
        self._preview_dirty = False
        self._tool_widgets: dict[str, ToolCallMsg] = {}

    def compose(self) -> ComposeResult:
        yield Static(f"[dim]◆ {_escape(self.agent_name)}[/dim]", id="subagent-header")
        yield Static("", id="subagent-preview")

    def append_token(self, content: str) -> None:
        self._token_preview += content
        self._preview_dirty  = True

    def flush_preview(self) -> None:
        if not self._preview_dirty:
            return
        self._preview_dirty = False
        preview = self._token_preview[-60:]
        try:
            self.query_one("#subagent-preview", Static).update(f"[dim]{_escape(preview)}[/dim]")
        except Exception:
            pass

    def add_tool(self, tool_use_id: str, tool_name: str, tool_input: dict) -> ToolCallMsg:
        widget = ToolCallMsg(tool_name, tool_input, tool_use_id=tool_use_id)
        self._tool_widgets[tool_use_id] = widget
        self.mount(widget)
        return widget

    def get_tool(self, tool_use_id: str) -> ToolCallMsg | None:
        return self._tool_widgets.get(tool_use_id)

    def mark_done(self, status: str) -> None:
        glyph = "[green]✔[/green]" if status == "completed" else "[red]✗[/red]"
        try:
            self.query_one("#subagent-header", Static).update(
                f"{glyph} [dim]{_escape(self.agent_name)}[/dim]"
            )
        except Exception:
            pass

    def tick_spinners(self) -> None:
        for w in self._tool_widgets.values():
            w.tick_spinner()


# ---------------------------------------------------------------------------
# HistoryDivider
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# StepCompleteMsg — plan step done indicator
# ---------------------------------------------------------------------------

class StepCompleteMsg(Static):
    """Compact step-done row: ✓ Step N  description (matches Claude Code style)."""

    DEFAULT_CSS = """
    StepCompleteMsg {
        margin: 0 0 0 2;
        height: auto;
    }
    """

    def __init__(self, step_number: int, description: str = "", **kwargs: Any) -> None:
        desc = f"  [dim]{_escape(description)}[/dim]" if description else ""
        super().__init__(f"[green]✓[/green] [bold]Step {step_number}[/bold]{desc}", **kwargs)


class StepStartMsg(Static):
    """▶ Step N  description — step started indicator."""

    DEFAULT_CSS = """
    StepStartMsg {
        margin: 0 0 0 2;
        height: auto;
    }
    """

    def __init__(self, step_number: int, description: str = "", **kwargs: Any) -> None:
        desc = f"  [dim]{_escape(description)}[/dim]" if description else ""
        super().__init__(f"[yellow]▶[/yellow] [bold]Step {step_number}[/bold]{desc}", **kwargs)


class StepBlockedMsg(Static):
    """✗ Step N  description  (reason) — step blocked indicator."""

    DEFAULT_CSS = """
    StepBlockedMsg {
        margin: 0 0 0 2;
        height: auto;
    }
    """

    def __init__(self, step_number: int, description: str = "", reason: str = "", **kwargs: Any) -> None:
        desc = f"  [dim]{_escape(description)}[/dim]" if description else ""
        rsn  = f"  [red dim]{_escape(reason)}[/red dim]" if reason else ""
        super().__init__(f"[red]✗[/red] [bold]Step {step_number}[/bold]{desc}{rsn}", **kwargs)


class PlanCompletedMsg(Static):
    """Plan completed banner — shown when all steps finish and the plan file is deleted."""

    DEFAULT_CSS = """
    PlanCompletedMsg {
        margin: 1 0 0 0;
        height: auto;
    }
    """

    def __init__(self, total_steps: int = 0, **kwargs: Any) -> None:
        steps_note = f" ({total_steps} steps)" if total_steps else ""
        super().__init__(
            f"[bold green]✔ Plan complete{steps_note}[/bold green]  [dim]plan file removed[/dim]",
            **kwargs,
        )


class PlanModeEnteredMsg(Static):
    """Claude Code-style ◈ RAI  Entering Plan Mode banner."""

    DEFAULT_CSS = """
    PlanModeEnteredMsg {
        margin: 0 0 0 2;
        height: auto;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            "[bold $warning]◈[/bold $warning] [bold]RAI[/bold]  "
            "[dim]Entering Plan Mode[/dim]",
            **kwargs,
        )


class HistoryDivider(Widget):
    """Visual separator shown when resuming a thread with loaded history."""

    DEFAULT_CSS = """
    HistoryDivider {
        height: 1;
        margin: 1 0;
        color: $text-muted 50%;
        content-align: center middle;
    }
    """

    def __init__(self, count: int, thread_id: str, total: int = 0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._count    = count
        self._thread_id = thread_id
        self._total    = total or count

    def compose(self) -> ComposeResult:
        tid = _escape(self._thread_id)
        if self._count < self._total:
            suffix = f"last {self._count} of {self._total} messages"
        else:
            suffix = f"{self._count} messages"
        yield Static(
            f"[dim]─── Resumed thread: [bold]{tid}[/bold]  ({suffix}) ───[/dim]"
        )


# ---------------------------------------------------------------------------
# CompactMsg — context compaction banner
# ---------------------------------------------------------------------------

class CompactMsg(Static):
    """◈ Context Compacted banner — shown after /compact runs."""

    DEFAULT_CSS = """
    CompactMsg {
        height: auto;
        margin: 1 0;
        padding: 0 2;
        border-left: thick cyan;
    }
    """

    def __init__(self, auto: bool = False, **kwargs: Any) -> None:
        label = "Auto-Compacted" if auto else "Context Compacted"
        lines = [
            f"[bold cyan]◈ {label}[/bold cyan]",
            "  [dim]Older messages summarized — next run will use significantly less context.[/dim]",
        ]
        super().__init__("\n".join(lines), **kwargs)


# ---------------------------------------------------------------------------
# CompactWarningMsg — context approaching limit
# ---------------------------------------------------------------------------

class CompactWarningMsg(Static):
    """⚠ Context approaching limit — shown when compact_status.should_compact is True."""

    DEFAULT_CSS = """
    CompactWarningMsg {
        height: auto;
        margin: 1 0;
        padding: 0 2;
        border-left: thick yellow;
    }
    """

    def __init__(self, estimated_tokens: int, message_count: int, **kwargs: Any) -> None:
        def _fmt(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.0f}k"
            return str(n)
        super().__init__(
            f"[bold yellow]⚠ Context approaching limit[/bold yellow]  "
            f"[dim]~{_fmt(estimated_tokens)} tokens · {message_count} messages"
            f" — run [bold]/compact[/bold] to free up space[/dim]",
            **kwargs,
        )
