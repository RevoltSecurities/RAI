"""PlanModeMiddleware — plan mode enforcement via system-reminder + write_todos interception."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

_REDIRECT_PERMANENT = (
    "write_todos is not available in this agent. "
    "Use plan-step tools instead: enter_plan_mode(), write_plan(content), "
    "list_plan_steps(), enter_step(N), mark_step_done(N), mark_step_blocked(N, reason), "
    "exit_plan_mode()."
)

_PLAN_MODE_REMINDER = """\
Plan mode is ACTIVE. You MUST NOT call tools that execute, write, or modify state.
Allowed tools: read_file, grep, glob, ls, read_todos, write_plan, list_plan_steps.

BEFORE writing the plan: use ask_user() to clarify requirements. Ask the user:
  • What is the exact goal or target scope?
  • What outcomes do they expect from each phase?
  • Any constraints, exclusions, or priorities they care about?
Build a detailed picture of what they want — then submit your plan via write_plan().
Do NOT write the plan until you have gathered enough context from the user.\
"""

_PLAN_WORKFLOW_REMINDER = (
    "⚑ Plan workflow: call enter_plan_mode() → use ask_user() to gather requirements → "
    "explore with read-only tools → write_plan(content) → wait for approval → execute. "
    "Use ask_user() strictly to clarify scope, goals, and constraints BEFORE writing the plan. "
    "Do NOT call write_todos before a plan has been approved."
)

_EXEC_ALL_DONE_REMINDER = (
    "⚑ All plan steps are complete. "
    "Call exit_plan_mode() to verify completion, then summarise what was accomplished and end the run."
)


def _prepend_reminder(request: ModelRequest, text: str) -> ModelRequest:
    """Prepend a <system-reminder> block to the last HumanMessage (ephemeral, not saved)."""
    from langchain_core.messages import HumanMessage

    msgs = list(request.messages)
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return request
    last = msgs[-1]
    tag = f"<system-reminder>\n{text}\n</system-reminder>\n\n"
    content = last.content
    if isinstance(content, str):
        new_content = tag + content
    elif isinstance(content, list):
        new_content = [{"type": "text", "text": tag}] + list(content)
    else:
        return request
    msgs[-1] = HumanMessage(content=new_content, additional_kwargs=last.additional_kwargs)
    return request.override(messages=msgs)


class PlanModeMiddleware(AgentMiddleware):
    """Plan mode enforcement: proactive reminder + write_todos interception.

    Two-layer approach matching Claude Code's trust-based EnterPlanMode:
    1. wrap_model_call: injects <system-reminder> into the last user message each turn
       so the LLM knows upfront it must not execute tools. System prompt never touched.
       All tools remain loaded — no tool swapping (cache-safe).
    2. wrap_tool_call: intercepts write_todos and redirects to write_plan() as a hard
       backstop if the LLM tries to execute anyway.

    Only added to the HTTP harness parent agent (when disable_opplan=True).
    HTTP subagents compiled by executor.py do NOT get this middleware because
    they call create_rai_agent() without disable_opplan=True — so subagents
    can call write_todos freely for their own task tracking.
    """

    # ------------------------------------------------------------------
    # Model call — proactive per-turn reminder (layer 1)
    # ------------------------------------------------------------------

    def _inject_reminder(self, request: ModelRequest) -> ModelRequest:
        if self._is_plan_mode():
            reminder = _PLAN_MODE_REMINDER
        elif self._is_plan_approved():
            all_steps = self._get_all_plan_steps()
            incomplete = [s for s in all_steps if s.get("status") not in ("done", "blocked")]
            if incomplete:
                reminder = self._make_exec_steps_reminder(incomplete)
            elif all_steps:
                # All parsed steps are done
                reminder = _EXEC_ALL_DONE_REMINDER
            else:
                # Plan had no parseable numbered/bulleted steps — guide execution
                reminder = (
                    "⚑ Plan approved. Execute the plan now. "
                    "Call mark_step_done(N) after each numbered step you complete. "
                    "Do NOT end the run until the plan is fully executed."
                )
        else:
            reminder = _PLAN_WORKFLOW_REMINDER
        return _prepend_reminder(request, reminder)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], object],
    ) -> object:
        return handler(self._inject_reminder(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[object]],
    ) -> object:
        return await handler(self._inject_reminder(request))

    # ------------------------------------------------------------------
    # Tool call — hard backstop interception (layer 2)
    # ------------------------------------------------------------------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "write_todos":
            return handler(request)
        return ToolMessage(tool_call_id=request.tool_call.get("id", ""), content=_REDIRECT_PERMANENT)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "write_todos":
            return await handler(request)
        return ToolMessage(tool_call_id=request.tool_call.get("id", ""), content=_REDIRECT_PERMANENT)

    @staticmethod
    def _is_plan_mode() -> bool:
        """Return True only when the current run (from _RUN_CONTEXT) has plan_mode=True."""
        try:
            from rai.harness.subagents.registry import _RUN_CONTEXT
            from rai.harness.runner import _RUN_REGISTRY

            ctx = _RUN_CONTEXT.get()
            if ctx is None:
                return False
            return bool(_RUN_REGISTRY.get(ctx["run_id"], {}).get("plan_mode"))
        except Exception:
            return False

    @staticmethod
    def _is_plan_approved() -> bool:
        """Return True when the current run has an approved plan (execution phase)."""
        try:
            from rai.harness.subagents.registry import _RUN_CONTEXT
            from rai.harness.runner import _RUN_REGISTRY

            ctx = _RUN_CONTEXT.get()
            if ctx is None:
                return False
            return bool(_RUN_REGISTRY.get(ctx["run_id"], {}).get("plan_approved"))
        except Exception:
            return False

    @staticmethod
    def _get_all_plan_steps() -> list[dict]:
        """Return all plan steps (pending or done)."""
        try:
            from rai.harness.subagents.registry import _RUN_CONTEXT
            from rai.harness.runner import _RUN_REGISTRY

            ctx = _RUN_CONTEXT.get()
            if ctx is None:
                return []
            return list(_RUN_REGISTRY.get(ctx["run_id"], {}).get("plan_steps", []))
        except Exception:
            return []

    @staticmethod
    def _get_incomplete_steps() -> list[dict]:
        """Return plan steps whose status is not 'done' or 'blocked'."""
        try:
            from rai.harness.subagents.registry import _RUN_CONTEXT
            from rai.harness.runner import _RUN_REGISTRY

            ctx = _RUN_CONTEXT.get()
            if ctx is None:
                return []
            steps = _RUN_REGISTRY.get(ctx["run_id"], {}).get("plan_steps", [])
            return [s for s in steps if s.get("status") not in ("done", "blocked")]
        except Exception:
            return []

    @staticmethod
    def _make_exec_steps_reminder(incomplete: list[dict]) -> str:
        lines = ["⚑ Plan approved. Execute steps in order. Incomplete steps:"]
        for s in incomplete:
            label = s.get("title") or s.get("description", "—")
            lines.append(f"  {s['number']}. {label}")
        lines.append(
            "Before starting each step call enter_step(step_number) to mark it in_progress "
            "(it will show you the step description and 🔧 approach reminder), "
            "then call mark_step_done(step_number) when complete. "
            "If a step cannot proceed call mark_step_blocked(step_number, reason). "
            "When ALL steps are done or blocked call exit_plan_mode() before ending the run."
        )
        return "\n".join(lines)
