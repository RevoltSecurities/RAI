"""ToolResultCompressionMiddleware — truncate old tool results/args before model call.

Sits between MessageCompressionMiddleware (layer 10.5) and SummarizationMiddleware
(layer 11). Zero LLM cost. Runs on every model call.

What it compacts:
  - Old ToolMessage results from bash/grep/file/http tools (>500 chars)
    → keep first 1500 chars + "\n...(+N chars truncated)"
  - Old AI tool_call args for bash commands (>600 chars)
    → keep first 600 chars + "...(truncated)"
  - Old AI tool_call args for findings_add description (>250 chars)
    → keep first 250 chars + "...(recorded in findings store)"

What it NEVER touches:
  - Any HumanMessage — <system-reminder> tags carry plan enforcement,
    subagent notifications, findings counts, HITL signals
  - SystemMessage — system prompt is always preserved
  - The last `keep_recent` messages (default: 20) — verbatim for agent reasoning
  - ToolMessage results from plan, findings, memory, ask_user tools
  - Any result already short (< min_chars, default: 500 chars)

Env vars for tuning:
  RAI_COMPACT_RESULT_KEEP      — last N messages never touched (default: 20)
  RAI_COMPACT_RESULT_MAX       — max chars for tool results (default: 1500)
  RAI_COMPACT_CMD_MAX          — max chars for bash command args (default: 600)
  RAI_COMPACT_FINDINGS_ARG_MAX — max chars for findings_add description (default: 250)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

# ── Tools whose ToolMessage results must never be truncated ──────────────────

_NEVER_TRUNCATE_TOOL_RESULTS = frozenset({
    # Plan harness — all 7 tools
    "enter_plan_mode", "write_plan", "list_plan_steps",
    "enter_step", "mark_step_done", "mark_step_blocked", "exit_plan_mode",
    # Findings — agent's recorded security discoveries
    "findings_add", "findings_list", "findings_export",
    # Memory — agent's persistent knowledge base
    "memory_read", "memory_write", "memory_update", "memory_path",
    "memory_files_list",
    # HITL — ask_user answers are decisions, not bulk data
    "ask_user",
    # Compact conversation — summarization control
    "compact_conversation",
})

# ── Tools whose AI tool_call ARGS must never be bulk-truncated ───────────────
# (findings_add gets special partial truncation — only description field)

_NEVER_TRUNCATE_ARGS = frozenset({
    "memory_write", "memory_update", "ask_user",
    "enter_plan_mode", "write_plan", "enter_step",
    "mark_step_done", "mark_step_blocked", "exit_plan_mode",
})

# ── Bash / shell tool names (command arg truncation target) ──────────────────

_BASH_TOOLS = frozenset({
    "bash", "shell", "run_bash", "execute_command", "terminal",
    "Bash", "run_command", "execute", "cmd", "Run",
})


class ToolResultCompressionMiddleware(AgentMiddleware):
    """Truncate old tool results and bash command args before every model call.

    Only operates on messages older than `keep_recent` from the end of the
    message list. Recent messages are never touched so the agent has full
    verbatim context for its current work.

    Fail-open: any exception passes the original request through unchanged.
    """

    def __init__(
        self,
        keep_recent: int = 20,
        max_result_chars: int = 1500,
        max_cmd_chars: int = 600,
        max_findings_arg_chars: int = 250,
        min_chars: int = 500,
    ) -> None:
        self._keep_recent = keep_recent
        self._max_result_chars = max_result_chars
        self._max_cmd_chars = max_cmd_chars
        self._max_findings_arg_chars = max_findings_arg_chars
        self._min_chars = min_chars

    def _compress(self, request: ModelRequest) -> ModelRequest:
        msgs = list(request.messages or [])
        if not msgs:
            return request

        # Only touch messages older than keep_recent from the end
        cutoff = max(0, len(msgs) - self._keep_recent)
        if cutoff == 0:
            return request

        changed = False
        new_msgs = list(msgs)

        for i in range(cutoff):
            msg = new_msgs[i]

            # ── HumanMessage — NEVER touch ────────────────────────────────────
            if isinstance(msg, HumanMessage):
                continue

            # ── SystemMessage — NEVER touch ───────────────────────────────────
            if isinstance(msg, SystemMessage):
                continue

            # ── ToolMessage — truncate large results from data tools ──────────
            if isinstance(msg, ToolMessage):
                tool_name = getattr(msg, "name", "") or ""
                if tool_name in _NEVER_TRUNCATE_TOOL_RESULTS:
                    continue
                content = msg.content
                if not isinstance(content, str):
                    content = str(content) if content is not None else ""
                if len(content) > self._min_chars:
                    kept = content[:self._max_result_chars]
                    overflow = len(content) - self._max_result_chars
                    suffix = f"\n...(+{overflow} chars truncated)"
                    new_msgs[i] = ToolMessage(
                        content=kept + suffix,
                        tool_call_id=msg.tool_call_id,
                        name=tool_name,
                    )
                    changed = True
                continue

            # ── AIMessage — truncate bash command args + findings_add desc ────
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                if not tool_calls:
                    continue

                new_tool_calls = []
                tc_changed = False

                for tc in tool_calls:
                    name = tc.get("name", "") or ""
                    args = dict(tc.get("args") or {})

                    # findings_add: truncate description field only
                    if name == "findings_add":
                        desc = str(args.get("description", ""))
                        if len(desc) > self._max_findings_arg_chars:
                            args["description"] = (
                                desc[:self._max_findings_arg_chars]
                                + "...(recorded in findings store)"
                            )
                            tc_changed = True
                        new_tool_calls.append({**tc, "args": args})
                        continue

                    # Never bulk-truncate these tools' args
                    if name in _NEVER_TRUNCATE_ARGS:
                        new_tool_calls.append(tc)
                        continue

                    # Bash/shell: truncate command arg specifically
                    if name in _BASH_TOOLS or name.lower() in {t.lower() for t in _BASH_TOOLS}:
                        cmd = str(args.get("command", ""))
                        if len(cmd) > self._max_cmd_chars:
                            args["command"] = cmd[:self._max_cmd_chars] + "...(truncated)"
                            tc_changed = True
                        new_tool_calls.append({**tc, "args": args})
                        continue

                    # Unknown / other tools: truncate any individual string arg > max_cmd_chars.
                    # This covers nuclei_scan, http_request, jwt_decode, MCP tools, etc.
                    # Each arg value is checked independently — only large strings are trimmed.
                    for k, v in list(args.items()):
                        if isinstance(v, str) and len(v) > self._max_cmd_chars:
                            args[k] = v[:self._max_cmd_chars] + "...(truncated)"
                            tc_changed = True

                    new_tool_calls.append({**tc, "args": args})

                if tc_changed:
                    # Rebuild AIMessage preserving all original fields
                    new_msgs[i] = msg.copy(update={"tool_calls": new_tool_calls})
                    changed = True

        if not changed:
            return request

        return request.override(messages=new_msgs)

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        try:
            return handler(self._compress(request))
        except Exception:
            logger.debug(
                "ToolResultCompressionMiddleware: compression failed, passing through",
                exc_info=True,
            )
            return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        try:
            return await handler(self._compress(request))
        except Exception:
            logger.debug(
                "ToolResultCompressionMiddleware: compression failed, passing through",
                exc_info=True,
            )
            return await handler(request)
