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

import contextvars
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

# Per-run cache stored in a contextvar so each concurrent run on the same
# compiled graph gets its own isolated LRU window.  The key is thread_id
# (LangGraph's run identifier); the value is an OrderedDict.
#
# Why contextvar and not self._cache: the compiled Pregel graph (and therefore
# this middleware instance) is reused across ALL concurrent runs via AgentPool.
# Storing state on self means run A's tool-call history contaminates run B —
# a legitimate identical grep in run B would be blocked because run A already
# issued it.  contextvars give each asyncio task its own slot automatically.
_run_cache: contextvars.ContextVar[
    dict[str, OrderedDict[str, tuple[str, str, str, int]]]
] = contextvars.ContextVar("rai_loop_detection_cache", default=None)  # type: ignore[assignment]


def _hash_call(tool_name: str, tool_input: dict) -> str:
    """Stable hash of tool_name + args for deduplication."""
    try:
        key = json.dumps({"n": tool_name, "a": tool_input}, sort_keys=True)
    except (TypeError, ValueError):
        key = f"{tool_name}:{str(tool_input)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _get_run_id(request: Any) -> str:
    """Best-effort extraction of the LangGraph thread_id from the request config."""
    try:
        cfg = getattr(request, "config", None) or {}
        if isinstance(cfg, dict):
            return cfg.get("configurable", {}).get("thread_id", "") or ""
        return getattr(cfg, "thread_id", "") or ""
    except Exception:
        return ""


class LoopDetectionMiddleware(AgentMiddleware):
    """Prevent the agent from re-executing identical tool calls.

    Maintains a per-run sliding window of recent (hash → result) pairs.
    Duplicate calls within the window return the cached result immediately
    with a warning that instructs the agent to accept the result and move on.

    The cache is stored in a contextvar keyed by run/thread_id so that
    concurrent runs sharing the same compiled graph remain fully isolated —
    a tool call in run A never blocks an identical legitimate call in run B.

    Fail-open: any exception falls through to normal execution.
    """

    def __init__(self, window: int | None = None) -> None:
        self._window = window or int(os.environ.get("RAI_LOOP_WINDOW", _WINDOW_DEFAULT))
        self._disabled = os.environ.get("RAI_LOOP_DISABLED", "0") == "1"

    # ------------------------------------------------------------------
    # Per-run cache access
    # ------------------------------------------------------------------

    def _cache_for(self, run_id: str) -> OrderedDict[str, tuple[str, str, str, int]]:
        """Return (or lazily create) the per-run LRU OrderedDict.

        asyncio.create_task() copies the ContextVar *slot* but the value is the
        same dict object.  If a child task inherits a non-None mapping from its
        parent, both tasks would write into the same dict — cross-run contamination.
        Copy-on-write: whenever a task first touches the mapping it hasn't itself
        created, replace it with a shallow copy so subsequent writes are isolated.
        """
        mapping = _run_cache.get(None)
        if mapping is None:
            # First access in this task — create a fresh isolated mapping.
            mapping = {}
            _run_cache.set(mapping)
        elif run_id not in mapping:
            # Mapping was inherited from parent task via create_task() context copy.
            # Shallow-copy it so this task's writes don't bleed into the parent.
            mapping = dict(mapping)
            _run_cache.set(mapping)
        if run_id not in mapping:
            mapping[run_id] = OrderedDict()
        return mapping[run_id]

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _get_duplicate(
        self,
        tool_name: str,
        tool_input: dict,
        tool_call_id: str,
        run_id: str,
    ) -> ToolMessage | None:
        """Return a cached ToolMessage if this call is a duplicate, else None."""
        if self._disabled:
            return None

        cache = self._cache_for(run_id)
        h = _hash_call(tool_name, tool_input)
        if h not in cache:
            return None

        _name, _orig_id, short_result, count = cache[h]
        cache[h] = (_name, _orig_id, short_result, count + 1)
        cache.move_to_end(h)

        logger.warning(
            "LoopDetectionMiddleware: duplicate call blocked "
            "(tool=%s, count=%d, run=%s)", tool_name, count + 1, run_id or "?",
        )

        warning = (
            f"⚠ DUPLICATE CALL BLOCKED: '{tool_name}' with these exact arguments "
            f"was already executed {count} time(s) and returned:\n\n"
            f"{short_result}\n\n"
            f"This result is final. Do NOT re-issue this command. "
            f"Accept the result above and proceed to the next step."
        )
        return ToolMessage(content=warning, tool_call_id=tool_call_id)

    def _record(
        self,
        tool_name: str,
        tool_input: dict,
        tool_call_id: str,
        result: Any,
        run_id: str,
    ) -> None:
        """Cache this tool call result, evicting oldest if window exceeded."""
        cache = self._cache_for(run_id)
        h = _hash_call(tool_name, tool_input)
        short = str(result)[:400] if result is not None else "(no output)"
        cache[h] = (tool_name, tool_call_id, short, 1)
        cache.move_to_end(h)
        while len(cache) > self._window:
            cache.popitem(last=False)

    def _extract(self, request: Any) -> tuple[str, dict, str]:
        """Extract (tool_name, tool_input, tool_call_id) from a ToolCallRequest."""
        tc = request.tool_call if hasattr(request, "tool_call") else {}
        if not isinstance(tc, dict):
            tc = {}
        name = tc.get("name", "") or ""
        inp  = tc.get("args", {}) or {}
        tid  = tc.get("id", "") or ""
        return name, inp, tid

    # ------------------------------------------------------------------
    # Middleware intercept points
    # ------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            name, inp, tid = self._extract(request)
            run_id = _get_run_id(request)
            dup = self._get_duplicate(name, inp, tid, run_id)
            if dup is not None:
                return dup

            result = handler(request)
            try:
                out = result.content if isinstance(result, ToolMessage) else str(result)
                self._record(name, inp, tid, out, run_id)
            except Exception:
                pass
            return result
        except Exception:
            logger.debug("LoopDetectionMiddleware.wrap_tool_call error", exc_info=True)
            return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            name, inp, tid = self._extract(request)
            run_id = _get_run_id(request)
            dup = self._get_duplicate(name, inp, tid, run_id)
            if dup is not None:
                return dup

            result = await handler(request)
            try:
                out = result.content if isinstance(result, ToolMessage) else str(result)
                self._record(name, inp, tid, out, run_id)
            except Exception:
                pass
            return result
        except Exception:
            logger.debug("LoopDetectionMiddleware.awrap_tool_call error", exc_info=True)
            return await handler(request)
