"""ReadFileDedupMiddleware — deduplicate file-read tool calls beyond LoopDetection.

LoopDetectionMiddleware (position 3.5) blocks exact duplicate reads within a
10-call sliding window. This middleware (position 3.6) handles the longer tail:
re-reads of the same unchanged file 30+ calls later.

Uses os.stat().st_mtime_ns to detect whether a file changed since the last read.
If unchanged, returns a compact stub (~150 chars) instead of the full content
(2-10k chars). Saves 95%+ tokens per dedup hit.

Fail-open: any exception passes through to normal execution.
Per-run isolation via ContextVar (same pattern as LoopDetectionMiddleware).
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_READ_TOOLS = frozenset({"read_file", "view_file", "Read", "NotebookRead"})

_run_cache: contextvars.ContextVar[
    dict[str, dict[str, tuple[int, int, int, int]]]
] = contextvars.ContextVar("rai_read_dedup_cache", default=None)  # type: ignore[assignment]

_turn_counter: contextvars.ContextVar[
    dict[str, int]
] = contextvars.ContextVar("rai_read_dedup_turn", default=None)  # type: ignore[assignment]


def _get_run_id(request: Any) -> str:
    try:
        cfg = getattr(request, "config", None) or {}
        if isinstance(cfg, dict):
            return cfg.get("configurable", {}).get("thread_id", "") or ""
        return getattr(cfg, "thread_id", "") or ""
    except Exception:
        return ""


def _extract_path(tool_call: dict) -> str:
    args = tool_call.get("args") or {}
    for key in ("file_path", "path", "filename", "file"):
        if key in args and args[key]:
            return str(args[key])
    return ""


class ReadFileDedupMiddleware(AgentMiddleware):
    """Return compact stubs for unchanged file re-reads beyond LoopDetection's window.

    Tracks {file_path → (mtime_ns, turn, line_count, char_count)} per-run.
    On re-read: stat() the file, compare mtime. If unchanged → stub. If changed → execute.

    Position 3.6: fires AFTER LoopDetection (3.5) — only sees calls that
    LoopDetection already allowed through (beyond its 10-call window).
    """

    def __init__(self) -> None:
        self._disabled = os.environ.get("RAI_DEDUP_DISABLED", "0") == "1"

    def _cache_for(self, run_id: str) -> dict[str, tuple[int, int, int, int]]:
        mapping = _run_cache.get(None)
        if mapping is None:
            mapping = {}
            _run_cache.set(mapping)
        elif run_id not in mapping:
            mapping = dict(mapping)
            _run_cache.set(mapping)
        if run_id not in mapping:
            mapping[run_id] = {}
        return mapping[run_id]

    def _turn_for(self, run_id: str) -> int:
        mapping = _turn_counter.get(None)
        if mapping is None:
            mapping = {}
            _turn_counter.set(mapping)
        elif run_id not in mapping:
            mapping = dict(mapping)
            _turn_counter.set(mapping)
        current = mapping.get(run_id, 0) + 1
        mapping[run_id] = current
        return current

    def _get_mtime(self, path: str) -> int | None:
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return None

    def _build_stub(self, path: str, turn: int, lines: int, chars: int) -> str:
        return (
            f'<file-cached path="{path}" read_at_turn={turn} lines={lines} chars={chars}>\n'
            f"File unchanged since turn {turn}. Content already in context above.\n"
            f"Use this result — do NOT re-read. If you need the content, refer to the earlier read result.\n"
            f"</file-cached>"
        )

    def _handle(self, request: Any, handler: Any, is_async: bool = False) -> Any:
        if self._disabled:
            return None

        tc = request.tool_call if hasattr(request, "tool_call") else {}
        if not isinstance(tc, dict):
            return None

        name = tc.get("name", "") or ""
        if name not in _READ_TOOLS:
            return None

        path = _extract_path(tc)
        if not path:
            return None

        run_id = _get_run_id(request)
        cache = self._cache_for(run_id)
        tool_call_id = tc.get("id", "") or ""

        if path in cache:
            prev_mtime, prev_turn, prev_lines, prev_chars = cache[path]
            current_mtime = self._get_mtime(path)
            if current_mtime is not None and current_mtime == prev_mtime:
                self._turn_for(run_id)
                logger.debug(
                    "ReadFileDedupMiddleware: stub for unchanged file %s (turn %d)",
                    path, prev_turn,
                )
                stub = self._build_stub(path, prev_turn, prev_lines, prev_chars)
                return ToolMessage(content=stub, tool_call_id=tool_call_id)

        return None

    def _record(self, path: str, result: Any, run_id: str) -> None:
        if not path:
            return
        content = result.content if isinstance(result, ToolMessage) else str(result)
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        if "error" in content.lower()[:100]:
            return

        mtime = self._get_mtime(path)
        if mtime is None:
            return

        turn = self._turn_for(run_id)
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        chars = len(content)
        cache = self._cache_for(run_id)
        cache[path] = (mtime, turn, lines, chars)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            stub = self._handle(request, handler)
            if stub is not None:
                return stub

            result = handler(request)

            try:
                tc = request.tool_call if hasattr(request, "tool_call") else {}
                if isinstance(tc, dict) and (tc.get("name", "") or "") in _READ_TOOLS:
                    path = _extract_path(tc)
                    run_id = _get_run_id(request)
                    self._record(path, result, run_id)
            except Exception:
                pass

            return result
        except Exception:
            logger.debug("ReadFileDedupMiddleware.wrap_tool_call error", exc_info=True)
            return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            stub = self._handle(request, handler, is_async=True)
            if stub is not None:
                return stub

            result = await handler(request)

            try:
                tc = request.tool_call if hasattr(request, "tool_call") else {}
                if isinstance(tc, dict) and (tc.get("name", "") or "") in _READ_TOOLS:
                    path = _extract_path(tc)
                    run_id = _get_run_id(request)
                    self._record(path, result, run_id)
            except Exception:
                pass

            return result
        except Exception:
            logger.debug("ReadFileDedupMiddleware.awrap_tool_call error", exc_info=True)
            return await handler(request)
