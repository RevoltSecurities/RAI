"""ToolCallWidget — Claude Code-style tool call with icons, Kali Bash header, live spinner."""

from __future__ import annotations

import itertools
import time
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


_BLOOM = ("·", ".", "+", "✶", "✻", "✽", "✶", "+", "·", ".")

_STATE_RUNNING = "running"
_STATE_SUCCESS = "success"
_STATE_ERROR   = "error"

_BASH_TOOLS   = frozenset({
    "bash", "shell", "run_bash", "execute_command", "terminal",
    "Bash", "run_command", "execute", "cmd", "Run",
})
_WRITE_TOOLS  = frozenset({"write_file", "write", "create_file", "Write"})
_EDIT_TOOLS   = frozenset({"edit_file", "edit", "Edit", "NotebookEdit"})
_READ_TOOLS   = frozenset({"read_file", "view_file", "Read", "NotebookRead"})
_FILE_TOOLS   = frozenset({
    "glob", "grep", "Glob", "Grep",
    "list_directory", "ls", "find", "LS",
})
_SEARCH_TOOLS = frozenset({
    "web_search", "WebSearch", "search", "search_web",
    "web_fetch", "WebFetch",
})

_OUTPUT_MAX_LINES = 3
_OUTPUT_MAX_CHARS = 200


def _tool_icon(tool_name: str) -> str:
    tn = tool_name.lower()
    t  = tool_name

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
        return "🔌"

    if t in _BASH_TOOLS or tn in {x.lower() for x in _BASH_TOOLS}:
        return "⬢"

    if "memory" in tn or "remember" in tn:
        return "🧠"

    if t in _WRITE_TOOLS or tn in {x.lower() for x in _WRITE_TOOLS}:
        return "✍"
    if t in _EDIT_TOOLS or tn in {x.lower() for x in _EDIT_TOOLS}:
        return "📝"
    if t in _READ_TOOLS or tn in {x.lower() for x in _READ_TOOLS}:
        return "📖"

    if any(x in tn for x in ("ldap", "active_directory", "ad_", "_ad", "domain_user",
                              "domain_group", "okta", "saml", "kerberos", "ntlm")):
        return "🏢"

    if any(x in tn for x in ("sql", "database", "postgres", "mysql",
                              "mongo", "redis", "sqlite", "cassandra")):
        return "🗄"

    if t in _SEARCH_TOOLS or tn == "grep" or any(x in tn for x in ("search", "query")):
        return "🔍"
    if t in _FILE_TOOLS or tn in {x.lower() for x in _FILE_TOOLS}:
        return "📁"

    if any(x in tn for x in ("docker", "container", "image", "registry", "compose")):
        return "🐳"

    if any(x in tn for x in ("k8s", "kube", "kubectl", "helm", "pod", "deploy", "namespace",
                              "ingress", "service_account", "manifest")):
        return "☸"

    if any(x in tn for x in ("aws", "s3", "ec2", "lambda", "iam", "rds", "cloudwatch",
                              "gcp", "gke", "bigquery", "azure", "aks", "cloud")):
        return "☁"

    if any(x in tn for x in ("git", "github", "gitlab", "commit", "branch",
                              "pull_request", "merge", "repo")):
        return "📦"

    if any(x in tn for x in ("webhook", "send_email", "send_slack", "slack_msg",
                              "notify", "alert", "email_send", "teams", "discord")):
        return "📨"

    if t in _SEARCH_TOOLS or any(x in tn for x in ("web", "http", "browser", "fetch",
                                                     "url", "navigate", "screenshot", "playwright")):
        return "🌐"

    if any(x in tn for x in ("ping", "traceroute", "dns", "whois", "netstat", "port",
                              "socket", "tcp", "udp", "ssl", "tls", "curl", "wget")):
        return "📡"

    if any(x in tn for x in ("scan", "exploit", "nmap", "nuclei", "burp", "fuzz",
                              "payload", "inject", "xss", "sqli", "pentest", "vuln")):
        return "🎯"

    if any(x in tn for x in ("secret", "vault", "cred", "auth", "token", "key",
                              "cert", "encrypt", "decrypt", "hash", "sign", "jwt")):
        return "🔑"

    if any(x in tn for x in ("send", "email", "slack", "message")):
        return "📨"

    if any(x in tn for x in ("agent", "spawn", "dispatch", "worker", "subagent")):
        return "🤖"

    if t in ("TaskCreate", "TaskUpdate", "TaskGet", "TaskList"):
        return "📋"

    if t == "Monitor":
        return "👁"

    return "⚙"


def _args_inline(tool_name: str, tool_input: dict, max_len: int = 60) -> str:
    if not tool_input:
        return ""
    tn = tool_name.lower()

    if tool_name in _BASH_TOOLS or tn in {x.lower() for x in _BASH_TOOLS}:
        cmd = str(tool_input.get("command", "")).strip()
        first = cmd.split("\n")[0]
        return first[:max_len] + "…" if len(first) > max_len else first

    if tool_name in _WRITE_TOOLS or tn in {x.lower() for x in _WRITE_TOOLS}:
        path = tool_input.get("file_path") or tool_input.get("path") or "?"
        n = len((tool_input.get("content") or "").splitlines())
        return f"{path}  +{n} lines" if n else str(path)

    if tool_name in _EDIT_TOOLS or tn in {x.lower() for x in _EDIT_TOOLS}:
        path  = tool_input.get("file_path") or tool_input.get("path") or "?"
        old_n = len((tool_input.get("old_string") or tool_input.get("old_text") or "").splitlines())
        new_n = len((tool_input.get("new_string") or tool_input.get("new_text") or "").splitlines())
        return f"{path}  +{new_n} -{old_n}"

    if tool_name in _READ_TOOLS or tn in {x.lower() for x in _READ_TOOLS}:
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        if path:
            s = str(path)
            return s[:max_len] + "…" if len(s) > max_len else s

    if tool_name in _FILE_TOOLS or tn in {x.lower() for x in _FILE_TOOLS}:
        path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("pattern") or ""
        if path:
            s = str(path)
            return s[:max_len] + "…" if len(s) > max_len else s

    if tool_name in _SEARCH_TOOLS or tn in {x.lower() for x in _SEARCH_TOOLS}:
        q = tool_input.get("query") or tool_input.get("url") or ""
        s = str(q)
        return s[:max_len] + "…" if len(s) > max_len else s

    if tool_name == "TaskCreate":
        subj = str(tool_input.get("subject", ""))
        return subj[:max_len] + "…" if len(subj) > max_len else subj

    if tool_name == "TaskUpdate":
        tid    = str(tool_input.get("taskId", ""))[:12]
        status = tool_input.get("status", "")
        subj   = tool_input.get("subject", "")
        if status:
            return f"{tid} → {status}"
        if subj:
            s = str(subj)
            return f"{tid} subject={s[:30] + '…' if len(s) > 30 else s}"
        fields = [k for k in tool_input if k != "taskId"]
        return f"{tid} [{', '.join(fields[:3])}]"

    if tool_name in ("TaskGet", "TaskList"):
        tid = str(tool_input.get("taskId", ""))
        return tid[:max_len] if tid else ""

    if tool_name == "Monitor":
        cmd   = str(tool_input.get("command", "")).strip()
        first = cmd.split("\n")[0]
        return first[:max_len] + "…" if len(first) > max_len else first

    parts = []
    for k, v in list(tool_input.items())[:2]:
        vs = str(v)
        if len(vs) > 30:
            vs = vs[:30] + "…"
        parts.append(f"{k}={vs}")
    result = ", ".join(parts)
    return result[:max_len] + "…" if len(result) > max_len else result


_STATUS_ICON = {
    "completed":  "[green]✓[/green]",
    "in_progress": "[yellow]▶[/yellow]",
    "pending":    "[dim]○[/dim]",
    "deleted":    "[red]✗[/red]",
}


def _try_task_output(raw: Any) -> str:
    """Return rich markup for known Task/Monitor result shapes, or '' to fall through."""
    import json as _j
    try:
        d = _j.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    if not isinstance(d, dict):
        return ""

    # TaskCreate: {"task": {"id": "...", "subject": "..."}}
    if "task" in d and isinstance(d.get("task"), dict) and "id" in d["task"]:
        t = d["task"]
        return (f"[green]✓[/green] created "
                f"[bold]{_escape(t.get('subject','')[:80])}[/bold]  "
                f"[dim]id={t['id'][:12]}[/dim]")

    # TaskUpdate: {"success": bool, "taskId": "...", "updatedFields": [...], "statusChange": {...}}
    if "success" in d and "taskId" in d and "updatedFields" in d:
        if not d.get("success"):
            return f"[red]✗[/red] {_escape(str(d.get('error', 'failed'))[:120])}"
        sc     = d.get("statusChange")
        fields = d.get("updatedFields", [])
        tid    = str(d["taskId"])[:12]
        parts  = [f"[green]✓[/green] [dim]{tid}[/dim]"]
        if sc:
            to_col = {"completed": "green", "in_progress": "yellow", "deleted": "red"}.get(sc.get("to",""), "dim")
            parts.append(f"[{to_col}]{_escape(sc.get('from',''))} → {_escape(sc.get('to',''))}[/{to_col}]")
        elif fields:
            parts.append(f"[dim]updated: {_escape(', '.join(fields[:4]))}[/dim]")
        return "  ".join(parts)

    # TaskList: {"tasks": [...]}
    if "tasks" in d and isinstance(d.get("tasks"), list):
        tasks = d["tasks"]
        if not tasks:
            return "[dim](no tasks)[/dim]"
        lines = []
        for t in tasks[:10]:
            icon = _STATUS_ICON.get(t.get("status", ""), "[dim]?[/dim]")
            subj = _escape(str(t.get("subject", ""))[:60])
            tid  = str(t.get("id", ""))[:8]
            lines.append(f"  {icon} [bold]{subj}[/bold]  [dim]{tid}[/dim]")
        if len(tasks) > 10:
            lines.append(f"  [dim]… +{len(tasks)-10} more[/dim]")
        return "\n".join(lines)

    # TaskGet: {"task": {...} | null}
    if "task" in d and not ("id" in d):  # distinguish from TaskCreate which also has "task"
        t = d["task"]
        if t is None:
            return "[dim](task not found)[/dim]"
        if isinstance(t, dict):
            icon   = _STATUS_ICON.get(t.get("status", ""), "[dim]?[/dim]")
            subj   = _escape(str(t.get("subject", ""))[:80])
            desc   = _escape(str(t.get("description", ""))[:120])
            blocks = t.get("blocks", [])
            bb     = t.get("blockedBy", [])
            parts  = [f"  {icon} [bold]{subj}[/bold]"]
            if desc:
                parts.append(f"  [dim]{desc}[/dim]")
            if blocks:
                parts.append(f"  [dim]blocks: {', '.join(str(x)[:8] for x in blocks[:3])}[/dim]")
            if bb:
                parts.append(f"  [dim]blocked by: {', '.join(str(x)[:8] for x in bb[:3])}[/dim]")
            return "\n".join(parts)

    # Monitor result: {"taskId": "...", "timeoutMs": int, "persistent": bool}
    if "taskId" in d and "timeoutMs" in d:
        tid     = str(d["taskId"])[:12]
        ms      = d.get("timeoutMs", 0)
        persist = d.get("persistent", False)
        timeout = "persistent" if persist else (f"{ms // 1000}s" if ms else "default")
        return f"[green]▶[/green] watching  [dim]id={tid}  timeout={timeout}[/dim]"

    return ""


def _format_output(raw: Any, expand: bool = False) -> str:
    if raw is None:
        return ""

    # Structured Task / Monitor result rendering
    structured = _try_task_output(raw)
    if structured:
        return structured

    import json as _json
    if isinstance(raw, (dict, list)):
        text = _json.dumps(raw, indent=2)
    else:
        text = str(raw)

    lines = text.splitlines()
    if not lines:
        return ""

    cap    = len(lines) if expand else _OUTPUT_MAX_LINES
    shown  = lines[:cap]
    hidden = len(lines) - cap

    parts = []
    for i, ln in enumerate(shown):
        if len(ln) > _OUTPUT_MAX_CHARS:
            ln = ln[:_OUTPUT_MAX_CHARS] + "…"
        prefix = "  └─ " if i == 0 else "     "
        parts.append(f"{prefix}{_escape(ln)}")

    if hidden > 0 and not expand:
        parts.append(f"  [dim]… +{hidden} lines  ▼ click to expand[/dim]")
    elif expand and hidden == 0 and len(lines) > _OUTPUT_MAX_LINES:
        parts.append(f"  [dim]▲ click to collapse[/dim]")

    return "\n".join(parts)


class ToolCallWidget(Widget):
    """Claude Code-style tool call: icon + name(args) header + tree-style output."""

    DEFAULT_CSS = """
    ToolCallWidget {
        margin: 0 0 0 0;
        padding: 0;
        height: auto;
        background: transparent;
    }
    ToolCallWidget #tool-header { height: auto; }
    ToolCallWidget #tool-output {
        margin: 0 0 1 0;
        color: $text-muted;
        height: auto;
    }
    """

    def __init__(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> None:
        super().__init__(id=tool_use_id)
        self.tool_name   = tool_name
        self.tool_input  = tool_input or {}
        self._state      = _STATE_RUNNING
        self._start_time = time.monotonic()
        self._raw_output: Any = None
        self._expand     = False

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), id="tool-header")
        yield Static("", id="tool-output")

    def _header_markup(self) -> str:
        name  = _escape(self.tool_name)
        args  = _escape(_args_inline(self.tool_name, self.tool_input))
        icon  = _tool_icon(self.tool_name)
        tn    = self.tool_name

        is_bash = tn in _BASH_TOOLS or tn.lower() in {x.lower() for x in _BASH_TOOLS}

        if is_bash:
            cmd   = str(self.tool_input.get("command", "")).strip()
            first = cmd.split("\n")[0] if cmd else ""
            disp  = (first[:80] + "…") if len(first) > 80 else first

            if self._state == _STATE_RUNNING:
                elapsed = time.monotonic() - self._start_time
                frame   = _BLOOM[int(elapsed * 8) % len(_BLOOM)]
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

        if self._state == _STATE_RUNNING:
            elapsed = time.monotonic() - self._start_time
            frame   = _BLOOM[int(elapsed * 8) % len(_BLOOM)]
            return f"[yellow]{frame}[/yellow] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"
        if self._state == _STATE_SUCCESS:
            return f"[green]✓[/green] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"
        if self._state == _STATE_ERROR:
            return f"[red]✗[/red] {icon} [bold]{name}[/bold]([dim]{args}[/dim])"

        return f"{icon} [bold]{name}[/bold]([dim]{args}[/dim])"

    def _refresh_header(self) -> None:
        try:
            self.query_one("#tool-header", Static).update(self._header_markup())
        except Exception:
            pass

    def _refresh_output(self) -> None:
        if self._is_bash() and self._raw_output is not None:
            is_err = self._state == _STATE_ERROR
            markup = self._bash_output_markup(str(self._raw_output), is_err, self._expand)
        else:
            markup = _format_output(self._raw_output, self._expand)
        try:
            self.query_one("#tool-output", Static).update(markup)
        except Exception:
            pass

    def tick_spinner(self) -> None:
        if self._state != _STATE_RUNNING:
            return
        self._refresh_header()

    def set_result(self, output: str, is_error: bool) -> None:
        if is_error:
            self.set_error(output)
        else:
            self.set_success(output)

    def _is_bash(self) -> bool:
        tn = self.tool_name
        return tn in _BASH_TOOLS or tn.lower() in {x.lower() for x in _BASH_TOOLS}

    def _bash_output_markup(self, text: str, is_error: bool, expand: bool = False) -> str:
        lines = text.splitlines()
        cap   = len(lines) if expand else _OUTPUT_MAX_LINES
        shown = lines[:cap]
        hidden = len(lines) - cap
        parts = []
        for ln in shown:
            if len(ln) > _OUTPUT_MAX_CHARS:
                ln = ln[:_OUTPUT_MAX_CHARS] + "…"
            if is_error:
                parts.append(f"  [bright_blue]│[/bright_blue] [red]{_escape(ln)}[/red]")
            else:
                parts.append(f"  [bright_blue]│[/bright_blue] {_escape(ln)}")
        if hidden > 0 and not expand:
            parts.append(f"  [dim]… +{hidden} more lines[/dim]")
        return "\n".join(parts)

    def set_success(self, output: Any) -> None:
        self._state      = _STATE_SUCCESS
        self._raw_output = output
        self._refresh_header()
        self._refresh_output()

    def set_error(self, output: Any) -> None:
        self._state      = _STATE_ERROR
        self._raw_output = output
        self._refresh_header()
        if self._is_bash():
            markup = self._bash_output_markup(str(output or "")[:500], is_error=True)
        else:
            text  = str(output)[:500] if output else ""
            lines = text.splitlines()[:_OUTPUT_MAX_LINES]
            parts = []
            for i, ln in enumerate(lines):
                if len(ln) > _OUTPUT_MAX_CHARS:
                    ln = ln[:_OUTPUT_MAX_CHARS] + "…"
                prefix = "  └─ " if i == 0 else "     "
                parts.append(f"[red]{prefix}{_escape(ln)}[/red]")
            markup = "\n".join(parts)
        try:
            self.query_one("#tool-output", Static).update(markup)
        except Exception:
            pass

    def on_click(self) -> None:
        if self._raw_output is not None:
            self._expand = not self._expand
            self._refresh_output()
            # Update expand hint in output static
            try:
                out = self.query_one("#tool-output", Static)
                current = out.renderable
                # hint appended by _refresh_output is not shown here —
                # the refresh already reflects the new state via _format_output
            except Exception:
                pass
