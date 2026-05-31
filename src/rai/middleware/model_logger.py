"""Model-call debug logger middleware.

Activated by setting RAI_DEBUG_LOG_CALLS=1 in the environment. Logs every
model invocation to a JSONL file, capturing the effective (post-compression)
message list that actually reaches the model. Useful for verifying that
context compaction is working correctly.

Log path defaults to ~/.rai/debug/model-calls.jsonl and can be overridden
with RAI_DEBUG_LOG_FILE.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse


class ModelCallLoggerMiddleware(AgentMiddleware):
    """Log every model call (message counts, compression state) to a JSONL file."""

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
        effective_count = len(msgs)

        summ_event = (request.state or {}).get("_summarization_event") or {}
        cutoff_index: int = summ_event.get("cutoff_index", 0) if summ_event else 0
        compressed = bool(summ_event)
        recent_kept = (effective_count - 1) if compressed else effective_count
        raw_approx = (cutoff_index + recent_kept) if compressed else effective_count

        return {
            "ts":              datetime.now().isoformat(),
            "effective_count": effective_count,
            "raw_approx":      raw_approx,
            "compressed":      compressed,
            "cutoff_index":    cutoff_index if compressed else None,
            "tool_count":      len(request.tools or []),
            "messages": [
                {
                    "type":            getattr(m, "type", type(m).__name__),
                    "content_preview": str(getattr(m, "content", ""))[:200],
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
