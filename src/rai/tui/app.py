"""RaiHttpTUIApp — standalone Textual TUI connected to the RAI HTTP harness."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import Footer, Static

from rai.tui.themes import ALL_THEMES

from rai.tui.runner import (
    AppendThinking,
    AppendToken,
    AskUserRequired,
    HITLAutoApproved,
    HITLRequired,
    HITLResolved,
    NotificationArrived,
    PermissionDenied,
    PipelineUpdate,
    PlanCompleted,
    PlanDone,
    PlanModeEntered,
    PlanReady,
    RateLimited,
    RunCompleted,
    RunError,
    SessionApproved,
    SSEEventPump,
    StepBlocked,
    StepComplete,
    StepStarted,
    SubagentAdded,
    SubagentDone,
    SubagentHITL,
    SubagentHITLAutoApproved,
    SubagentThinking,
    SubagentToken,
    SubagentToolFinished,
    SubagentToolStarted,
    ToolFinished,
    ToolStarted,
)
from rai.tui.screens.mcp_viewer import MCPViewerScreen
from rai.tui.screens.model_picker import ModelPickerScreen
from rai.tui.screens.run_detail import RunDetailScreen
from rai.tui.screens.runs_browser import RunsBrowserScreen
from rai.tui.screens.theme_picker import ThemePickerScreen
from rai.tui.screens.thread_browser import ThreadBrowserScreen
from rai.tui.widgets import (
    AskUserPanel,
    AssistantMsg,
    BackgroundRunsPanel,
    CompactMsg,
    CompactWarningMsg,
    FindingsPanel,
    HeaderBar,
    HITLPanel,
    HistoryDivider,
    InputBar,
    PlanCompletedMsg,
    PlanModeEnteredMsg,
    StepBlockedMsg,
    StepCompleteMsg,
    StepStartMsg,
    PlanPanel,
    StatusBar,
    SubagentGroup,
    SubagentTreePanel,
    ThinkingMsg,
    ToolCallMsg,
    UserMsg,
    WelcomeBanner,
    WizardStepWidget,
)

# Plan harness execution tools — suppressed from generic ToolCallMsg rendering.
# Visual feedback for these comes from SSE step events (StepStartMsg, etc.) and
# the PlanModeEnteredMsg banner, not from tool call cards.
_PLAN_EXEC_TOOLS = frozenset({
    "enter_plan_mode", "enter_step", "mark_step_done",
    "mark_step_blocked", "exit_plan_mode", "list_plan_steps",
})
from rai.tui.widgets.hitl_panel import HITLPanel as HITLPanelWidget
from rai.tui.widgets.messages import _args_inline, _escape

_SHOW_BANNER = True  # unused module constant — banner logic is in RaiHttpTUIApp._show_banner


class RaiHttpTUIApp(App):
    """Standalone HTTP-connected TUI — zero DeepAgents inheritance."""

    CSS_PATH = "app.tcss"
    TITLE = "RAI"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("escape", "cancel_run", "↺ Cancel", show=False, priority=True),
        Binding("ctrl+b", "background_run", "Background", show=True),
        Binding("ctrl+o", "open_runs", "Runs", show=True),
        Binding("ctrl+n", "new_thread", "New Thread", show=True),
        Binding("ctrl+r", "resume_thread", "Resume", show=True),
        Binding("ctrl+t", "pick_theme", "Theme", show=True),
        Binding("ctrl+a", "toggle_auto_approve", "Auto-Approve", show=True),
        Binding("ctrl+k", "clear_messages", "Clear", show=True),
        Binding("ctrl+p", "toggle_plan", "Plan", show=True),
        Binding("ctrl+l", "toggle_subagents", "Subagents", show=False),
        Binding("ctrl+x", "open_editor", "Editor", show=False),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        agent: str = "rai",
        api_key: str = "",
        thread_id: str | None = None,
        banner: str | None = None,
        default_theme: str = "github-dark",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._base_url = base_url
        self._agent = agent
        self._api_key = api_key
        self._initial_thread_id = thread_id
        # banner=None  → show default RAI banner
        # banner=""    → suppress banner entirely
        # banner="…"   → show custom markup banner
        self._show_banner   = banner is None or bool(banner)
        self._banner_markup = banner if banner else None
        self._default_theme = default_theme

        self._client: Any = None
        self._active_run_id: str | None = None
        self._active_thread_id: str | None = thread_id
        self._pump_tasks: dict[str, asyncio.Task] = {}
        self._bg_run_ids: set[str] = set()
        self._auto_approve = False

        self._tool_widgets: dict[str, ToolCallMsg] = {}
        self._subagent_groups: dict[str, SubagentGroup] = {}
        self._subagent_run_ids: dict[str, str] = {}   # task_id → run_id for server cancel
        self._last_assistant: AssistantMsg | None = None
        self._current_thinking: ThinkingMsg | None = None
        self._scroll_pending = False
        self._current_run_prompt: str = ""
        self._welcome_banner: WelcomeBanner | None = None
        self._detail_screen: RunDetailScreen | None = None
        self._detail_run_id: str | None = None
        self._discovered_skills: list[dict] = []
        self._model_override: str | None = None
        self._mcp_server_infos: list = []
        self._compact_warning_shown: bool = False
        self._pre_run_msg_count: int = 0
        self._agent_wizard: dict | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with VerticalScroll(id="chat"):
            yield Container(id="messages")
        yield SubagentTreePanel(id="subagent-tree")
        yield PlanPanel(id="plan-panel")
        yield HITLPanelWidget(id="hitl-panel")
        yield AskUserPanel(id="ask-user-panel")
        yield FindingsPanel(id="findings-panel")
        yield BackgroundRunsPanel(id="bg-runs-panel")
        yield InputBar(id="input-bar")
        yield StatusBar(id="status-bar")
        yield Footer()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        # Register all themes and apply default
        for _t in ALL_THEMES:
            try:
                self.register_theme(_t)
            except Exception:
                pass
        try:
            self.theme = self._default_theme
        except Exception:
            pass

        from rai.client import RAIClient

        self._client = RAIClient(base_url=self._base_url, api_key=self._api_key)

        header = self.query_one("#header", HeaderBar)
        header.agent = self._agent
        header.server = self._base_url

        try:
            await self._client.system.health()
            header.server_ok = True
        except Exception:
            header.server_ok = False

        if self._show_banner:
            wb = WelcomeBanner(self._agent, self._base_url, custom_markup=self._banner_markup)
            self._welcome_banner = wb
            container = self.query_one("#messages", Container)
            await container.mount(wb)
            self.run_worker(self._fetch_recent_threads(wb), exclusive=False)

        if self._initial_thread_id:
            await self._load_thread_history(self._initial_thread_id)

        self.set_interval(0.1, self._tick_animations)
        self.run_worker(self._fetch_branch(), exclusive=False)
        self.run_worker(self._discover_skills(), exclusive=False, name="skill_discovery")
        self.run_worker(self._discover_mcp(), exclusive=False, name="mcp_discovery")
        self.run_worker(self._check_for_update(), exclusive=False, name="update_check")

    async def _fetch_recent_threads(self, wb: WelcomeBanner) -> None:
        try:
            threads_resp = await self._client.threads.list(agent=self._agent, limit=8)
            threads = []
            for t in (threads_resp or []):
                d = t.__dict__ if hasattr(t, "__dict__") else dict(t)
                threads.append(d)

            async def _get_prompt(t: dict) -> dict:
                try:
                    tid = t.get("thread_id", "")
                    h = await self._client.threads.history(tid, limit=3)
                    for m in h.get("messages", []):
                        if m.get("type") == "human" and m.get("content"):
                            t["initial_prompt"] = str(m["content"])[:80]
                            return t
                except Exception:
                    pass
                return t

            results = await asyncio.gather(*[_get_prompt(t) for t in threads])
            wb.set_recent_threads(list(results))
        except Exception:
            pass

    async def _check_for_update(self) -> None:
        try:
            from rai.update import is_update_available
            from rai import __version__
            available, latest = await asyncio.to_thread(is_update_available)
            if available and latest:
                self.notify(
                    f"v{latest} is available (you have v{__version__}). Run [bold]rai update[/bold] to upgrade.",
                    title="Update available",
                    timeout=12,
                    severity="warning",
                )
        except Exception:
            pass

    async def _fetch_branch(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
            branch = out.decode().strip()
            if branch:
                self.query_one("#status-bar", StatusBar).branch = branch
        except Exception:
            pass

    async def _load_thread_history(self, thread_id: str) -> None:
        # Bind findings store to this thread so resuming a session restores its findings
        try:
            from rai.tools.core.findings import init_findings_store
            init_findings_store(thread_id)
        except Exception:
            pass
        try:
            history = await self._client.threads.history(thread_id, limit=500)
            messages = history.get("messages", [])
            total = history.get("total", len(messages))
            container = self.query_one("#messages", Container)

            # Map tool_call_id → ToolCallMsg so ToolMessages can pair with their call
            pending: dict[str, ToolCallMsg] = {}

            _SKIP_TOOL_NAMES = _PLAN_EXEC_TOOLS | {"ask_user", "compact_conversation"}

            for msg in messages:
                msg_type = msg.get("type", "")
                content = msg.get("content", "")

                if msg_type == "system":
                    # System prompts / injected context — never render in chat
                    continue

                if msg_type == "human":
                    c = str(content)
                    # Skip: background-agent watcher injections and /compact invocations
                    if not c or c.startswith("[Background agent") or c.strip() == "/compact":
                        continue
                    await container.mount(UserMsg(c))

                elif msg_type == "ai":
                    if content and content.strip():
                        am = AssistantMsg()
                        await container.mount(am)
                        am.append_text(str(content))
                        am.set_final()
                    for tc in msg.get("tool_calls", []):
                        tc_name = tc.get("name", "")
                        if tc_name == "enter_plan_mode":
                            await container.mount(PlanModeEnteredMsg())
                            continue
                        if tc_name in _SKIP_TOOL_NAMES:
                            continue
                        w = ToolCallMsg(
                            tc_name,
                            tc.get("args", {}),
                            tool_use_id=tc.get("id", ""),
                        )
                        await container.mount(w)
                        if tc.get("id"):
                            pending[tc["id"]] = w

                elif msg_type == "tool":
                    tool_call_id = msg.get("tool_call_id", "")
                    tool_name = msg.get("name", "tool")
                    if tool_name in _SKIP_TOOL_NAMES:
                        continue
                    if tool_call_id and tool_call_id in pending:
                        pending[tool_call_id].set_success(content or "")
                    elif content:
                        w = ToolCallMsg(tool_name, {}, tool_use_id=tool_call_id)
                        await container.mount(w)
                        w.set_success(content)

            # Footer divider after messages (like deepagents), with truncation notice
            await container.mount(HistoryDivider(len(messages), thread_id, total=total))

            self._active_thread_id = thread_id
            self.query_one("#header", HeaderBar).thread_id = thread_id
            self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
        except Exception as exc:
            self._notify_error(f"Failed to load thread history: {exc}")

    # ------------------------------------------------------------------
    # Animation tick
    # ------------------------------------------------------------------

    def _tick_animations(self) -> None:
        # Tool and subagent spinners
        for w in self._tool_widgets.values():
            w.tick_spinner()
        for g in self._subagent_groups.values():
            g.tick_spinners()
            g.flush_preview()
        try:
            self.query_one("#subagent-tree", SubagentTreePanel).tick()
        except Exception:
            pass
        try:
            self.query_one("#bg-runs-panel", BackgroundRunsPanel).tick()
        except Exception:
            pass
        # Flush batched thinking content
        if self._current_thinking is not None:
            self._current_thinking.flush()
        # Flush batched scroll (set by on_append_token)
        if self._scroll_pending:
            self._scroll_pending = False
            try:
                self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Input submission
    # ------------------------------------------------------------------

    async def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return

        if value.startswith("/"):
            self._handle_slash_command(value)
            return

        await self._start_run(value)

    def _handle_slash_command(self, cmd: str) -> None:
        """Dispatch slash commands — action_* methods run as workers via run_worker."""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command == "/clear":
            self.action_clear_messages()
        elif command in ("/theme", "/t"):
            self.action_pick_theme()
        elif command == "/agents":
            self.run_worker(self._list_agents())
        elif command in ("/threads", "/r"):
            self.action_resume_thread()
        elif command in ("/runs", "/o"):
            self.action_open_runs()
        elif command == "/bg":
            self.action_background_run()
        elif command in ("/auto", "/a"):
            self.action_toggle_auto_approve()
        elif command == "/compact":
            arg = parts[1].strip() if len(parts) > 1 else ""
            if arg == "status":
                self.run_worker(self._cmd_compact_status())
            else:
                self.run_worker(self._compact_thread())
        elif command in ("/new", "/n"):
            self.action_new_thread()
        elif command in ("/quit", "/q"):
            self.action_quit()
        elif command == "/help":
            self._show_help()
        elif command == "/debug":
            self._show_debug_state()
        elif command == "/model":
            self.run_worker(self._cmd_model(parts[1].strip() if len(parts) > 1 else ""))
        elif command == "/mcp":
            self._cmd_mcp()
        elif command == "/skills":
            self._cmd_skills_list()
        elif command.startswith("/skill:"):
            skill_name = command[7:]
            args = parts[1].strip() if len(parts) > 1 else ""
            self.run_worker(self._cmd_invoke_skill(skill_name, args))
        elif command == "/editor":
            self.run_worker(self._cmd_open_editor())
        elif command == "/changelog":
            self._open_url("https://github.com/RevoltSecurities/RAI/blob/main/CHANGELOG.md", "changelog")
        elif command == "/issue":
            self._open_url("https://github.com/RevoltSecurities/RAI/issues/new/choose", "GitHub Issues")
        elif command == "/tokens":
            self._cmd_tokens()
        elif command == "/findings":
            self._cmd_findings()
        elif command == "/create-agent":
            self.run_worker(self._rai_start_create_wizard(), exclusive=False, name="create_agent_wizard")
        else:
            self._notify_error(f"Unknown command: {command}")

    async def _start_run(self, text: str, *, skill_allowed_tools: list[str] | None = None) -> None:
        self._current_run_prompt = text
        self._last_assistant = None
        self._current_thinking = None
        self._tool_widgets.clear()
        self._subagent_groups.clear()
        self._subagent_run_ids.clear()

        container = self.query_one("#messages", Container)
        await container.mount(UserMsg(text))

        am = AssistantMsg()
        await container.mount(am)
        self._last_assistant = am
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

        self.query_one("#input-bar", InputBar).set_disabled(True)
        self.query_one("#status-bar", StatusBar).run_status = "running"
        self.query_one("#header", HeaderBar).run_mode = "running"

        self.query_one("#subagent-tree", SubagentTreePanel).reset()

        # Snapshot effective message count before the run so on_run_completed can
        # detect whether auto-compact fired during this run (count drops by >50%).
        self._pre_run_msg_count = 0
        if self._active_thread_id:
            try:
                _pre = await self._client.threads.compact_status(self._active_thread_id)
                self._pre_run_msg_count = _pre.message_count
            except Exception:
                pass

        try:
            if self._auto_approve:
                allowed_tools: list[str] | None = []
            elif skill_allowed_tools is not None:
                allowed_tools = skill_allowed_tools
            else:
                allowed_tools = None

            run = await self._client.runs.create(
                self._agent,
                text,
                thread_id=self._active_thread_id,
                allowed_tools=allowed_tools,
                model=self._model_override,
            )
            self._model_override = None
            self._active_run_id = run.run_id
            self._active_thread_id = run.thread_id

            header = self.query_one("#header", HeaderBar)
            header.thread_id = run.thread_id or ""

            pump = SSEEventPump()
            task = asyncio.create_task(
                pump.run(self, self._client, self._agent, run.run_id)
            )
            self._pump_tasks[run.run_id] = task

        except Exception as exc:
            self._notify_error(f"Failed to start run: {exc}")
            self.query_one("#input-bar", InputBar).set_disabled(False)
            self.query_one("#status-bar", StatusBar).run_status = "error"

    # ------------------------------------------------------------------
    # SSE event handlers
    # ------------------------------------------------------------------

    async def on_append_token(self, event: AppendToken) -> None:
        if event.run_id in self._bg_run_ids:
            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                panel.buffer_token(event.run_id, event.content)
                if self._detail_screen and self._detail_run_id == event.run_id:
                    self._detail_screen.post_message(
                        RunDetailScreen.LiveEvent({"type": "message", "text": event.content})
                    )
            except Exception:
                pass
            return
        if self._current_thinking is not None and not self._current_thinking._done:
            self._current_thinking.mark_done()
        if self._last_assistant is None or self._last_assistant._is_final:
            am = AssistantMsg()
            container = self.query_one("#messages", Container)
            await container.mount(am)
            self._last_assistant = am
        self._last_assistant.append_text(event.content)
        self._scroll_pending = True  # batched — flushed by _tick_animations

    def on_append_thinking(self, event: AppendThinking) -> None:
        if event.run_id in self._bg_run_ids:
            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                panel.buffer_thinking(event.run_id, event.content)
                if self._detail_screen and self._detail_run_id == event.run_id:
                    self._detail_screen.post_message(
                        RunDetailScreen.LiveEvent({"type": "thinking", "text": event.content})
                    )
            except Exception:
                pass
            return
        if self._current_thinking is None:
            thinking = ThinkingMsg()
            self._current_thinking = thinking
            container = self.query_one("#messages", Container)
            self.call_after_refresh(container.mount, thinking)
        self._current_thinking.append(event.content)

    async def on_tool_started(self, event: ToolStarted) -> None:
        if event.tool_name in {"ask_user", "compact_conversation"}:
            return
        if event.tool_name in _PLAN_EXEC_TOOLS:
            return
        if event.run_id in self._bg_run_ids:
            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                panel.track_tool_start(event.run_id, event.tool_name, _args_inline(event.tool_name, event.tool_input))
                idx = panel.buffer_tool_start(event.run_id, event.tool_name, event.tool_input)
                if self._detail_screen and self._detail_run_id == event.run_id:
                    self._detail_screen.post_message(RunDetailScreen.LiveEvent({
                        "type": "tool_start", "name": event.tool_name, "input": event.tool_input,
                        "idx": idx, "output": None, "is_error": False, "done": False,
                    }))
            except Exception:
                pass
            return
        # Sweep ALL empty AssistantMsg spinners — not just _last_assistant —
        # to kill orphans left by parallel tool completions.
        try:
            for w in list(self.query(AssistantMsg)):
                if not w._buf and not w._is_final:
                    await w.remove()
                elif not w._is_final:
                    w.set_final()
        except Exception:
            pass
        self._last_assistant = None
        if self._current_thinking is not None:
            self._current_thinking.mark_done()
        self._current_thinking = None
        container = self.query_one("#messages", Container)
        tool_widget = ToolCallMsg(event.tool_name, event.tool_input)
        await container.mount(tool_widget)
        key = f"{event.run_id}:{event.tool_name}:{len(self._tool_widgets)}"
        self._tool_widgets[key] = tool_widget
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    async def on_tool_finished(self, event: ToolFinished) -> None:
        if event.tool_name in {"ask_user", "compact_conversation"}:
            return
        if event.tool_name in _PLAN_EXEC_TOOLS:
            return
        if event.run_id in self._bg_run_ids:
            try:
                output   = event.tool_output or ""
                is_error = isinstance(output, str) and output.lower().startswith("error")
                panel    = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                panel.track_tool_finish(event.run_id, event.tool_name, error=is_error)
                idx = panel.buffer_tool_end(event.run_id, event.tool_name, event.tool_output, is_error)
                if self._detail_screen and self._detail_run_id == event.run_id and idx >= 0:
                    self._detail_screen.post_message(RunDetailScreen.LiveEvent({
                        "type": "tool_end", "name": event.tool_name,
                        "idx": idx, "output": event.tool_output, "is_error": is_error,
                    }))
            except Exception:
                pass
            return
        for key in reversed(list(self._tool_widgets.keys())):
            w = self._tool_widgets[key]
            if w.tool_name == event.tool_name and w._state == "running":
                output = event.tool_output
                if isinstance(output, str) and output.lower().startswith("error"):
                    w.set_error(output)
                else:
                    w.set_success(output)
                break
        # Mount a spinner only if there isn't already a live one waiting.
        # This prevents duplicate spinners from parallel tool completions.
        if self._active_run_id == event.run_id:
            if (self._last_assistant is None
                    or self._last_assistant._is_final
                    or self._last_assistant._buf):
                am = AssistantMsg()
                container = self.query_one("#messages", Container)
                await container.mount(am)
                self._last_assistant = am

    def on_permission_denied(self, event: PermissionDenied) -> None:
        for key in reversed(list(self._tool_widgets.keys())):
            w = self._tool_widgets[key]
            if w.tool_name == event.tool_name and w._state == "running":
                w.set_denied(event.reason)
                break

    def on_ask_user_required(self, event: AskUserRequired) -> None:
        self.query_one("#status-bar", StatusBar).mode_label = "ask_user"
        self.query_one("#ask-user-panel", AskUserPanel).show(
            event.questions, thread_id=event.thread_id, run_id=event.run_id
        )

    async def on_ask_user_panel_answered(self, event: AskUserPanel.Answered) -> None:
        self.query_one("#status-bar", StatusBar).mode_label = ""
        try:
            await self._client.threads.submit_ask_user(event.thread_id, event.answers)
        except Exception as exc:
            # 409 = already resolved server-side (run ended while panel was open) — not an error
            if "409" not in str(exc):
                self._notify_error(f"ask_user submit failed: {exc}")

    async def on_ask_user_panel_cancelled(self, event: AskUserPanel.Cancelled) -> None:
        self.query_one("#status-bar", StatusBar).mode_label = ""
        try:
            await self._client.threads.submit_ask_user(event.thread_id, [], status="cancelled")
        except Exception as exc:
            if "409" not in str(exc):
                self._notify_error(f"ask_user cancel failed: {exc}")

    async def on_hitlrequired(self, event: HITLRequired) -> None:
        is_bg = event.run_id in self._bg_run_ids
        if self._auto_approve:
            try:
                await self._client.threads.submit_interrupt(event.thread_id, "approve")
            except Exception:
                pass
            return
        if is_bg:
            try:
                self.query_one("#bg-runs-panel", BackgroundRunsPanel).set_status(event.run_id, "hitl")
            except Exception:
                pass
        self.query_one("#status-bar", StatusBar).mode_label = "hitl"
        hitl = self.query_one("#hitl-panel", HITLPanelWidget)
        hitl.show(
            event.action_requests,
            thread_id=event.thread_id,
            task_id="",
            interrupt_id=event.interrupt_id,
            is_subagent=False,
            run_id=event.run_id,
            is_background=is_bg,
        )

    def on_hitlauto_approved(self, event: HITLAutoApproved) -> None:
        pass

    def on_hitlresolved(self, event: HITLResolved) -> None:
        try:
            self.query_one("#hitl-panel", HITLPanelWidget).hide()
        except Exception:
            pass
        try:
            self.query_one("#bg-runs-panel", BackgroundRunsPanel).set_status(event.run_id, "running")
        except Exception:
            pass
        self.query_one("#status-bar", StatusBar).mode_label = ""

    def on_session_approved(self, event: SessionApproved) -> None:
        pass

    async def on_plan_mode_entered(self, event: PlanModeEntered) -> None:
        self.query_one("#status-bar", StatusBar).mode_label = "plan"
        self.query_one("#header", HeaderBar).plan_mode = True
        self.query_one("#input-bar", InputBar).set_plan_mode(True)
        container = self.query_one("#messages", Container)
        await container.mount(PlanModeEnteredMsg())
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    def on_plan_ready(self, event: PlanReady) -> None:
        self.query_one("#plan-panel", PlanPanel).show(event.plan, event.run_id, self._agent)
        self.query_one("#status-bar", StatusBar).mode_label = "plan"
        self.query_one("#header", HeaderBar).plan_mode = True

    def on_plan_done(self, event: PlanDone) -> None:
        pass  # badge stays "plan" through execution — cleared only on plan_completed / run_completed

    def on_subagent_added(self, event: SubagentAdded) -> None:
        self._subagent_run_ids[event.task_id] = event.run_id
        self.query_one("#subagent-tree", SubagentTreePanel).add_task(event.task_id, event.agent_name)
        group = SubagentGroup(event.task_id, event.agent_name)
        self._subagent_groups[event.task_id] = group
        container = self.query_one("#messages", Container)
        self.call_after_refresh(container.mount, group)
        try:
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.add(event.task_id, event.agent_name)
            bg.show_panel()
        except Exception:
            pass

    def on_subagent_token(self, event: SubagentToken) -> None:
        if event.task_id in self._subagent_groups:
            self._subagent_groups[event.task_id].append_token(event.content)
        self.query_one("#subagent-tree", SubagentTreePanel).append_token(event.task_id, event.content)
        try:
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.buffer_token(event.task_id, event.content)
            if self._detail_screen and self._detail_run_id == event.task_id:
                self._detail_screen.post_message(
                    RunDetailScreen.LiveEvent({"type": "message", "text": event.content})
                )
        except Exception:
            pass

    def on_subagent_thinking(self, event: SubagentThinking) -> None:
        try:
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.buffer_thinking(event.task_id, event.content)
            if self._detail_screen and self._detail_run_id == event.task_id:
                self._detail_screen.post_message(
                    RunDetailScreen.LiveEvent({"type": "thinking", "text": event.content})
                )
        except Exception:
            pass

    async def on_subagent_tool_started(self, event: SubagentToolStarted) -> None:
        if event.task_id in self._subagent_groups:
            self._subagent_groups[event.task_id].add_tool(
                event.tool_name, event.tool_name, event.tool_input,
            )
        try:
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.track_tool_start(event.task_id, event.tool_name, _args_inline(event.tool_name, event.tool_input))
            idx = bg.buffer_tool_start(event.task_id, event.tool_name, event.tool_input)
            if self._detail_screen and self._detail_run_id == event.task_id:
                self._detail_screen.post_message(RunDetailScreen.LiveEvent({
                    "type": "tool_start", "name": event.tool_name,
                    "input": event.tool_input, "idx": idx,
                    "output": None, "is_error": False, "done": False,
                }))
        except Exception:
            pass

    def on_subagent_tool_finished(self, event: SubagentToolFinished) -> None:
        if event.task_id in self._subagent_groups:
            tool_w = self._subagent_groups[event.task_id].get_tool(event.tool_name)
            if tool_w:
                output = event.tool_output
                if isinstance(output, str) and output.lower().startswith("error"):
                    tool_w.set_error(output)
                else:
                    tool_w.set_success(output)
        try:
            output   = event.tool_output
            is_error = isinstance(output, str) and output.lower().startswith("error")
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.track_tool_finish(event.task_id, event.tool_name, error=is_error)
            idx = bg.buffer_tool_end(event.task_id, event.tool_name, output, is_error)
            if self._detail_screen and self._detail_run_id == event.task_id and idx >= 0:
                self._detail_screen.post_message(RunDetailScreen.LiveEvent({
                    "type": "tool_end", "name": event.tool_name,
                    "idx": idx, "output": output, "is_error": is_error,
                }))
        except Exception:
            pass

    def on_subagent_done(self, event: SubagentDone) -> None:
        self.query_one("#subagent-tree", SubagentTreePanel).mark_done(event.task_id, event.status)
        if event.task_id in self._subagent_groups:
            self._subagent_groups[event.task_id].mark_done(event.status)
        try:
            final_status = "error" if event.status == "error" else "done"
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            bg.set_status(event.task_id, final_status)
            if self._detail_screen and self._detail_run_id == event.task_id:
                self._detail_screen.post_message(RunDetailScreen.LiveEvent({"type": "done"}))
            run_id_copy = event.task_id
            self.set_timer(3.0, lambda: bg.remove(run_id_copy))
        except Exception:
            pass

    async def on_subagent_hitl(self, event: SubagentHITL) -> None:
        if self._auto_approve:
            try:
                await self._client.subagents.submit_interrupt(event.task_id, "approve")
            except Exception:
                pass
            return
        try:
            self.query_one("#bg-runs-panel", BackgroundRunsPanel).set_status(event.task_id, "hitl")
        except Exception:
            pass
        self.query_one("#hitl-panel", HITLPanelWidget).show(
            event.action_requests,
            thread_id="",
            task_id=event.task_id,
            interrupt_id=event.interrupt_id,
            is_subagent=True,
        )

    def on_subagent_hitlauto_approved(self, event: SubagentHITLAutoApproved) -> None:
        pass

    async def on_run_completed(self, event: RunCompleted) -> None:
        if event.run_id in self._bg_run_ids:
            self._bg_run_ids.discard(event.run_id)
            short_id    = event.run_id[:8]
            agent_name  = self._agent
            output_text = ""

            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                entry = panel.get_run(event.run_id)
                if entry:
                    agent_name = entry.agent
                    # Collect only the post-background buffered message tokens
                    output_text = "".join(
                        ev["text"] for ev in entry.chat_events
                        if ev["type"] == "message"
                    )
                    # Fall back to run-end output if nothing was buffered
                    if not output_text:
                        output_text = event.output or ""
                final_status = "done" if event.status in ("completed", "turn_complete") else "error"
                panel.set_status(event.run_id, final_status)
                # For turn_complete (resumed subagent) keep the panel slot — user may continue
                if event.status != "turn_complete":
                    run_id_copy = event.run_id
                    self.set_timer(2.0, lambda: panel.remove(run_id_copy))
            except Exception:
                pass

            if self._detail_screen and self._detail_run_id == event.run_id:
                self._detail_screen.post_message(
                    RunDetailScreen.LiveEvent({"type": "done"})
                )

            bg_count = len(self._bg_run_ids)
            self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}" if bg_count > 0 else "idle"

            # Render the bg run's response into the main chat
            container = self.query_one("#messages", Container)
            if event.status not in ("completed", "turn_complete"):
                await container.mount(Static(
                    f"[dim]⧖ [bold]{_escape(agent_name)}[/bold]  bg:{short_id}  [red]failed[/red][/dim]",
                    classes="sys-msg",
                ))
            elif output_text:
                await container.mount(Static(
                    f"[dim]⧖ [bold]{_escape(agent_name)}[/bold]  bg:{short_id}  complete[/dim]",
                    classes="sys-msg",
                ))
                am = AssistantMsg()
                await container.mount(am)
                am.append_text(output_text)
                am.set_final()
                self.notify(f"bg:{short_id} complete", timeout=3)
                try:
                    self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
                except Exception:
                    pass
            else:
                self.notify(f"bg:{short_id} complete", timeout=3)
            return

        # Ignore stale completions from runs that are no longer the active run
        if event.run_id != self._active_run_id:
            return

        self._active_run_id = None

        if self._last_assistant:
            self._last_assistant.set_final()
        if self._current_thinking is not None:
            self._current_thinking.mark_done()
        self._current_thinking = None

        # Kill any orphaned AssistantMsg spinners and ThinkingMsg widgets
        for w in list(self.query(AssistantMsg)):
            if not w._buf and not w._is_final:
                await w.remove()
            elif not w._is_final:
                w.set_final()
        self._last_assistant = None
        for w in list(self.query(ThinkingMsg)):
            w.mark_done()

        # Clean up any overlay panels that may be stuck open from this run
        try:
            self.query_one("#ask-user-panel", AskUserPanel).hide()
        except Exception:
            pass
        try:
            self.query_one("#hitl-panel", HITLPanelWidget).hide()
        except Exception:
            pass
        self.query_one("#input-bar", InputBar).set_disabled(False)
        status_bar = self.query_one("#status-bar", StatusBar)
        if event.status == "completed":
            status_bar.run_status = "done"
        elif event.status == "cancelled":
            status_bar.run_status = "cancelled"
            container = self.query_one("#messages", Container)
            await container.mount(
                Static("[dim]⊘ Run cancelled[/dim]", classes="sys-msg")
            )
        else:
            status_bar.run_status = "error"
        status_bar.mode_label = ""
        self.query_one("#header", HeaderBar).plan_mode = False
        self.query_one("#input-bar", InputBar).set_plan_mode(False)

        # Accumulate token usage from completed run
        completed_run_id = event.run_id
        try:
            detail = await self._client.runs.get(self._agent, completed_run_id)
            if detail and detail.usage:
                status_bar.add_usage(
                    detail.usage.input_tokens,
                    detail.usage.output_tokens,
                )
        except Exception:
            pass

        # Warn if context is approaching the compaction threshold.
        # compact_status applies _summarization_event server-side, so message_count and
        # estimated_tokens reflect effective context (post-compact), not raw state count.
        if self._active_thread_id and not self._compact_warning_shown:
            try:
                compact_st = await self._client.threads.compact_status(
                    self._active_thread_id
                )
                if compact_st.should_compact:
                    self._compact_warning_shown = True
                    container = self.query_one("#messages", Container)
                    await container.mount(
                        CompactWarningMsg(
                            estimated_tokens=compact_st.estimated_tokens,
                            message_count=compact_st.message_count,
                        )
                    )
                    self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
                elif (
                    self._pre_run_msg_count > 0
                    and compact_st.message_count < self._pre_run_msg_count // 2
                ):
                    # Message count dropped by >50% during this run — auto-compact fired.
                    self._compact_warning_shown = True
                    container = self.query_one("#messages", Container)
                    await container.mount(CompactMsg(auto=True))
                    self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
            except Exception:
                pass

        bg_count = len(self._bg_run_ids)
        self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}" if bg_count > 0 else "idle"

    def on_run_error(self, event: RunError) -> None:
        if event.run_id in self._bg_run_ids:
            return
        if self._last_assistant:
            self._last_assistant.append_text(f"\n\n[red]Error: {event.message}[/red]")
            self._last_assistant.set_final()
        # Stop timers on ALL orphaned spinners (sync — can't await remove)
        for w in list(self.query(AssistantMsg)):
            if not w._is_final:
                w.set_final()
        for w in list(self.query(ThinkingMsg)):
            w.mark_done()
        self._last_assistant = None
        self._current_thinking = None
        try:
            self.query_one("#ask-user-panel", AskUserPanel).hide()
        except Exception:
            pass
        try:
            self.query_one("#hitl-panel", HITLPanelWidget).hide()
        except Exception:
            pass
        self.query_one("#input-bar", InputBar).set_disabled(False)
        self.query_one("#input-bar", InputBar).set_plan_mode(False)
        self.query_one("#header", HeaderBar).plan_mode = False
        self.query_one("#status-bar", StatusBar).mode_label = ""
        self.query_one("#status-bar", StatusBar).run_status = "error"

    def on_rate_limited(self, event: RateLimited) -> None:
        msg = f"Rate limited. Resets at: {event.resets_at}" if event.resets_at else "Rate limited."
        if self._last_assistant:
            self._last_assistant.append_text(f"\n[yellow]{msg}[/yellow]")

    async def on_step_complete(self, event: StepComplete) -> None:
        if event.run_id in self._bg_run_ids:
            return
        container = self.query_one("#messages", Container)
        widget = StepCompleteMsg(event.step_number, event.description)
        await container.mount(widget)
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    async def on_step_started(self, event: StepStarted) -> None:
        if event.run_id in self._bg_run_ids:
            return
        container = self.query_one("#messages", Container)
        await container.mount(StepStartMsg(event.step_number, event.description))
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    async def on_step_blocked(self, event: StepBlocked) -> None:
        if event.run_id in self._bg_run_ids:
            return
        container = self.query_one("#messages", Container)
        await container.mount(StepBlockedMsg(event.step_number, event.description, event.reason))
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    async def on_plan_completed(self, event: PlanCompleted) -> None:
        if event.run_id in self._bg_run_ids:
            return
        self.query_one("#status-bar", StatusBar).mode_label = ""
        self.query_one("#header", HeaderBar).plan_mode = False
        self.query_one("#input-bar", InputBar).set_plan_mode(False)
        container = self.query_one("#messages", Container)
        await container.mount(PlanCompletedMsg(event.total_steps))
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    def on_notification_arrived(self, event: NotificationArrived) -> None:
        pass

    def on_pipeline_update(self, event: PipelineUpdate) -> None:
        pass

    # ------------------------------------------------------------------
    # HITLPanel / PlanPanel decisions
    # ------------------------------------------------------------------

    async def on_hitlpanel_decision(self, event: HITLPanel.Decision) -> None:
        try:
            bg = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            if event.is_subagent and event.task_id:
                bg.set_status(event.task_id, "running")
            else:
                bg.set_status(event.run_id, "running")
        except Exception:
            pass
        try:
            first = event.decisions[0] if event.decisions else {"type": "approve"}
            decision_type = first.get("type", "approve")
            payload: dict = {"decision": decision_type}
            if decision_type == "edit":
                payload["edited_action"] = first.get("edited_action") or {}
            elif decision_type in ("reject", "respond"):
                msg = first.get("message", "")
                if msg:
                    payload["message"] = msg
            if event.is_subagent:
                await self._client.subagents.submit_interrupt(event.task_id, payload)
            else:
                await self._client.threads.submit_interrupt(event.thread_id, payload)
        except Exception as exc:
            self._notify_error(f"HITL submit failed: {exc}")

    async def on_plan_panel_decision(self, event: PlanPanel.Decision) -> None:
        try:
            if event.decision == "approve":
                await self._client.runs.approve_plan(self._agent, event.run_id)
            elif event.decision == "edit":
                await self._client.runs.edit_plan(self._agent, event.run_id, event.feedback)
            elif event.decision == "respond":
                await self._client.runs.respond_plan(self._agent, event.run_id, event.feedback)
            else:  # "reject"
                await self._client.runs.reject_plan(self._agent, event.run_id, event.feedback)
        except Exception as exc:
            self._notify_error(f"Plan decision failed: {exc}")

    # ------------------------------------------------------------------
    # BackgroundRunsPanel message handlers
    # ------------------------------------------------------------------

    def on_background_runs_panel_view_run(self, event: BackgroundRunsPanel.ViewRun) -> None:
        run_id = event.run_id
        try:
            panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            if panel.get_run(run_id) is None:
                return
        except Exception:
            return
        self.run_worker(self._do_view_run(run_id), exclusive=False, name=f"view_{run_id[:8]}")

    async def _do_view_run(self, run_id: str) -> None:
        try:
            panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            panel.hide_panel()
            screen = RunDetailScreen(run_id, panel)
            self._detail_screen = screen
            self._detail_run_id = run_id
            await self.push_screen_wait(screen)
        except Exception:
            pass
        finally:
            # Screen dismissed — clear tracking and re-show panel if runs remain
            self._detail_screen = None
            self._detail_run_id = None
            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                if self._bg_run_ids or panel.count > 0:
                    panel.show_panel()
            except Exception:
                pass

    def _cancel_bg_slot(self, slot_id: str) -> None:
        if slot_id in self._pump_tasks:
            self._pump_tasks[slot_id].cancel()
            del self._pump_tasks[slot_id]
        self._bg_run_ids.discard(slot_id)

    def on_background_runs_panel_stop_run(self, event: BackgroundRunsPanel.StopRun) -> None:
        run_id = event.run_id
        self._cancel_bg_slot(run_id)
        # For subagent entries (tracked by task_id, not in _bg_run_ids), cancel server-side
        subagent_run_id = self._subagent_run_ids.pop(run_id, None)
        if subagent_run_id:
            self.run_worker(
                self._do_cancel_subagent(subagent_run_id),
                exclusive=False, name=f"cancel_sub_{run_id[:8]}",
            )
        try:
            panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            panel.remove(run_id)
            if panel.count == 0:
                panel.hide_panel()
        except Exception:
            pass
        bg_count = len(self._bg_run_ids)
        self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}" if bg_count > 0 else "idle"

    async def _do_cancel_subagent(self, run_id: str) -> None:
        try:
            await self._client.runs.cancel(self._agent, run_id)
        except Exception:
            pass

    def on_background_runs_panel_stop_all(self, event: BackgroundRunsPanel.StopAll) -> None:
        for run_id in list(self._bg_run_ids):
            self._cancel_bg_slot(run_id)
        self._bg_run_ids.clear()
        try:
            self.query_one("#bg-runs-panel", BackgroundRunsPanel).hide_panel()
        except Exception:
            pass
        self.query_one("#header", HeaderBar).run_mode = "idle"

    async def on_run_detail_screen_new_prompt(self, event: RunDetailScreen.NewPrompt) -> None:
        """Continue a backgrounded main-agent run on the same thread."""
        run_id = event.run_id
        text   = event.text

        # Subagent entries (tracked in bg panel but not in _bg_run_ids) are
        # controlled by the main agent — prompt the user to use the main chat.
        if run_id not in self._bg_run_ids:
            self.notify(
                "Subagents are controlled by the main agent — send your message in the main chat.",
                timeout=5,
            )
            return

        try:
            panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
            entry = panel.get_run(run_id)
            thread_id  = entry.thread_id if entry else self._active_thread_id or ""
            agent_name = entry.agent if entry else self._agent
        except Exception:
            thread_id  = self._active_thread_id or ""
            agent_name = self._agent

        try:
            run = await self._client.runs.create(agent_name, text, thread_id=thread_id or None)
            new_run_id = run.run_id

            self._bg_run_ids.add(new_run_id)
            try:
                panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
                panel.add(new_run_id, agent_name, thread_id=thread_id)
                panel.set_prompt(new_run_id, text)
            except Exception:
                pass

            self._detail_run_id = new_run_id
            if self._detail_screen:
                self._detail_screen.post_message(RunDetailScreen.SwitchRun(new_run_id))

            pump = SSEEventPump()
            pump_task = asyncio.create_task(
                pump.run(self, self._client, agent_name, new_run_id)
            )
            self._pump_tasks[new_run_id] = pump_task

            bg_count = len(self._bg_run_ids)
            self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}"

        except Exception as exc:
            self._notify_error(f"Failed to continue bg run: {exc}")

    def on_run_detail_screen_stop(self, event: RunDetailScreen.Stop) -> None:
        run_id = event.run_id
        if self._detail_run_id == run_id:
            self._detail_screen = None
            self._detail_run_id = None
        self._cancel_bg_slot(run_id)
        try:
            self.query_one("#bg-runs-panel", BackgroundRunsPanel).remove(run_id)
        except Exception:
            pass
        bg_count = len(self._bg_run_ids)
        self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}" if bg_count > 0 else "idle"

    # ------------------------------------------------------------------
    # Actions — those that need push_screen_wait use run_worker
    # ------------------------------------------------------------------

    def action_background_run(self) -> None:
        panel = self.query_one("#bg-runs-panel", BackgroundRunsPanel)
        if not self._active_run_id:
            # Show panel when bg runs or subagents are active; toggle when empty
            if self._bg_run_ids or panel.count > 0:
                panel.show_panel()
            else:
                panel.toggle()
            return
        run_id    = self._active_run_id
        thread_id = self._active_thread_id or ""
        self._bg_run_ids.add(run_id)
        self._active_run_id = None
        if self._last_assistant:
            self._last_assistant.set_final()
            self._last_assistant = None
        panel.add(run_id, self._agent, thread_id=thread_id)
        panel.set_prompt(run_id, self._current_run_prompt)
        panel.show_panel()
        self.query_one("#input-bar", InputBar).set_disabled(False)
        self.query_one("#status-bar", StatusBar).run_status = "idle"
        bg_count = len(self._bg_run_ids)
        self.query_one("#header", HeaderBar).run_mode = f"bg:{bg_count}"
        short_id = run_id[:8]
        container = self.query_one("#messages", Container)
        self.call_after_refresh(
            container.mount,
            Static(
                f"[dim]⧖ run [bold]{short_id}[/bold] backgrounded — ctrl+b to monitor[/dim]",
                classes="sys-msg",
            ),
        )

    def action_open_runs(self) -> None:
        """Opens runs browser — uses run_worker to satisfy push_screen_wait."""
        self.run_worker(self._do_open_runs(), exclusive=True, name="open_runs")

    async def _do_open_runs(self) -> None:
        try:
            runs = await self._client.runs.list_all(limit=50)
        except Exception:
            runs = []
        if not runs:
            self._notify_error("No runs found.")
            return
        screen = RunsBrowserScreen(runs)
        result = await self.push_screen_wait(screen)
        if result and isinstance(result, RunsBrowserScreen.RunSelected):
            await self._attach_run(result.run_id, result.agent, result.status)

    async def _attach_run(self, run_id: str, agent: str, status: str) -> None:
        if status in ("running", "pending", "interrupted"):
            self._active_run_id = run_id
            pump = SSEEventPump()
            task = asyncio.create_task(
                pump.run(self, self._client, agent or self._agent, run_id)
            )
            self._pump_tasks[run_id] = task
            self.query_one("#status-bar", StatusBar).run_status = "running"
            self.query_one("#input-bar", InputBar).set_disabled(True)
            # The "interrupt" SSE event was published before this connection —
            # RunEventBus skips replay when last_event_id=None. For already-
            # interrupted runs, poll the interrupt state via HTTP immediately.
            if status == "interrupted":
                try:
                    detail = await self._client.runs.get(agent or self._agent, run_id)
                    thread_id = getattr(detail, "thread_id", "")
                    if thread_id:
                        intr = await self._client.threads.get_interrupt(thread_id)
                        if intr.pending:
                            self.post_message(
                                HITLRequired(
                                    run_id,
                                    thread_id,
                                    intr.interrupt_id or "",
                                    intr.action_requests or [],
                                )
                            )
                except Exception:
                    pass
        else:
            try:
                detail = await self._client.runs.get(agent or self._agent, run_id)
                output = getattr(detail, "output", "") or ""
                if output:
                    am = AssistantMsg()
                    self._last_assistant = am
                    await self.query_one("#messages", Container).mount(am)
                    am.append_text(output)
                    am.set_final()
            except Exception as exc:
                self._notify_error(f"Failed to load run output: {exc}")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "cancel_run":
            if not self._active_run_id:
                return False
            # Let ESC close overlay panels instead of cancelling the run
            try:
                if self.query_one("#bg-runs-panel", BackgroundRunsPanel)._visible:
                    return False
            except Exception:
                pass
            try:
                if not self.query_one("#hitl-panel", HITLPanelWidget).has_class("hitl-hidden"):
                    return False
            except Exception:
                pass
            try:
                if self.query_one("#ask-user-panel", AskUserPanel).has_class("panel-open"):
                    return False
            except Exception:
                pass
            return True
        return True

    def action_cancel_run(self) -> None:
        self.run_worker(self._do_cancel_run(), exclusive=False, name="cancel_run")

    async def _do_cancel_run(self) -> None:
        run_id = self._active_run_id
        if not run_id:
            return
        self.notify("Cancelling…", timeout=2)
        try:
            await self._client.runs.cancel(self._agent, run_id)
        except Exception:
            pass
        # Cancel the local SSE pump silently so it doesn't post a stale RunError
        task = self._pump_tasks.pop(run_id, None)
        if task and not task.done():
            task.cancel()
        # Drive normal TUI cleanup through the existing on_run_completed path
        self.post_message(RunCompleted(run_id, "cancelled"))

    def action_new_thread(self) -> None:
        self._active_thread_id = str(uuid.uuid4())
        self._active_run_id = None
        self._last_assistant = None
        self._current_thinking = None
        self._tool_widgets.clear()
        self._subagent_groups.clear()
        self._subagent_run_ids.clear()
        self._model_override = None
        self._discovered_skills = []
        self._compact_warning_shown = False
        self._pre_run_msg_count = 0
        # Bind a fresh findings store for the new thread
        try:
            from rai.tools.core.findings import init_findings_store
            init_findings_store(self._active_thread_id)
        except Exception:
            pass
        self.query_one("#header", HeaderBar).thread_id = self._active_thread_id
        self.action_clear_messages()
        self.run_worker(self._restore_banner(), exclusive=False)
        self.run_worker(self._discover_skills(), exclusive=False, name="skill_discovery")

    def action_resume_thread(self) -> None:
        """Opens thread browser — uses run_worker to satisfy push_screen_wait."""
        self.run_worker(self._do_resume_thread(), exclusive=True, name="resume_thread")

    async def _do_resume_thread(self) -> None:
        try:
            # Only show threads for the current agent, not subagents
            threads_resp = await self._client.threads.list(
                agent=self._agent, limit=30
            )
            threads = [
                t.__dict__ if hasattr(t, "__dict__") else dict(t)
                for t in threads_resp
            ]
        except Exception:
            threads = []
        if not threads:
            self._notify_error(f"No threads found for agent '{self._agent}'.")
            return

        async def _get_prompt(t: dict) -> str:
            try:
                tid = t.get("thread_id", "")
                h = await self._client.threads.history(tid, limit=3)
                for m in h.get("messages", []):
                    if m.get("type") == "human" and m.get("content"):
                        return str(m["content"])[:80]
            except Exception:
                pass
            return ""

        prompts = await asyncio.gather(*[_get_prompt(t) for t in threads])
        for t, p in zip(threads, prompts):
            t["initial_prompt"] = p

        screen = ThreadBrowserScreen(threads)
        result = await self.push_screen_wait(screen)
        if result and isinstance(result, ThreadBrowserScreen.ThreadSelected):
            self._active_thread_id = result.thread_id
            self.action_clear_messages()
            await self._load_thread_history(result.thread_id)

    def action_pick_theme(self) -> None:
        """Opens theme picker — uses run_worker to satisfy push_screen_wait."""
        self.run_worker(self._do_pick_theme(), exclusive=True, name="pick_theme")

    async def _do_pick_theme(self) -> None:
        current = str(self.theme) if self.theme else self._default_theme
        screen = ThemePickerScreen(current_theme=current)
        await self.push_screen_wait(screen)

    def action_toggle_auto_approve(self) -> None:
        self._auto_approve = not self._auto_approve
        self.query_one("#status-bar", StatusBar).auto_approve = self._auto_approve
        label = "ON" if self._auto_approve else "OFF"
        self.notify(f"Auto-approve {label}", timeout=2)

    def action_clear_messages(self) -> None:
        try:
            self.query_one("#messages", Container).remove_children()
        except Exception:
            pass
        self._last_assistant = None
        self._current_thinking = None
        self._tool_widgets.clear()
        self._subagent_groups.clear()
        self._subagent_run_ids.clear()
        self._model_override = None
        self._compact_warning_shown = False
        self._pre_run_msg_count = 0
        self.query_one("#subagent-tree", SubagentTreePanel).reset()

    def action_toggle_plan(self) -> None:
        try:
            panel = self.query_one("#plan-panel", PlanPanel)
            if not panel.display:
                panel.display = True
            else:
                panel.hide()
        except Exception:
            pass

    def action_toggle_subagents(self) -> None:
        try:
            self.query_one("#subagent-tree", SubagentTreePanel).toggle()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _restore_banner(self) -> None:
        if self._show_banner:
            wb = WelcomeBanner(self._agent, self._base_url, custom_markup=self._banner_markup)
            self._welcome_banner = wb
            container = self.query_one("#messages", Container)
            await container.mount(wb)
            self.run_worker(self._fetch_recent_threads(wb), exclusive=False)

    def _notify_error(self, msg: str) -> None:
        self.notify(msg, severity="error", timeout=5)

    async def _list_agents(self) -> None:
        try:
            agents = await self._client.agents.list()
            names = ", ".join(getattr(a, "name", str(a)) for a in (agents or []))
            self.notify(f"Agents: {names}", timeout=5)
        except Exception as exc:
            self._notify_error(f"Failed to list agents: {exc}")

    async def _compact_thread(self) -> None:
        if not self._active_thread_id:
            self._notify_error("No active thread.")
            return
        if self._active_run_id:
            self.notify("Cannot compact during an active run.", timeout=3)
            return
        container = self.query_one("#messages", Container)
        indicator = Static("  [dim]◈ Compacting context…[/dim]", classes="sys-msg")
        await container.mount(indicator)
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
        try:
            # threads.compact() calls graph.ainvoke("/compact") via the LLM tool path.
            # On success, _summarization_event is written to the checkpoint; subsequent
            # model calls see only [summary_msg] + recent messages.
            resp = await self._client.threads.compact(self._active_thread_id)
            await indicator.remove()
            self._compact_warning_shown = True
            if resp.status == "no_change":
                self.notify(
                    "Nothing to compact — conversation is within the token budget.\n"
                    "[dim]Compaction runs when context reaches ~50 k tokens.[/dim]",
                    timeout=6,
                )
            else:
                await container.mount(CompactMsg(auto=False))
                self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
        except Exception as exc:
            await indicator.remove()
            self._notify_error(f"Compact failed: {exc}")

    async def _cmd_compact_status(self) -> None:
        if not self._active_thread_id:
            self.notify("No active thread.", timeout=3)
            return
        try:
            st = await self._client.threads.compact_status(self._active_thread_id)
            rec = (
                "[yellow]compact recommended[/yellow]"
                if st.should_compact
                else "[green]OK[/green]"
            )
            def _fmt(n: int) -> str:
                if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
                if n >= 1_000: return f"{n / 1_000:.0f}k"
                return str(n)
            self.notify(
                f"[bold]Context Status[/bold]\n"
                f"  messages:    [cyan]{st.message_count}[/cyan]\n"
                f"  est. tokens: [cyan]{_fmt(st.estimated_tokens)}[/cyan]\n"
                f"  status:      {rec}",
                timeout=6,
            )
        except Exception as exc:
            self._notify_error(f"compact status failed: {exc}")

    def _show_help(self) -> None:
        self.notify(
            "/clear /agents /threads /runs /bg /theme /compact [status] /auto /new /quit /debug\n"
            "/model [name|reset]  /mcp  /skills  /skill:name [args]  /findings\n"
            "/editor (ctrl+x)  /changelog  /issue  /tokens\n"
            "ctrl+b bg  ctrl+o runs  ctrl+n new  ctrl+r resume\n"
            "ctrl+t theme  ctrl+a auto  ctrl+k clear  ctrl+q quit",
            title="Help",
            timeout=10,
        )

    def _show_debug_state(self) -> None:
        try:
            header = self.query_one("#header", HeaderBar)
            status = self.query_one("#status-bar", StatusBar)
            ask_panel = self.query_one("#ask-user-panel", AskUserPanel)
            ask_open = ask_panel.has_class("panel-open")
        except Exception:
            ask_open = "?"
        bg_count = len(self._bg_run_ids)
        lines = [
            f"agent={self._agent}",
            f"thread={self._active_thread_id or '-'}",
            f"run={self._active_run_id or '-'}",
            f"plan_mode={header.plan_mode}  run_mode={header.run_mode}",
            f"auto_approve={self._auto_approve}",
            f"model_override={self._model_override or '-'}",
            f"skills={len(self._discovered_skills)}",
            f"bg_runs={bg_count}  bg_ids={list(self._bg_run_ids)[:4]}",
            f"tool_widgets={len(self._tool_widgets)}",
            f"subagent_groups={len(self._subagent_groups)}",
            f"ask_user_open={ask_open}",
            f"last_assistant={'live' if self._last_assistant and not self._last_assistant._is_final else 'final/none'}",
        ]
        self.notify("\n".join(lines), title="Debug State", timeout=15)

    # ------------------------------------------------------------------
    # Skill / model / editor / url helpers
    # ------------------------------------------------------------------

    async def _discover_skills(self) -> None:
        import asyncio as _asyncio
        from rai.skills.discovery import list_skills
        try:
            skills = await _asyncio.to_thread(list_skills, self._agent)
            self._discovered_skills = skills
            self.query_one("#input-bar", InputBar).set_skill_commands(skills)
        except Exception:
            pass

    async def _discover_mcp(self) -> None:
        from pathlib import Path
        from rai.mcp.loader import resolve_and_load_mcp_tools
        try:
            _tools, manager, server_infos = await resolve_and_load_mcp_tools(
                cwd=Path.cwd(), agent_name=self._agent
            )
            self._mcp_server_infos = server_infos
            if manager:
                await manager.cleanup()
        except Exception:
            self._mcp_server_infos = []

    async def _cmd_model(self, override: str) -> None:
        if not override:
            result = await self.push_screen_wait(
                ModelPickerScreen(current_override=self._model_override)
            )
            if result:
                self._model_override = result
                self.notify(
                    f"Override set: [yellow]{result}[/yellow]  (clears after next run)",
                    timeout=4,
                )
            return
        if override == "reset":
            self._model_override = None
            self.notify("Model override cleared", timeout=3)
        else:
            self._model_override = override
            self.notify(f"Override set: [yellow]{override}[/yellow] (clears after next run)", timeout=4)

    def _cmd_mcp(self) -> None:
        self.run_worker(self._do_cmd_mcp(), exclusive=False, name="mcp_viewer")

    async def _do_cmd_mcp(self) -> None:
        await self.push_screen_wait(MCPViewerScreen(self._mcp_server_infos))

    def _cmd_skills_list(self) -> None:
        if not self._discovered_skills:
            self.notify(
                "No skills found.\n[dim]Skill dirs: ~/.rai/skills/  ·  .rai/skills/  ·  .claude/skills/[/dim]",
                timeout=5,
            )
            return
        lines = ["[bold]Skills[/bold]  [dim]— /skill:name [args] to invoke[/dim]"]
        for s in self._discovered_skills:
            desc = (s.get("description") or "")[:60]
            src  = s.get("source", "")
            lines.append(
                f"  [cyan]/skill:{s['name']}[/cyan]  [dim]{desc}[/dim]"
                + (f"  [dim italic]({src})[/dim italic]" if src else "")
            )
        self.notify("\n".join(lines), timeout=10)

    async def _cmd_invoke_skill(self, skill_name: str, args: str) -> None:
        import asyncio as _asyncio
        from rai.skills.invocation import resolve_slash_command
        raw = f"/{skill_name}" + (f" {args}" if args else "")
        try:
            result = await _asyncio.to_thread(resolve_slash_command, raw, self._agent)
        except ValueError as exc:
            self._notify_error(str(exc))
            return
        if result is None:
            self._notify_error(f"Skill not found: {skill_name!r}")
            return
        container = self.query_one("#messages", Container)
        await container.mount(UserMsg(f"/skill:{result.skill_name}"))
        await self._start_run(result.prompt, skill_allowed_tools=result.allowed_tools)

    def action_open_editor(self) -> None:
        self.run_worker(self._cmd_open_editor(), exclusive=False, name="editor")

    async def _cmd_open_editor(self) -> None:
        import os
        import shlex
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        try:
            current = self.query_one("#input-bar", InputBar).query_one("#chat-input").value
        except Exception:
            current = ""

        cmd_str = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        cmd = shlex.split(cmd_str)
        _GUI_WAIT = {
            "code": "--wait", "cursor": "--wait", "zed": "--wait",
            "subl": "-w", "windsurf": "--wait", "atom": "--wait",
        }
        exe = Path(cmd[0]).stem
        if exe in _GUI_WAIT and _GUI_WAIT[exe] not in cmd:
            cmd.append(_GUI_WAIT[exe])

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".md", prefix="rai-edit-", delete=False, mode="w") as f:
                tmp_path = f.name
                f.write(current)
            rc = None
            with self.suspend():
                rc = subprocess.run(
                    cmd + [tmp_path], stdin=sys.stdin,
                    stdout=sys.stdout, stderr=sys.stderr,
                ).returncode
            if rc == 0:
                edited = Path(tmp_path).read_text(encoding="utf-8").removesuffix("\n")
                if edited.strip():
                    self.query_one("#input-bar", InputBar).query_one("#chat-input").value = edited
        except Exception as exc:
            self._notify_error(f"Editor error: {exc}")
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _open_url(self, url: str, label: str) -> None:
        import webbrowser
        webbrowser.open(url)
        self.notify(f"Opening {label} in browser…", timeout=3)

    def _cmd_tokens(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        ti, to = sb.tokens_in, sb.tokens_out
        total = ti + to

        def _fmt(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.2f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}k"
            return str(n)

        if total == 0:
            hint = (
                "(run in progress — tokens shown after completion)"
                if self._active_run_id
                else "(no runs completed in this session)"
            )
            self.notify(
                f"[bold]Token Usage[/bold]\n  No usage recorded yet\n  [dim]{hint}[/dim]",
                timeout=5,
            )
            return

        # Notification popup
        self.notify(
            f"[bold]Token Usage[/bold]\n"
            f"  ↓ in:   [cyan]{_fmt(ti)}[/cyan]\n"
            f"  ↑ out:  [cyan]{_fmt(to)}[/cyan]\n"
            f"  total:  [cyan]{_fmt(total)}[/cyan]",
            timeout=6,
        )
        # Also print to chat with full brightness
        container = self.query_one("#messages", Container)
        self.call_after_refresh(
            container.mount,
            Static(
                f"[bold]Token Usage[/bold]\n"
                f"  [yellow]↓[/yellow] in:   [cyan]{_fmt(ti)}[/cyan]  [dim]({ti:,})[/dim]\n"
                f"  [yellow]↑[/yellow] out:  [cyan]{_fmt(to)}[/cyan]  [dim]({to:,})[/dim]\n"
                f"  [yellow]Σ[/yellow] total: [cyan]{_fmt(total)}[/cyan]  [dim]({total:,})[/dim]",
                classes="sys-msg",
            ),
        )

    def _cmd_findings(self) -> None:
        """Toggle the findings panel for the current thread."""
        try:
            panel = self.query_one("#findings-panel", FindingsPanel)
            panel.toggle()
        except Exception as exc:
            self._notify_error(f"findings panel error: {exc}")

    # ──────────────────────────────────────────────────────────────────────
    # /create-agent wizard
    # ──────────────────────────────────────────────────────────────────────

    async def _wizard_ask(
        self,
        question: str,
        question_type: str = "text",
        choices: list[str] | None = None,
        placeholder: str = "Type here…",
    ) -> str | None:
        """Mount a WizardStepWidget, await the user's answer, return it (None = cancelled)."""
        fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        widget = WizardStepWidget(
            question=question,
            question_type=question_type,
            choices=choices,
            placeholder=placeholder,
            answer_future=fut,
        )
        container = self.query_one("#messages", Container)
        await container.mount(widget)
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
        return await fut

    async def _rai_start_create_wizard(self) -> None:
        if self._agent_wizard is not None:
            self.notify("A wizard is already running. Press Esc to cancel it first.", timeout=4)
            return
        if self._active_run_id:
            self.notify("Cannot start wizard during an active run.", timeout=3)
            return

        self._agent_wizard = "running"
        self.query_one("#input-bar", InputBar).set_disabled(True)

        container = self.query_one("#messages", Container)
        await container.mount(
            Static(
                "[bold $accent]  Create Agent Wizard[/bold $accent]\n"
                "[dim]Answer a few questions to build and register a new RAI subagent.\n"
                "Press Esc at any step to cancel.[/dim]",
                classes="sys-msg",
            )
        )
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

        try:
            await self._run_wizard_coroutine()
        finally:
            was_delegated = self._agent_wizard == "delegated"
            self._agent_wizard = None
            if not was_delegated:
                self.query_one("#input-bar", InputBar).set_disabled(False)
                try:
                    self.query_one("#input-bar", InputBar).query_one("#chat-input").focus()
                except Exception:
                    pass

    async def _run_wizard_coroutine(self) -> None:
        container = self.query_one("#messages", Container)

        async def _abort(msg: str = "Agent creation cancelled.") -> None:
            await container.mount(Static(f"[dim]{_escape(msg)}[/dim]", classes="sys-msg"))
            self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

        # ── Step 1: Agent name ─────────────────────────────────────────
        name = await self._wizard_ask(
            "Step 1 of 3 — Agent Name\nWhat should your agent be called?",
            question_type="text",
            placeholder="e.g. api-tester, jwt-fuzzer",
        )
        if name is None:
            await _abort(); return
        name = name.strip().lower().replace(" ", "-").replace("_", "-")

        # ── Step 2a: Model ─────────────────────────────────────────────
        model_choice = await self._wizard_ask(
            "Step 2 of 3 — Model\nWhich model should this agent use?",
            question_type="choice",
            choices=["Inherit from RAI (default)", "Enter model string…"],
        )
        if model_choice is None:
            await _abort(); return
        if model_choice.startswith("Enter"):
            model_str = await self._wizard_ask(
                "Model String\nEnter the model identifier:",
                question_type="text",
                placeholder="e.g. anthropic:claude-sonnet-4-6, openai/gpt-4o",
            )
            if model_str is None:
                await _abort(); return
            model = model_str.strip() or "inherit"
        else:
            model = "inherit"

        # ── Step 2b: API key ───────────────────────────────────────────
        api_choice = await self._wizard_ask(
            "API Key\nUse RAI's inherited key or provide a custom one?",
            question_type="choice",
            choices=["Inherit (use RAI's key)", "Enter custom API key…"],
        )
        if api_choice is None:
            await _abort(); return
        if api_choice.startswith("Enter"):
            api_key_val = await self._wizard_ask(
                "Custom API Key\nEnter your API key:",
                question_type="text",
                placeholder="sk-...",
            )
            if api_key_val is None:
                await _abort(); return
            api_key = api_key_val.strip() or "inherit"
        else:
            api_key = "inherit"

        # ── Step 2c: Base URL ──────────────────────────────────────────
        url_choice = await self._wizard_ask(
            "Base URL\nUse the default endpoint or specify a custom one?",
            question_type="choice",
            choices=["Inherit (default)", "Enter custom base URL…"],
        )
        if url_choice is None:
            await _abort(); return
        if url_choice.startswith("Enter"):
            base_url_val = await self._wizard_ask(
                "Custom Base URL\nEnter the base URL for this agent's model:",
                question_type="text",
                placeholder="http://localhost:11434/v1",
            )
            if base_url_val is None:
                await _abort(); return
            base_url = base_url_val.strip() or "inherit"
        else:
            base_url = "inherit"

        # ── Step 3: Specialization ─────────────────────────────────────
        description = await self._wizard_ask(
            "Step 3 of 3 — Agent Specialization\nDescribe what this agent specializes in:",
            question_type="text",
            placeholder="e.g. JWT authentication testing, OAuth2 misconfigurations",
        )
        if description is None:
            await _abort(); return
        description = description.strip()

        # ── Hand off to agent-creator ──────────────────────────────────
        await container.mount(
            Static(
                f"[dim]Handing off to [bold]agent-creator[/bold] "
                f"to build [cyan]{_escape(name)}[/cyan]…[/dim]",
                classes="sys-msg",
            )
        )
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

        brief_lines = [
            f"Agent name: {name}",
            f"One-line description: {description}",
            f"Model: {model}",
        ]
        if api_key not in ("", "inherit"):
            brief_lines.append("API key: (custom — user provided)")
        if base_url not in ("", "inherit"):
            brief_lines.append(f"Base URL: {base_url}")
        brief_lines += [
            "",
            "Specialization (exactly as described by the user):",
            description,
            "",
            "Instructions:",
            f"- Write the full system prompt to /tmp/agents/{name}_agent.md",
            "- Verify line count (must be 1000-2000 lines)",
            "- Register via create_subagent using system_prompt_path",
        ]
        brief = "\n".join(brief_lines)
        delegation = (
            f"Delegate this task to the subagent named 'agent-creator': "
            f"Build and register a new specialized security agent with these requirements:"
            f"\n\n{brief}"
        )
        self._agent_wizard = "delegated"  # signal: don't re-enable input bar in finally
        await self._start_run(delegation)

    async def on_unmount(self) -> None:
        for task in self._pump_tasks.values():
            task.cancel()
        if self._client:
            await self._client.aclose()
