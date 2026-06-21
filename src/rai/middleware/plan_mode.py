"""PlanModeMiddleware — two-phase enforcement: hard tool blocking in Plan Phase, strict injection in Task Phase."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

BLOCKED_IN_PLAN = frozenset({
    "execute", "bash", "write_file", "edit_file", "write_todos",
})

_REDIRECT_PERMANENT = (
    "write_todos unavailable. Use: enter_plan_mode → write_plan → "
    "enter_step(N) → mark_step_done(N) → exit_plan_mode."
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
    """Two-phase plan enforcement with hard tool blocking.

    Phase 1 (Plan Phase): plan_mode=True, plan_approved=False
      - wrap_tool_call: HARD-BLOCKS destructive tools (bash, write, edit, execute)
      - wrap_model_call: Injects "Plan Phase" workflow reminder
      - Subagents, read, web, scan tools all ALLOWED

    Phase 2 (Task Phase): plan_approved=True
      - wrap_tool_call: ALL tools allowed (only redirects write_todos)
      - wrap_model_call: Injects step enforcement reminder

    Normal Mode: plan_mode=False, plan_approved=False
      - NO injection, NO blocking — completely transparent

    Only added to the HTTP harness parent agent (when disable_opplan=True).
    HTTP subagents do NOT get this middleware.
    """

    # ------------------------------------------------------------------
    # Model call — phase-aware injection
    # ------------------------------------------------------------------

    def _inject_reminder(self, request: ModelRequest) -> ModelRequest:
        if self._is_plan_mode():
            return _prepend_reminder(
                request,
                "<plan-harness-restricted>\n"
                "PHASE: Plan (research only)\n"
                "ENFORCEMENT: HARD — destructive tools (bash, write_file, edit_file, execute) "
                "are BLOCKED by the harness and will return errors.\n"
                "ALLOWED: read_file, grep, glob, web_search, web_fetch, http_request (GET), "
                "Subagent, MultiSubagents, ask_user, list_plan_steps, read_plan.\n"
                "ACTION REQUIRED: Research the task using allowed tools. "
                "When research is complete, call write_plan(title, description, steps) "
                "to submit a structured plan for user approval.\n"
                "DO NOT attempt blocked tools — the harness will reject them.\n"
                "</plan-harness-restricted>",
            )

        if self._is_plan_approved():
            incomplete = self._get_incomplete_steps()
            if not incomplete:
                return _prepend_reminder(
                    request,
                    "<task-harness-notify>\n"
                    "STATUS: All plan steps resolved.\n"
                    "ACTION REQUIRED: Call exit_plan_mode() immediately to finalize "
                    "and trigger self-learning memory phase.\n"
                    "</task-harness-notify>",
                )
            steps_str = ", ".join(str(s["number"]) for s in incomplete)
            return _prepend_reminder(
                request,
                f"<task-harness-enforce>\n"
                f"PHASE: Task Execution\n"
                f"PENDING: {len(incomplete)} step(s) [{steps_str}] — MUST be completed.\n"
                f"ENFORCEMENT: You CANNOT end the conversation or ignore pending steps. "
                f"The harness will re-invoke you until all steps are done or blocked.\n"
                f"WORKFLOW: enter_step(N) → execute work → mark_step_done(N, notes=...) "
                f"OR mark_step_blocked(N, reason=...).\n"
                f"All tools are available. Execute the next pending step now.\n"
                f"</task-harness-enforce>",
            )

        # Normal mode: NO injection whatsoever
        return request

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
    # Tool call — hard blocking in Plan Phase + write_todos redirect
    # ------------------------------------------------------------------

    def _check_plan_block(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return a blocking ToolMessage if tool is forbidden in current phase, else None."""
        tc = request.tool_call if hasattr(request, "tool_call") else {}
        tool_name = tc.get("name", "") or ""
        tool_call_id = tc.get("id", "") or ""

        # write_todos is ALWAYS redirected (plan or task phase)
        if tool_name == "write_todos":
            return ToolMessage(tool_call_id=tool_call_id, content=_REDIRECT_PERMANENT)

        # Only block in Plan Phase (plan_mode=True AND plan_approved=False)
        if not self._is_plan_mode():
            return None

        if tool_name not in BLOCKED_IN_PLAN:
            return None

        return ToolMessage(
            tool_call_id=tool_call_id,
            content=(
                f"<plan-harness-blocked>\n"
                f"REJECTED: '{tool_name}' is not permitted during Plan Phase.\n"
                f"REASON: Destructive/mutating tools are hard-blocked until plan approval.\n"
                f"ALLOWED: read_file, grep, glob, web_search, web_fetch, http_request (GET), "
                f"Subagent, MultiSubagents, ask_user, list_plan_steps, read_plan.\n"
                f"ACTION: Use research tools to gather information, then call "
                f"write_plan(title, description, steps) to submit your plan.\n"
                f"</plan-harness-blocked>"
            ),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            blocked = self._check_plan_block(request)
            if blocked:
                return blocked
        except Exception as exc:
            logger.warning("PlanModeMiddleware: plan block check failed (%s) — allowing tool through", exc)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            blocked = self._check_plan_block(request)
            if blocked:
                return blocked
        except Exception as exc:
            logger.warning("PlanModeMiddleware: plan block check failed (%s) — allowing tool through", exc)
        return await handler(request)

    # ------------------------------------------------------------------
    # State helpers — read from _RUN_REGISTRY via _RUN_CONTEXT
    # ------------------------------------------------------------------

    @staticmethod
    def _is_plan_mode() -> bool:
        """True when plan_mode=True AND plan_approved=False (Plan Phase)."""
        try:
            from rai.harness.subagents.registry import _RUN_CONTEXT
            from rai.harness.runner import _RUN_REGISTRY

            ctx = _RUN_CONTEXT.get()
            if ctx is None:
                return False
            reg = _RUN_REGISTRY.get(ctx["run_id"], {})
            return bool(reg.get("plan_mode")) and not bool(reg.get("plan_approved"))
        except Exception:
            return False

    @staticmethod
    def _is_plan_approved() -> bool:
        """True when plan is approved (Task Phase)."""
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
