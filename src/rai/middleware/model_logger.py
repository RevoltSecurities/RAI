"""Model-call debug logger middleware.

Activated by setting RAI_DEBUG_LOG_CALLS=1 in the environment. Logs every
model invocation to a JSONL file, capturing the effective (post-compression)
message list that actually reaches the model. Useful for verifying that
context compaction is working correctly.

Log path defaults to ~/.rai/debug/model-calls.jsonl and can be overridden
with RAI_DEBUG_LOG_FILE.

Log fields:
  ts                — ISO timestamp
  effective_count   — ACCURATE: exact messages sent to model this call
  total_chars       — ACCURATE: total character count of all messages sent
  estimated_tokens  — ACCURATE: estimated tokens (2.5 chars/token)
  compressed        — whether SummarizationMiddleware has fired before
  cutoff_index      — raw message index where summarization cut (if compressed)
  raw_session_total — estimated total messages ever in this session
  truncated_count   — messages that were truncated by ToolResultCompressionMiddleware
  by_type           — message type breakdown {human, ai, tool, system}
  tool_count        — number of tools registered
  messages          — first 200 chars preview of each message sent
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

_CHARS_PER_TOKEN = 2.5


def _msg_chars(msg: Any) -> int:
    """Total character count of a message including tool call args."""
    total = 0
    content = getattr(msg, "content", "") or ""
    if isinstance(content, str):
        total += len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                total += len(block.get("text", ""))

    # Count tool call args (AI messages)
    for tc in getattr(msg, "tool_calls", None) or []:
        args = tc.get("args", {}) if isinstance(tc, dict) else {}
        total += len(json.dumps(args))

    return total


def _is_truncated(msg: Any) -> bool:
    """Detect if ToolResultCompressionMiddleware truncated this message."""
    content = getattr(msg, "content", "") or ""
    if isinstance(content, str) and "chars truncated)" in content:
        return True
    # Check truncated tool call args
    for tc in getattr(msg, "tool_calls", None) or []:
        args = tc.get("args", {}) if isinstance(tc, dict) else {}
        for v in args.values():
            if isinstance(v, str) and "(truncated)" in v:
                return True
    return False


class ModelCallLoggerMiddleware(AgentMiddleware):
    """Log every model call with accurate counts to a JSONL file."""

    def __init__(self, log_path: str | Path | None = None) -> None:
        super().__init__()
        resolved = log_path or os.environ.get(
            "RAI_DEBUG_LOG_FILE",
            str(Path.home() / ".rai" / "debug" / "model-calls.jsonl"),
        )
        self._log_path = Path(resolved)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _build_entry(self, request: ModelRequest) -> dict:
        msgs = request.messages or []
        effective_count = len(msgs)  # ACCURATE — exact count sent to model

        # Token / char stats
        total_chars = sum(_msg_chars(m) for m in msgs)
        estimated_tokens = int(total_chars / _CHARS_PER_TOKEN)

        # Summarization state
        summ_event = (request.state or {}).get("_summarization_event") or {}
        cutoff_index: int = summ_event.get("cutoff_index", 0) if summ_event else 0
        compressed = bool(summ_event)
        # raw_session_total: cutoff (summarized msgs) + current effective
        # subtract 1 for the summary msg itself when compressed
        raw_session_total = (cutoff_index + effective_count - 1) if compressed else effective_count

        # Message type breakdown
        by_type: dict[str, int] = {}
        for m in msgs:
            t = getattr(m, "type", type(m).__name__)
            by_type[t] = by_type.get(t, 0) + 1

        # Truncation stats from ToolResultCompressionMiddleware
        truncated_count = sum(1 for m in msgs if _is_truncated(m))

        return {
            "ts":               datetime.now().isoformat(),
            # ── Accurate counts ──────────────────────────────────────────────
            "effective_count":  effective_count,    # exact messages sent to model
            "total_chars":      total_chars,         # exact chars sent
            "estimated_tokens": estimated_tokens,    # total_chars / 2.5
            # ── Session history ──────────────────────────────────────────────
            "compressed":       compressed,
            "cutoff_index":     cutoff_index if compressed else None,
            "raw_session_total": raw_session_total,  # total msgs ever in session
            # ── Compression stats ────────────────────────────────────────────
            "truncated_count":  truncated_count,     # msgs truncated by ToolResultCompression
            "by_type":          by_type,              # {human:N, ai:N, tool:N, system:N}
            "tool_count":       len(request.tools or []),
            # ── Per-message preview ──────────────────────────────────────────
            "messages": [
                {
                    "type":     getattr(m, "type", type(m).__name__),
                    "chars":    _msg_chars(m),
                    "truncated": _is_truncated(m),
                    "preview":  str(getattr(m, "content", ""))[:200],
                }
                for m in msgs
            ],
        }

    def _write(self, entry: dict) -> None:
        try:
            with self._log_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._write(self._build_entry(request))
        return await handler(request)
