"""ClaudeTUIApp — professional chat TUI for rai claude-serve.

Streaming: AssistantMessage(Widget) = spinner → incremental Markdown.append().
User messages: UserMessage(Static) with escaped text, no Markdown parsing.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Static

from rai.client.claude import (
    ClaudeServeClient,
    ClaudeErrorEvent,
    ClaudeHITLRequestEvent,
    ClaudeRunEndEvent,
    ClaudeSessionIdEvent,
    ClaudeSubagentDoneEvent,
    ClaudeSubagentProgressEvent,
    ClaudeSubagentStartEvent,
    ClaudeThinkingEvent,
    ClaudeTokenEvent,
    ClaudeToolResultEvent,
    ClaudeToolStartEvent,
)
from rai.tui.claude_tui.screens.agents_screen import AgentsScreen
from rai.tui.claude_tui.screens.ask_user import AskUserScreen
from rai.tui.claude_tui.screens.context import ContextScreen
from rai.tui.claude_tui.screens.exit_plan_mode import ExitPlanModeScreen
from rai.tui.claude_tui.screens.file_hitl import EditHITLScreen, ReadHITLScreen, WriteHITLScreen
from rai.tui.claude_tui.screens.history import HistoryScreen
from rai.tui.claude_tui.screens.hitl import HITLScreen
from rai.tui.claude_tui.screens.mcp import MCPScreen
from rai.tui.claude_tui.screens.settings import SettingsScreen
from rai.tui.claude_tui.screens.skills import SkillsScreen
from rai.tui.claude_tui.widgets.agent_bar import AgentBar, AgentEntry
from rai.tui.claude_tui.widgets.agent_panel import AgentPanel
from rai.tui.claude_tui.widgets.assistant import AssistantMessage
from rai.tui.claude_tui.widgets.sidebar import SessionSidebar
from rai.tui.claude_tui.widgets.status_bar import StatusBar
from rai.tui.claude_tui.widgets.task_panel import TaskMonitorPanel
from rai.tui.claude_tui.widgets.subagent import SubagentRow
from rai.tui.claude_tui.widgets.thinking import ThinkingBlock
from rai.tui.claude_tui.widgets.tool_call import ToolCallWidget
from rai.tui.claude_tui.widgets.user_msg import UserMessage
from rai.tui.branding import BrandingConfig
from rai.tui.themes import ALL_THEMES

_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/resume",   "Resume a previous session"),
    ("/new",      "Start a new session"),
    ("/settings", "Open settings (theme, model, permissions)"),
    ("/theme",    "Theme picker"),
    ("/help",     "Show help"),
    ("/quit",     "Quit"),
    ("/costs",    "Show session cost breakdown"),
    ("/context",  "Show context window usage"),
    ("/compact",  "Compact context · /compact status for estimate"),
    ("/model",    "Show / override model"),
    ("/commands", "List all available capabilities"),
    ("/mcp",      "Show configured MCP servers"),
    ("/agents",   "List available agents"),
    ("/skills",   "List available skills"),
]


def _score(query: str, candidate: str, description: str = "") -> int:
    """Fuzzy score: higher = better match."""
    q = query.lower()
    c = candidate.lower()
    if not q:
        return 1
    if q == c:
        return 250
    if c.startswith(q):
        return 200
    if q in c:
        return 150
    boundary_score = 0
    parts = c.split("_") + c.split("-") + c.split(" ")
    initials = "".join(p[0] for p in parts if p)
    if q in initials:
        boundary_score = 110
    desc_score = 90 if q in description.lower() else 0
    seq_score = 0
    ci = 0
    for ch in q:
        idx = c.find(ch, ci)
        if idx == -1:
            seq_score = 0
            break
        seq_score += 10 + max(0, 70 - idx * 5)
        ci = idx + 1
    return max(boundary_score, desc_score, seq_score)

_RAI_ICON = (
    "[bold $accent]    ▲   ▲[/bold $accent]\n"
    "[bold $accent]  ╭───────╮[/bold $accent]\n"
    "[bold $accent]▝▜│ ██ ██ │▛▘  RAI * Your CyberSecurity AI Assistant[/bold $accent]\n"
    "[bold $accent]  ╰───────╯[/bold $accent]\n"
    "[bold $accent]    ▘▘ ▝▝  [/bold $accent]\n"
)


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("revolt-rai")
    except Exception:
        return "dev"


def _username() -> str:
    import os
    for key in ("USER", "USERNAME", "LOGNAME"):
        v = os.getenv(key, "")
        if v:
            return v.capitalize()
    return "there"


def _short_path(p: str, max_len: int = 42) -> str:
    import os
    home = os.path.expanduser("~")
    if p.startswith(home):
        p = "~" + p[len(home):]
    if len(p) > max_len:
        p = "…" + p[-(max_len - 1):]
    return p


def _escape_markup(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _build_banner(
    server: str = "",
    agent: str = "rai",
    recent: list[dict] | None = None,
    branding: "BrandingConfig | None" = None,
) -> str:
    """Build the Metasploit-style welcome banner markup."""
    import os
    import time
    from rai.tui.branding import BrandingConfig, RAI_BRANDING

    brand = branding or RAI_BRANDING
    if not brand.show_banner:
        return ""

    ver = _escape_markup(_version())
    user = _escape_markup(_username())
    cwd = _escape_markup(_short_path(os.getcwd()))
    srv = _escape_markup(server or "connecting…")
    ag = _escape_markup(agent)
    title = _escape_markup(brand.title)

    lines: list[str] = []
    lines.append(brand.icon)
    subtitle = f"  {_escape_markup(brand.subtitle)}" if brand.subtitle else "  [dim italic]claude-serve mode[/dim italic]"
    lines.append(f"[dim]v{ver}[/dim]{subtitle}")
    lines.append("")
    lines.append(f"[dim]+ -- --=[/dim]  [bold $accent]{title}[/bold $accent]  [dim]── ──+[/dim]")
    lines.append(f"[dim]+ -- --=[/dim]  [dim]agent  :[/dim]  [cyan]{ag}[/cyan]")
    lines.append(f"[dim]+ -- --=[/dim]  [dim]server :[/dim]  [dim]{srv}[/dim]")
    lines.append(f"[dim]+ -- --=[/dim]  [dim]cwd    :[/dim]  [dim]{cwd}[/dim]")

    for label, value in brand.info_rows:
        lbl = _escape_markup(label)
        val = _escape_markup(value)
        lines.append(f"[dim]+ -- --=[/dim]  [dim]{lbl:<7}:[/dim]  [dim]{val}[/dim]")

    lines.append(f"[dim]+ -- --=[/dim]  [bold]Welcome back {user}![/bold]")
    lines.append("")

    if recent:
        lines.append("[bold $warning]Recent sessions[/bold $warning]")
        for s in recent[:5]:
            title_s = s.get("custom_title") or s.get("first_prompt") or s.get("summary") or "untitled"
            if len(title_s) > 48:
                title_s = title_s[:48] + "…"
            ts = s.get("last_modified") or s.get("_ts")
            age = ""
            if ts:
                try:
                    diff = time.time() - float(ts)
                    if diff < 120:
                        age = "just now"
                    elif diff < 3600:
                        age = f"{int(diff // 60)}m ago"
                    elif diff < 86400:
                        age = f"{int(diff // 3600)}h ago"
                    else:
                        age = f"{int(diff // 86400)}d ago"
                except (TypeError, ValueError):
                    age = "recent"
            lines.append(f"  [dim]{age:>8}[/dim]  {_escape_markup(title_s)}")
        lines.append("  [dim]/resume for more[/dim]")
    else:
        lines.append("[dim]No recent sessions — start a conversation![/dim]")

    lines.append("")
    lines.append(brand.tips if brand.tips != RAI_BRANDING.tips else (
        "[dim]/resume[/dim] sessions  [dim]·[/dim]  [dim]/settings[/dim] config  "
        "[dim]·[/dim]  [dim]ctrl+h[/dim] sidebar  [dim]·[/dim]  [dim]ctrl+q[/dim] quit"
    ))
    return "\n".join(lines)

_HELP_MD = """\
**Commands:**
- `/resume` — pick a session to resume
- `/new` — start a new session
- `/settings` — model, theme, mode
- `/costs` — session cost & usage summary
- `/context` — context window usage breakdown
- `/mcp` — MCP server connection status
- `/agents` — available agent definitions
- `/skills` — available skills list
- `/compact [instructions]` — trigger context compaction (optional custom prompt)
- `/model [name]` — show or switch model
- `/commands` — show available skills, tools, and agents
- `/help` — show this help
- `/quit` — exit
"""


class SlashInput(Input):
    """Input with visible slash-command completion popup."""

    BINDINGS = [
        Binding("tab", "complete_or_accept", "Complete", show=False, priority=True),
        Binding("ctrl+a", "app_toggle_agents", "Agents", show=False, priority=True),
        Binding("up", "comp_up", "Up", show=False),
        Binding("down", "comp_down", "Down", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._completions: list[tuple[str, str]] = []
        self._comp_idx: int = 0

    @property
    def _popup(self) -> Static:
        return self.screen.query_one("#slash-popup", Static)

    def watch_value(self, value: str) -> None:
        self._show_completions(value)

    def _show_completions(self, value: str) -> None:
        if not value.startswith("/"):
            self._hide_completions()
            return
        query = value[1:]
        scored = [
            (cmd, desc, _score(query, cmd[1:], desc))
            for cmd, desc in _SLASH_COMMANDS
        ]
        filtered = [(cmd, desc) for cmd, desc, sc in scored if sc > 0]
        filtered.sort(key=lambda x: -_score(query, x[0][1:], x[1]))
        self._completions = filtered[:8]
        if not self._completions:
            self._hide_completions()
            return
        self._comp_idx = 0
        self._render_popup()
        try:
            self._popup.styles.display = "block"
        except Exception:
            pass

    def _hide_completions(self) -> None:
        self._completions = []
        self._comp_idx = 0
        try:
            self._popup.styles.display = "none"
        except Exception:
            pass

    def _render_popup(self) -> None:
        if not self._completions:
            return
        n = len(self._completions)
        idx = self._comp_idx % n
        lines: list[str] = []
        for i, (cmd, desc) in enumerate(self._completions):
            cmd_part = f"[bold][cyan]{cmd}[/cyan][/bold]"
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            row = f"{cmd_part}{desc_part}"
            if i == idx:
                row = f"[reverse] {row} [/reverse]"
            else:
                row = f" {row} "
            lines.append(row)
        try:
            self._popup.update("\n".join(lines))
        except Exception:
            pass

    def action_complete_or_accept(self) -> None:
        if self._completions:
            cmd = self._completions[self._comp_idx % len(self._completions)][0]
            self.value = cmd
            self.cursor_position = len(cmd)
            self._hide_completions()
        elif self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)

    def action_comp_up(self) -> None:
        if self._completions:
            self._comp_idx = (self._comp_idx - 1) % len(self._completions)
            self._render_popup()

    def action_comp_down(self) -> None:
        if self._completions:
            self._comp_idx = (self._comp_idx + 1) % len(self._completions)
            self._render_popup()

    def action_app_toggle_agents(self) -> None:
        self.app.action_toggle_agent_panel()


class ClaudeTUIApp(App):
    """Professional TUI for rai claude-serve."""

    TITLE = "RAI"
    AUTO_FOCUS = "SlashInput"

    CSS = """
    Screen {
        layers: sidebar tasks agents;
    }

    #banner {
        margin: 1 2;
        padding: 1 2;
        border: round $accent 40%;
    }

    #chat {
        padding: 1 2;
        scrollbar-size: 1 1;
        scrollbar-color: $primary 25%;
    }

    .session-divider {
        color: $primary 40%;
        text-align: center;
        margin: 0 0;
    }

    SlashInput {
        border: round $accent 45%;
        background: $panel;
        padding: 0 1;
        margin: 0 1 1 1;
    }

    SlashInput:focus {
        border: round $accent;
    }

    SlashInput.plan-mode {
        border: round yellow 70%;
        background: $surface 40%;
    }

    SlashInput.plan-mode:focus {
        border: round yellow;
    }

    .plan-entered-banner {
        margin: 0 0 0 2;
        height: auto;
    }

    #slash-popup {
        display: none;
        height: auto;
        max-height: 10;
        background: $surface 95%;
        border: round $accent 60%;
        margin: 0 1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+h", "toggle_sidebar", "History"),
        Binding("ctrl+t", "toggle_task_panel", "Tasks"),
        Binding("ctrl+a", "toggle_agent_panel", "Agents", priority=True),
        Binding("escape", "cancel_run", "Cancel", show=False, priority=True),
    ]

    show_sidebar    = reactive(False)
    show_task_panel = reactive(False)
    show_agent_panel = reactive(False)

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8001",
        *,
        api_key: str = "",
        session_id: str = "default",
        initial_theme: str = "github-dark",
        branding: "BrandingConfig | None" = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._base_url = base_url
        self._api_key = api_key
        self._session_id = session_id
        self._initial_theme = initial_theme
        self._branding = branding
        self._client: ClaudeServeClient | None = None
        self._current_response: AssistantMessage | None = None
        self._activity_bloom: AssistantMessage | None = None
        self._is_streaming = False
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._thinking_block: ThinkingBlock | None = None
        self._subagent_rows: dict[str, SubagentRow] = {}
        self._current_model: str = "claude-sonnet-4-6"
        self._permission_mode: str = "default"
        self._resume_id: str | None = None
        self._pending_task_creates: dict[str, str] = {}   # tool_use_id → subject
        self._pending_task_updates: dict[str, dict] = {}  # tool_use_id → TaskUpdate input
        self._agent_id_to_task: dict[str, str] = {}       # uuid/session_id → task_id
        self._session_cost: float = 0.0
        self._session_turns: int = 0
        self._session_duration_ms: int = 0

    def compose(self) -> ComposeResult:
        yield SessionSidebar()
        yield TaskMonitorPanel()
        yield AgentPanel()
        yield Header()
        with VerticalScroll(id="chat"):
            yield Static(_build_banner(self._base_url, branding=self._branding), id="banner")
        yield AgentBar()
        yield Static("", id="slash-popup")
        yield SlashInput(placeholder="Message RAI...")
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        for theme in ALL_THEMES:
            self.register_theme(theme)
        self.theme = self._initial_theme
        self.query_one("#chat", VerticalScroll).anchor()
        self._client = ClaudeServeClient(
            self._base_url, api_key=self._api_key
        )
        self.query_one(SessionSidebar)._client = self._client
        self.set_interval(1 / 8, self._tick_tool_spinners)
        self._load_banner_sessions()

    def _tick_tool_spinners(self) -> None:
        for w in self._tool_widgets.values():
            w.tick_spinner()

    @work
    async def _load_banner_sessions(self) -> None:
        """Fetch recent sessions and update the banner with activity."""
        if self._client is None:
            return
        try:
            result = await self._client.history.list(limit=5)
            sessions = result.get("sessions", [])
        except Exception:
            sessions = []
        try:
            banner = self.query_one("#banner", Static)
            banner.update(_build_banner(self._base_url, recent=sessions, branding=self._branding))
        except Exception:
            pass

    @on(Input.Submitted)
    async def handle_input(self, event: Input.Submitted) -> None:
        inp = self.query_one(SlashInput)
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/") and inp._completions:
            text = inp._completions[inp._comp_idx % len(inp._completions)][0]
        inp._hide_completions()
        event.input.clear()

        if text.startswith("/"):
            await self._dispatch_slash(text)
            return

        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(UserMessage(text))
        self._stream_turn(text)

    async def _dispatch_slash(self, cmd: str) -> None:
        parts = cmd.split(None, 1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        chat = self.query_one("#chat", VerticalScroll)

        if command == "/resume":
            self.push_screen(HistoryScreen(self._client), self._on_session_selected)
        elif command == "/new":
            self.action_new_session()
        elif command in ("/settings", "/theme"):
            self.push_screen(SettingsScreen(self._current_model, self.theme, self._permission_mode))
        elif command == "/help":
            resp = AssistantMessage()
            await chat.mount(resp)
            resp.set_content(_HELP_MD)
        elif command == "/quit":
            self.exit()
        elif command == "/costs":
            await self._cmd_costs(chat)
        elif command == "/context":
            self.push_screen(ContextScreen(self._client, self._session_id))
        elif command == "/mcp":
            self.push_screen(MCPScreen(self._client, self._session_id))
        elif command == "/agents":
            self.push_screen(AgentsScreen(self._client, self._session_id))
        elif command == "/skills":
            self.push_screen(SkillsScreen(self._client, self._session_id))
        elif command == "/compact":
            self._stream_turn(cmd)
        elif command == "/commands":
            await self._cmd_commands(chat)
        elif command == "/model":
            await self._cmd_model(chat, arg)
        else:
            self._stream_turn(cmd)

    async def _cmd_costs(self, chat: VerticalScroll) -> None:
        duration_s = self._session_duration_ms / 1000
        md = (
            f"**Session Cost**\n\n"
            f"- Total: **${self._session_cost:.4f}**\n"
            f"- Turns: {self._session_turns}\n"
            f"- API time: ~{duration_s:.1f}s\n"
        )
        resp = AssistantMessage()
        await chat.mount(resp)
        resp.set_content(md)

    async def _cmd_commands(self, chat: VerticalScroll) -> None:
        resp = AssistantMessage()
        await chat.mount(resp)
        try:
            info = await self._client.sessions.get_server_info(self._session_id)
        except Exception as exc:
            err = str(exc)
            if "404" in err:
                resp.set_content("Session not connected. Send a message to activate it first.")
            else:
                resp.set_content(f"Failed to fetch capabilities: {exc}")
            return

        lines = ["**Available Capabilities**\n"]

        commands = info.get("commands", []) or info.get("slashCommands", [])
        if commands:
            lines.append(f"**Slash Commands** ({len(commands)}):")
            for c in commands:
                name = c if isinstance(c, str) else c.get("name", "?")
                desc = "" if isinstance(c, str) else c.get("description", "")
                lines.append(f"- `/{name}`" + (f" — {desc}" if desc else ""))
            lines.append("")

        skills_data = info.get("skills", {})
        if isinstance(skills_data, dict):
            skill_list = skills_data.get("skillFrontmatter", [])
            if skill_list:
                lines.append(f"**Skills** ({len(skill_list)}):")
                for s in skill_list:
                    name = s.get("name", "?") if isinstance(s, dict) else s
                    lines.append(f"- `{name}`")
                lines.append("")
        elif skills_data:
            lines.append(f"**Skills** ({len(skills_data)}):")
            for s in skills_data:
                name = s if isinstance(s, str) else s.get("name", "?")
                lines.append(f"- `{name}`")
            lines.append("")

        agents_data = info.get("agents", [])
        if agents_data:
            lines.append(f"**Agents** ({len(agents_data)}):")
            for a in agents_data:
                if isinstance(a, dict):
                    name = a.get("name") or a.get("agentType") or a.get("type") or "?"
                else:
                    name = a
                lines.append(f"- `{name}`")
            lines.append("")

        if len(lines) == 1:
            lines.append("*No additional capabilities reported.*")

        resp.set_content("\n".join(lines))

    async def _cmd_model(self, chat: VerticalScroll, arg: str) -> None:
        resp = AssistantMessage()
        await chat.mount(resp)
        if not arg:
            resp.set_content(f"Current model: **{self._current_model}**")
            return
        try:
            await self._client.sessions.set_model(self._session_id, arg)
            self._current_model = arg
            resp.set_content(f"Model switched to **{arg}**")
        except Exception as exc:
            resp.set_content(f"Failed to switch model: {exc}")

    def _on_session_selected(self, session_id: str | None) -> None:
        if session_id is None:
            return
        self._session_id = session_id
        self._resume_id = session_id
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self.call_after_refresh(self._mount_resumed, session_id)

    async def _mount_resumed(self, session_id: str) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        short_id = session_id[:8] if len(session_id) > 8 else session_id
        await chat.mount(Static(f"[dim]─── resumed · {short_id} ───[/dim]", classes="session-divider"))
        self._load_history(session_id)

    @work
    async def _load_history(self, session_id: str) -> None:
        try:
            result = await self._client.history.messages(session_id, limit=50)
            messages = result.get("messages", [])
        except Exception:
            messages = []
        if not messages:
            return
        chat = self.app.query_one("#chat", VerticalScroll)

        # tool_use_id → ToolCallWidget for pairing tool_result blocks.
        # widget IDs use a random suffix so the same tool_use_id appearing in
        # multiple turns, or concurrent _load_history calls, never collides.
        pending_tools: dict[str, ToolCallWidget] = {}

        for msg in messages:
            mtype = msg.get("type", "")
            role  = msg.get("role", mtype)
            text  = msg.get("text", "").strip()

            if role == "user":
                # Always pair tool_results BEFORE the skip check — handles all cases,
                # including parallel tool calls where one user msg has N results
                for tr in msg.get("tool_results", []):
                    w = pending_tools.pop(tr.get("tool_use_id", ""), None)
                    if w:
                        w.set_result(tr.get("output", ""), tr.get("is_error", False))

                # Skip injected control messages and empty turns
                if not text or "<system-reminder>" in text:
                    continue
                if any(tag in text for tag in (
                    "<plan-harness-restricted>", "<plan-harness-blocked>",
                    "<task-harness-enforce>", "<task-harness-notify>",
                    "<system-info>",
                )):
                    continue
                if text.startswith("[Background agent") or text.strip().startswith("/compact"):
                    continue
                if "<task-notification>" in text:
                    # Render completed subagent notification as a done SubagentRow
                    _notif_agent = ""
                    _notif_status = "completed"
                    _notif_label = ""
                    for _line in text.splitlines():
                        if _line.startswith("Agent:"):
                            parts = _line.split("|")
                            _notif_agent = parts[0].replace("Agent:", "").strip()
                            if len(parts) > 2:
                                _notif_status = parts[2].replace("Status:", "").strip()
                            for p in parts:
                                if "(" in p and ")" in p:
                                    _notif_label = p[p.index("(")+1:p.index(")")]
                    _notif_desc = _notif_label or _notif_agent or "subagent"
                    _row = SubagentRow(uuid.uuid4().hex[:8], _notif_desc)
                    await chat.mount(_row)
                    _row.mark_done(_notif_status, _notif_agent, 0, auto_remove=False)
                    continue
                await chat.mount(UserMessage(text))

            elif role == "assistant":
                thinking = msg.get("thinking", "")
                if thinking:
                    tb = ThinkingBlock()
                    await chat.mount(tb)
                    tb.append(thinking)
                    tb.finalize()

                if text:
                    resp = AssistantMessage()
                    await chat.mount(resp)
                    resp.set_content(text)

                for i, tu in enumerate(msg.get("tool_uses", [])):
                    tuid   = tu.get("id", "")
                    tname  = tu.get("name", "")
                    tinput = tu.get("input", {})
                    if not tname:
                        continue
                    # Hide internal tools that have dedicated screens — same as live stream
                    if tname in ("EnterPlanMode", "ExitPlanMode", "AskUserQuestion", "Agent"):
                        continue
                    # Random suffix ensures uniqueness across turns and concurrent loads
                    widget_id = f"tw_{uuid.uuid4().hex[:8]}"
                    w = ToolCallWidget(widget_id, tname, tinput)
                    await chat.mount(w)
                    if tuid:
                        pending_tools[tuid] = w

        # Finalize any tool widgets that never received a result (session ended
        # mid-turn or tool result was missing from history)
        for w in pending_tools.values():
            w.set_result("", is_error=False)

    async def _get_or_mount_response(self) -> AssistantMessage:
        """Return the current AssistantMessage, mounting a new one if needed."""
        if self._current_response is None:
            chat = self.app.query_one("#chat", VerticalScroll)
            if not chat.is_attached:
                raise RuntimeError("chat widget not attached")
            resp = AssistantMessage()
            if self._activity_bloom and self._activity_bloom.is_attached:
                await chat.mount(resp, before=self._activity_bloom)
            else:
                await chat.mount(resp)
            self._current_response = resp
        return self._current_response

    @work(exclusive=True)
    async def _stream_turn(self, prompt: str) -> None:
        self._is_streaming = True
        self.query_one(StatusBar).is_streaming = True
        resume = self._resume_id
        self._resume_id = None
        _skip_xml = False

        chat = self.app.query_one("#chat", VerticalScroll)
        bloom = AssistantMessage()
        await chat.mount(bloom)
        self._activity_bloom = bloom

        try:
            async for ev in self._client.chat.stream(
                prompt, session_id=self._session_id, resume=resume
            ):
                if isinstance(ev, ClaudeSessionIdEvent):
                    # Never overwrite self._session_id from the SDK event.
                    # The server registers sessions under req.session_id (whatever
                    # the TUI sent), not the SDK UUID.  Updating here would make
                    # the next message look for a key the server never registered
                    # → _create_session fires again → subprocess conflict → 500.
                    # HITL resolves use ev.session_id directly, so the UUID is
                    # still available when needed.
                    pass

                elif isinstance(ev, ClaudeThinkingEvent):
                    if self._thinking_block is None:
                        if self._current_response and not self._current_response._buf:
                            self._current_response.remove()
                            self._current_response = None
                        if self._activity_bloom and self._activity_bloom.is_attached:
                            self._activity_bloom.display = True
                        self._thinking_block = ThinkingBlock()
                        chat = self.app.query_one("#chat", VerticalScroll)
                        if self._activity_bloom and self._activity_bloom.is_attached:
                            await chat.mount(self._thinking_block, before=self._activity_bloom)
                        else:
                            await chat.mount(self._thinking_block)
                    self._thinking_block.append(ev.text)
                    await asyncio.sleep(0)

                elif isinstance(ev, ClaudeTokenEvent):
                    if not self._is_streaming:
                        self._is_streaming = True
                        self.query_one(StatusBar).is_streaming = True
                    # Filter harness XML blocks from user-visible output
                    _HARNESS_OPEN_TAGS = (
                        "<task-notification>", "<system-reminder>",
                        "<plan-harness-restricted>", "<plan-harness-blocked>",
                        "<task-harness-enforce>", "<task-harness-notify>",
                        "<system-info>",
                    )
                    _HARNESS_CLOSE_TAGS = (
                        "</task-notification>", "</system-reminder>",
                        "</plan-harness-restricted>", "</plan-harness-blocked>",
                        "</task-harness-enforce>", "</task-harness-notify>",
                        "</system-info>",
                    )
                    if any(t in ev.text for t in _HARNESS_OPEN_TAGS):
                        _skip_xml = True
                        continue
                    if _skip_xml:
                        if any(t in ev.text for t in _HARNESS_CLOSE_TAGS):
                            _skip_xml = False
                        continue
                    if self._thinking_block is not None:
                        self._thinking_block.finalize()
                        self._thinking_block = None
                    if self._activity_bloom and self._activity_bloom.is_attached:
                        self._activity_bloom.display = False
                    response = await self._get_or_mount_response()
                    response.append_token(ev.text)
                    await asyncio.sleep(0)

                elif isinstance(ev, ClaudeHITLRequestEvent):
                    # Mark subagent as HITL-pending in bar/feed
                    _hitl_task_id = ""
                    if ev.agent_id:
                        _hitl_task_id = self._agent_id_to_task.get(ev.agent_id, "")
                        if _hitl_task_id:
                            self.query_one(AgentBar).set_hitl(_hitl_task_id, True)
                            feed = self.query_one(AgentPanel).get_feed(_hitl_task_id)
                            if feed:
                                feed.set_hitl_indicator(True)

                    if ev.tool_name == "ExitPlanMode":
                        result = await self.app.push_screen_wait(ExitPlanModeScreen(ev))
                        sid = ev.session_id or self._session_id
                        if result == "approve":
                            await self._client.sessions.resolve_permission(sid, ev.tool_use_id, "allow")
                            self._clear_plan_mode()
                        elif result == "reject":
                            await self._client.sessions.resolve_permission(sid, ev.tool_use_id, "deny")
                            self._clear_plan_mode()
                        elif isinstance(result, tuple) and result[0] == "feedback":
                            await self._client.sessions.resolve_plan_feedback(sid, ev.tool_use_id, result[1])

                    elif ev.tool_name == "AskUserQuestion":
                        questions = ev.input.get("questions", [])
                        answers = await self.app.push_screen_wait(AskUserScreen(questions))
                        if answers is None:
                            await self._client.sessions.resolve_permission(
                                ev.session_id or self._session_id,
                                ev.tool_use_id,
                                "deny",
                            )
                        else:
                            await self._client.sessions.resolve_ask_user(
                                ev.session_id or self._session_id,
                                ev.tool_use_id,
                                answers,
                            )
                    elif ev.tool_name == "Write":
                        decision = await self.app.push_screen_wait(WriteHITLScreen(ev))
                        await self._client.sessions.resolve_permission(
                            ev.session_id or self._session_id,
                            ev.tool_use_id,
                            decision,
                        )
                    elif ev.tool_name == "Edit":
                        decision = await self.app.push_screen_wait(EditHITLScreen(ev))
                        await self._client.sessions.resolve_permission(
                            ev.session_id or self._session_id,
                            ev.tool_use_id,
                            decision,
                        )
                    elif ev.tool_name == "Read":
                        decision = await self.app.push_screen_wait(ReadHITLScreen(ev))
                        await self._client.sessions.resolve_permission(
                            ev.session_id or self._session_id,
                            ev.tool_use_id,
                            decision,
                        )
                    else:
                        decision = await self.app.push_screen_wait(HITLScreen(ev))
                        await self._client.sessions.resolve_permission(
                            ev.session_id or self._session_id,
                            ev.tool_use_id,
                            decision,
                        )

                    # Clear HITL indicator after resolution
                    if _hitl_task_id:
                        self.query_one(AgentBar).set_hitl(_hitl_task_id, False)
                        feed = self.query_one(AgentPanel).get_feed(_hitl_task_id)
                        if feed:
                            feed.set_hitl_indicator(False)

                elif isinstance(ev, ClaudeToolStartEvent):
                    # Finalize current response segment before mounting tool widget
                    if self._current_response is not None:
                        if self._current_response._buf:
                            self._current_response.finalize()
                        else:
                            self._current_response.remove()
                        self._current_response = None
                    # Re-show activity bloom (was hidden during token streaming)
                    if self._activity_bloom and self._activity_bloom.is_attached:
                        self._activity_bloom.display = True
                    # Plan mode tools: update status bar + input style, never mount as ToolCallWidget
                    if ev.name == "EnterPlanMode":
                        self.query_one(StatusBar).plan_mode = True
                        inp = self.query_one(SlashInput)
                        inp.add_class("plan-mode")
                        inp.placeholder = "◈ Plan mode — type to send guidance…"
                        banner = Static(
                            "[bold $warning]◈[/bold $warning] [bold]RAI[/bold]  "
                            "[dim]Entering Plan Mode[/dim]",
                            classes="plan-entered-banner",
                        )
                        chat = self.app.query_one("#chat", VerticalScroll)
                        if self._activity_bloom and self._activity_bloom.is_attached:
                            await chat.mount(banner, before=self._activity_bloom)
                        else:
                            await chat.mount(banner)
                        continue
                    if ev.name == "ExitPlanMode":
                        continue
                    # AskUserQuestion has its own modal screen — suppress ToolCallWidget
                    if ev.name == "AskUserQuestion":
                        continue
                    # Agent tool has dedicated SubagentRow + AgentPanel — suppress
                    if ev.name == "Agent":
                        continue
                    widget = ToolCallWidget(ev.id, ev.name, ev.input)
                    self._tool_widgets[ev.id] = widget
                    chat = self.app.query_one("#chat", VerticalScroll)
                    if self._activity_bloom and self._activity_bloom.is_attached:
                        await chat.mount(widget, before=self._activity_bloom)
                    else:
                        await chat.mount(widget)
                    # Task panel — additive, does not replace chat widget
                    if ev.name == "TaskCreate":
                        subj = ev.input.get("subject", "")
                        self._pending_task_creates[ev.id] = subj
                        self.query_one(TaskMonitorPanel).add_task(ev.id, subj)
                        if not self.show_task_panel:
                            self.show_task_panel = True
                    elif ev.name == "TaskUpdate":
                        self._pending_task_updates[ev.id] = ev.input
                        # Update panel immediately on tool_start — don't wait for tool_result
                        status = ev.input.get("status", "")
                        if status:
                            self.query_one(TaskMonitorPanel).update_task(
                                ev.input.get("taskId", ""), status, ev.input.get("subject", "")
                            )

                elif isinstance(ev, ClaudeToolResultEvent):
                    w = self._tool_widgets.pop(ev.id, None)
                    if w:
                        w.set_result(ev.output, ev.is_error)
                    # Next token will open a fresh AssistantMessage below the tool
                    # Task panel resolution
                    if ev.id in self._pending_task_creates:
                        subj = self._pending_task_creates.pop(ev.id)
                        # SDK returns plain text: "Task #19 created successfully: ..."
                        # Try JSON first (future-proof), then regex on text
                        real_id = ""
                        try:
                            import json as _j
                            real_id = str(_j.loads(ev.output).get("task", {}).get("id", ""))
                        except Exception:
                            pass
                        if not real_id:
                            import re as _re
                            m = _re.search(r'[Tt]ask\s*#?(\d+)', ev.output)
                            if m:
                                real_id = m.group(1)
                        self.query_one(TaskMonitorPanel).confirm_task(ev.id, real_id, subj)
                    elif ev.id in self._pending_task_updates:
                        upd = self._pending_task_updates.pop(ev.id)
                        # Use status from the input we already have — output is plain text
                        status = upd.get("status", "")
                        if not status:
                            try:
                                import json as _j
                                sc     = _j.loads(ev.output).get("statusChange") or {}
                                status = sc.get("to", "")
                            except Exception:
                                pass
                        if status:
                            self.query_one(TaskMonitorPanel).update_task(
                                upd.get("taskId", ""), status, upd.get("subject", "")
                            )

                elif isinstance(ev, ClaudeSubagentStartEvent):
                    row = SubagentRow(ev.task_id, ev.description, ev.task_type)
                    self._subagent_rows[ev.task_id] = row
                    chat = self.app.query_one("#chat", VerticalScroll)
                    if self._activity_bloom and self._activity_bloom.is_attached:
                        await chat.mount(row, before=self._activity_bloom)
                    else:
                        await chat.mount(row)
                    # Agent bar + panel
                    entry = AgentEntry(
                        task_id=ev.task_id,
                        description=ev.description,
                        task_type=ev.task_type or "",
                    )
                    self.query_one(AgentBar).add_agent(entry)
                    self.query_one(AgentPanel).add_feed(ev.task_id, ev.description, ev.task_type or "")
                    # Auto-show panel when first agent spawns
                    if not self.show_agent_panel:
                        self.show_agent_panel = True
                    self._update_agent_count()
                    if ev.uuid:
                        self._agent_id_to_task[ev.uuid] = ev.task_id
                    if ev.session_id:
                        self._agent_id_to_task[ev.session_id] = ev.task_id
                    # Mirror Monitor watches to the task panel
                    if ev.task_type == "local_bash":
                        self.query_one(TaskMonitorPanel).add_monitor(ev.task_id, ev.description)
                        if not self.show_task_panel:
                            self.show_task_panel = True

                elif isinstance(ev, ClaudeSubagentProgressEvent):
                    row = self._subagent_rows.get(ev.task_id)
                    if row:
                        row.update_progress(ev.last_tool, ev.tokens)
                    # Agent bar + feed
                    self.query_one(AgentBar).update_agent(
                        ev.task_id, ev.last_tool, ev.tokens, ev.duration_ms
                    )
                    feed = self.query_one(AgentPanel).get_feed(ev.task_id)
                    if feed:
                        feed.add_step(ev.description, ev.last_tool, ev.tokens, ev.duration_ms)
                    self.query_one(TaskMonitorPanel).update_monitor(
                        ev.task_id, ev.description, ev.last_tool, ev.tokens
                    )

                elif isinstance(ev, ClaudeSubagentDoneEvent):
                    row = self._subagent_rows.pop(ev.task_id, None)
                    if row:
                        row.mark_done(ev.status, ev.summary, ev.tokens)
                    # Agent bar + feed + panel row
                    self.query_one(AgentBar).done_agent(ev.task_id, ev.status)
                    panel = self.query_one(AgentPanel)
                    panel.update_agent_status(ev.task_id, ev.status, ev.summary)
                    feed = panel.get_feed(ev.task_id)
                    if feed:
                        feed.mark_done(ev.status, ev.summary)
                    self.query_one(TaskMonitorPanel).done_monitor(
                        ev.task_id, ev.status, ev.summary, ev.output_file
                    )
                    self._update_agent_count()

                elif isinstance(ev, ClaudeRunEndEvent):
                    # Finalize current response but DON'T break — the stream
                    # stays open for background subagent notifications.
                    if self._thinking_block is not None:
                        self._thinking_block.finalize()
                        self._thinking_block = None
                    if self._current_response is not None:
                        if self._current_response._buf:
                            self._current_response.finalize()
                        else:
                            self._current_response.remove()
                        self._current_response = None
                    # Remove activity bloom — run is done
                    if self._activity_bloom is not None:
                        if self._activity_bloom.is_attached:
                            self._activity_bloom.remove()
                        self._activity_bloom = None
                    # Accumulate cost/usage
                    self._session_cost += ev.cost or 0.0
                    self._session_turns = ev.turns
                    self._session_duration_ms += ev.duration_ms or 0
                    status = self.query_one(StatusBar)
                    status.cost = self._session_cost
                    # Only mark streaming done if no subagents are still running.
                    # When subagents are active, the stream stays open for
                    # notifications — keep streaming indicator visible.
                    if not self._subagent_rows:
                        self._is_streaming = False
                        status.is_streaming = False
                    self._fetch_context_pct()
                    try:
                        self.app.query_one("#chat", VerticalScroll).scroll_end(animate=False)
                    except Exception:
                        pass

                elif isinstance(ev, ClaudeErrorEvent):
                    response = await self._get_or_mount_response()
                    response.append_token(f"\n\n**Error:** {ev.message}")
                    break

        except asyncio.CancelledError:
            try:
                response = await self._get_or_mount_response()
                response.append_token("\n\n*[cancelled]*")
            except Exception:
                pass
        except Exception as exc:
            try:
                response = await self._get_or_mount_response()
                response.append_token(f"\n\n**Error:** {exc}")
            except Exception:
                pass
        finally:
            if self._thinking_block is not None:
                self._thinking_block.finalize()
                self._thinking_block = None
            if self._current_response is not None:
                if self._current_response._buf:
                    self._current_response.finalize()
                else:
                    self._current_response.remove()
                self._current_response = None
            if self._activity_bloom is not None:
                if self._activity_bloom.is_attached:
                    self._activity_bloom.remove()
                self._activity_bloom = None
            self._is_streaming = False
            self.query_one(StatusBar).is_streaming = False
            try:
                self.app.query_one("#chat", VerticalScroll).scroll_end(animate=False)
            except Exception:
                pass

    def action_new_session(self) -> None:
        self._session_id = "default"
        self._resume_id = None
        self._tool_widgets.clear()
        self._thinking_block = None
        self._activity_bloom = None
        self._current_response = None
        self._subagent_rows.clear()
        self._pending_task_creates.clear()
        self._pending_task_updates.clear()
        self._agent_id_to_task.clear()
        self._session_cost = 0.0
        self._session_turns = 0
        self._session_duration_ms = 0
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self.query_one(TaskMonitorPanel).clear()
        self.query_one(AgentBar).remove_all()
        self.query_one(AgentPanel).clear_all()
        self.show_task_panel = False
        self.show_agent_panel = False
        status = self.query_one(StatusBar)
        status.plan_mode = False
        status.agent_count = 0
        status.is_streaming = False
        status.cost = 0.0
        status.context_pct = 0.0
        inp = self.query_one(SlashInput)
        inp.remove_class("plan-mode")
        inp.placeholder = "Message RAI..."
        self.call_after_refresh(self._mount_welcome)

    async def _mount_welcome(self) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(Static(_build_banner(self._base_url, branding=self._branding), id="banner"))
        self._load_banner_sessions()

    def _update_agent_count(self) -> None:
        bar = self.query_one(AgentBar)
        running = sum(1 for e in bar._agents.values() if e.status == "running")
        self.query_one(StatusBar).agent_count = running

    def _clear_plan_mode(self) -> None:
        """Remove plan mode visual indicators (status bar + input styling)."""
        self.query_one(StatusBar).plan_mode = False
        inp = self.query_one(SlashInput)
        inp.remove_class("plan-mode")
        inp.placeholder = "Message RAI..."

    @work(exclusive=True, group="ctx_fetch")
    async def _fetch_context_pct(self) -> None:
        try:
            data = await self._client.sessions.get_context(self._session_id)
            pct = data.get("percentage", 0.0)
            self.app.query_one(StatusBar).context_pct = pct
        except Exception:
            pass

    def action_cancel_run(self) -> None:
        if self._is_streaming:
            for w in self.workers:
                w.cancel()

    def action_toggle_sidebar(self) -> None:
        self.show_sidebar = not self.show_sidebar

    def watch_show_sidebar(self, show_sidebar: bool) -> None:
        sidebar = self.query_one(SessionSidebar)
        sidebar.set_class(show_sidebar, "-visible")
        if show_sidebar:
            sidebar.load_sessions()

    def action_toggle_task_panel(self) -> None:
        self.show_task_panel = not self.show_task_panel

    def watch_show_task_panel(self, show: bool) -> None:
        self.query_one(TaskMonitorPanel).set_class(show, "-visible")

    def action_toggle_agent_panel(self) -> None:
        self.show_agent_panel = not self.show_agent_panel

    def watch_show_agent_panel(self, show: bool) -> None:
        self.query_one(AgentPanel).set_class(show, "-visible")

    @on(AgentPanel.Closed)
    def _on_agent_panel_closed(self, event: AgentPanel.Closed) -> None:
        self.show_agent_panel = False

    @on(AgentBar.AgentSelected)
    def _on_agent_selected(self, event: AgentBar.AgentSelected) -> None:
        panel = self.query_one(AgentPanel)
        panel.show_agent(event.task_id)
        self.show_agent_panel = True

    @on(AgentBar.AgentStopped)
    def _on_agent_stopped(self, event: AgentBar.AgentStopped) -> None:
        self._cancel_subagent(event.task_id)

    @on(SubagentRow.Selected)
    def _on_subagent_row_selected(self, event: SubagentRow.Selected) -> None:
        panel = self.query_one(AgentPanel)
        panel.show_agent(event.task_id)
        self.show_agent_panel = True

    @work
    async def _cancel_subagent(self, task_id: str) -> None:
        try:
            await self._client.sessions.interrupt(self._session_id)
        except Exception:
            pass

    def action_switch_session(self, session_id: str) -> None:
        self._session_id = session_id
        self._resume_id = session_id
        self._tool_widgets.clear()
        self._thinking_block = None
        self._subagent_rows.clear()
        self._pending_task_creates.clear()
        self._pending_task_updates.clear()
        self._agent_id_to_task.clear()
        self._session_cost = 0.0
        self._session_turns = 0
        self._session_duration_ms = 0
        chat = self.query_one("#chat", VerticalScroll)
        chat.remove_children()
        self.query_one(TaskMonitorPanel).clear()
        self.query_one(AgentBar).remove_all()
        self.query_one(AgentPanel).clear_all()
        self.show_task_panel = False
        self.show_agent_panel = False
        status = self.query_one(StatusBar)
        status.plan_mode = False
        status.agent_count = 0
        status.is_streaming = False
        status.cost = 0.0
        status.context_pct = 0.0
        inp = self.query_one(SlashInput)
        inp.remove_class("plan-mode")
        inp.placeholder = "Message RAI..."
        self.call_after_refresh(self._mount_resumed, session_id)
        self.show_sidebar = False

    async def on_unmount(self) -> None:
        if self._client:
            await self._client.aclose()
