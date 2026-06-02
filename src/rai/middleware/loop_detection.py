"""LoopDetectionMiddleware — prevent degenerate tool call loops.

Detects when the agent issues the exact same tool call (same name + same args)
that it already executed and received a result for within the last N turns.
When detected, short-circuits re-execution and returns the cached result with
a warning that tells the model to accept it and move on.

Root cause this fixes:
  Under context pressure, the agent re-derives the same "next action" from
  degraded context — not realising it already ran that command. Without this
  guard, the same grep/bash command executes 50+ times in a row.

What it does:
  - Maintains a sliding window LRU cache of (tool_name, args_hash) → result
  - On every tool call: checks if identical call was already executed recently
  - If duplicate: returns cached result immediately with ⚠ warning, NO re-exec
  - If new: executes normally and caches the result

Configurable via env vars:
  RAI_LOOP_WINDOW   — recent tool calls to track (default: 10)
  RAI_LOOP_DISABLED — set to 1 to disable (for debugging)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_WINDOW_DEFAULT = 10


def _hash_call(tool_name: str, tool_input: dict) -> str:
    """Stable hash of tool_name + args for deduplication."""
    try:
        key = json.dumps({"n": tool_name, "a": tool_input}, sort_keys=True)
    except (TypeError, ValueError):
        key = f"{tool_name}:{str(tool_input)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class LoopDetectionMiddleware(AgentMiddleware):
    """Prevent the agent from re-executing identical tool calls.

    Maintains a sliding window of recent (hash → result) pairs.
    Duplicate calls within the window return the cached result immediately
    with a warning that instructs the agent to accept the result and move on.

    Fail-open: any exception falls through to normal execution.
    """

    def __init__(self, window: int | None = None) -> None:
        self._window = window or int(os.environ.get("RAI_LOOP_WINDOW", _WINDOW_DEFAULT))
        # LRU: hash → (tool_name, tool_call_id, short_result, count)
        self._cache: OrderedDict[str, tuple[str, str, str, int]] = OrderedDict()
        self._disabled = os.environ.get("RAI_LOOP_DISABLED", "0") == "1"

    def _get_duplicate(
        self, tool_name: str, tool_input: dict, tool_call_id: str
    ) -> ToolMessage | None:
        """Return a cached ToolMessage if this call is a duplicate, else None."""
        if self._disabled:
            return None

        h = _hash_call(tool_name, tool_input)
        if h not in self._cache:
            return None

        _name, _orig_id, short_result, count = self._cache[h]
        self._cache[h] = (_name, _orig_id, short_result, count + 1)
        self._cache.move_to_end(h)

        logger.warning(
            "LoopDetectionMiddleware: duplicate call blocked "
            "(tool=%s, count=%d)", tool_name, count + 1,
        )

        warning = (
            f"⚠ DUPLICATE CALL BLOCKED: '{tool_name}' with these exact arguments "
            f"was already executed {count} time(s) and returned:\n\n"
            f"{short_result}\n\n"
            f"This result is final. Do NOT re-issue this command. "
            f"Accept the result above and proceed to the next step."
        )
        return ToolMessage(content=warning, tool_call_id=tool_call_id)

    def _record(self, tool_name: str, tool_input: dict, tool_call_id: str, result: Any) -> None:
        """Cache this tool call result, evicting oldest if window exceeded."""
        h = _hash_call(tool_name, tool_input)
        short = str(result)[:400] if result is not None else "(no output)"
        self._cache[h] = (tool_name, tool_call_id, short, 1)
        self._cache.move_to_end(h)
        while len(self._cache) > self._window:
            self._cache.popitem(last=False)

    def _extract(self, request: Any) -> tuple[str, dict, str]:
        """Extract (tool_name, tool_input, tool_call_id) from a ToolCallRequest."""
        tc = request.tool_call if hasattr(request, "tool_call") else {}
        if not isinstance(tc, dict):
            tc = {}
        name = tc.get("name", "") or ""
        inp  = tc.get("args", {}) or {}
        tid  = tc.get("id", "") or ""
        return name, inp, tid

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            name, inp, tid = self._extract(request)
            dup = self._get_duplicate(name, inp, tid)
            if dup is not None:
                return dup

            result = handler(request)
            try:
                out = result.content if isinstance(result, ToolMessage) else str(result)
                self._record(name, inp, tid, out)
            except Exception:
                pass
            return result
        except Exception:
            logger.debug("LoopDetectionMiddleware.wrap_tool_call error", exc_info=True)
            return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            name, inp, tid = self._extract(request)
            dup = self._get_duplicate(name, inp, tid)
            if dup is not None:
                return dup

            result = await handler(request)
            try:
                out = result.content if isinstance(result, ToolMessage) else str(result)
                self._record(name, inp, tid, out)
            except Exception:
                pass
            return result
        except Exception:
            logger.debug("LoopDetectionMiddleware.awrap_tool_call error", exc_info=True)
            return await handler(request)
