"""Hooks middleware — fires Claude Code–compatible hooks on tool and model lifecycle events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hooks middleware — Claude Code–compatible hook system
# ---------------------------------------------------------------------------


class HooksMiddleware(AgentMiddleware):
    """Fire Claude Code–compatible hooks on tool and model lifecycle events.

    Reads hook configuration from (in priority order):
      1. ~/.claude/settings.json  — Claude Code native hooks (read-only compat)
      2. ~/.rai/hooks.json        — RAI-native hooks (higher precedence)
      3. extra_config_path        — caller-supplied path (highest precedence)

    PreToolUse hooks are blocking: a non-zero exit code or JSON
    ``{"decision": "block"}`` response aborts the tool call and returns the
    hook's stdout as the error message.  Timeout or spawn failure is fail-open.

    PostToolUse, PreModelCall, and PostModelCall hooks are fire-and-forget
    background tasks — their exit code is ignored.

    Args:
        extra_config_path: Optional path to an additional hooks.json file.
            Its entries are merged on top of the default config (highest
            precedence).  Useful when using RAI as a package with per-agent
            hook overrides.  Not exposed via the CLI.
    """

    def __init__(self, extra_config_path: str | None = None) -> None:
        self._extra_config_path = extra_config_path

    # ------------------------------------------------------------------
    # Extra-path helpers — only called when extra_config_path is set.
    # Runs hooks from the caller-supplied file ON TOP of the global config
    # so per-agent hooks compose correctly in multi-agent deployments.
    # ------------------------------------------------------------------

    def _extra_pre_tool_use(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Fire PreToolUse hooks from extra_config_path; return first blocking decision."""
        import asyncio
        import json
        from rai.hooks.runner import (
            PRE_TOOL_USE, _PRE_HOOK_TIMEOUT, _SESSION_ID,
            _matches, _parse_decision, _read_hooks_from_file, _resolve_argv, _run_hook,
            HookDecision,
        )
        entries = _read_hooks_from_file(self._extra_config_path).get(PRE_TOOL_USE, [])
        if not entries:
            return HookDecision(blocked=False)
        payload = json.dumps({
            "session_id": _SESSION_ID, "hook_event_name": PRE_TOOL_USE,
            "tool_name": tool_name, "tool_input": args,
        }).encode()
        for entry in entries:
            if not _matches(entry.get("matcher"), tool_name):
                continue
            for hook in entry.get("hooks", []):
                if hook.get("type", "command") != "command" or not hook.get("command"):
                    continue
                code, stdout = _run_hook(_resolve_argv(hook["command"]), payload, timeout=_PRE_HOOK_TIMEOUT)
                decision = _parse_decision(code, stdout)
                if decision.blocked:
                    return decision
        return HookDecision(blocked=False)

    def _extra_post_tool_use_bg(self, tool_name: str, args: dict[str, Any], response: str) -> None:
        """Fire PostToolUse hooks from extra_config_path in background."""
        import threading
        from rai.hooks.runner import POST_TOOL_USE, _BG_HOOK_TIMEOUT, _SESSION_ID, _matches, _read_hooks_from_file, _resolve_argv, _run_hook
        import json
        entries = _read_hooks_from_file(self._extra_config_path).get(POST_TOOL_USE, [])
        if not entries:
            return
        payload = json.dumps({
            "session_id": _SESSION_ID, "hook_event_name": POST_TOOL_USE,
            "tool_name": tool_name, "tool_input": args, "tool_response": response,
        }).encode()
        def _run():
            for entry in entries:
                if not _matches(entry.get("matcher"), tool_name):
                    continue
                for hook in entry.get("hooks", []):
                    if hook.get("type", "command") != "command" or not hook.get("command"):
                        continue
                    _run_hook(_resolve_argv(hook["command"]), payload, timeout=_BG_HOOK_TIMEOUT)
        threading.Thread(target=_run, daemon=True).start()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        from rai.hooks.runner import fire_post_tool_use_bg, fire_pre_tool_use

        tool_name = request.tool_call.get("name") or ""
        args: dict[str, Any] = request.tool_call.get("args") or {}

        decision = fire_pre_tool_use(tool_name, args)
        if decision.blocked:
            logger.info("HooksMiddleware: blocked tool '%s': %s", tool_name, decision.reason)
            return ToolMessage(
                content=f"[blocked by PreToolUse hook] {decision.reason}",
                name=tool_name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        if self._extra_config_path:
            decision = self._extra_pre_tool_use(tool_name, args)
            if decision.blocked:
                logger.info("HooksMiddleware: extra hook blocked tool '%s': %s", tool_name, decision.reason)
                return ToolMessage(
                    content=f"[blocked by extra PreToolUse hook] {decision.reason}",
                    name=tool_name,
                    tool_call_id=request.tool_call["id"],
                    status="error",
                )

        result = handler(request)
        content = result.content if hasattr(result, "content") else str(result)
        fire_post_tool_use_bg(tool_name, args, content if isinstance(content, str) else str(content))
        if self._extra_config_path:
            self._extra_post_tool_use_bg(tool_name, args, content if isinstance(content, str) else str(content))
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        import asyncio
        from rai.hooks.runner import afire_pre_tool_use, fire_post_tool_use_bg

        tool_name = request.tool_call.get("name") or ""
        args: dict[str, Any] = request.tool_call.get("args") or {}

        decision = await afire_pre_tool_use(tool_name, args)
        if decision.blocked:
            logger.info("HooksMiddleware: blocked tool '%s': %s", tool_name, decision.reason)
            return ToolMessage(
                content=f"[blocked by PreToolUse hook] {decision.reason}",
                name=tool_name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        if self._extra_config_path:
            decision = await asyncio.to_thread(self._extra_pre_tool_use, tool_name, args)
            if decision.blocked:
                logger.info("HooksMiddleware: extra hook blocked tool '%s': %s", tool_name, decision.reason)
                return ToolMessage(
                    content=f"[blocked by extra PreToolUse hook] {decision.reason}",
                    name=tool_name,
                    tool_call_id=request.tool_call["id"],
                    status="error",
                )

        result = await handler(request)
        content = result.content if hasattr(result, "content") else str(result)
        fire_post_tool_use_bg(tool_name, args, content if isinstance(content, str) else str(content))
        if self._extra_config_path:
            self._extra_post_tool_use_bg(tool_name, args, content if isinstance(content, str) else str(content))
        return result

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        from rai.hooks.runner import POST_MODEL_CALL, PRE_MODEL_CALL, fire_model_event_bg

        fire_model_event_bg(PRE_MODEL_CALL)
        response = handler(request)
        fire_model_event_bg(POST_MODEL_CALL)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        from rai.hooks.runner import POST_MODEL_CALL, PRE_MODEL_CALL, fire_model_event_bg

        fire_model_event_bg(PRE_MODEL_CALL)
        response = await handler(request)
        fire_model_event_bg(POST_MODEL_CALL)
        return response
