"""MessageCompressionMiddleware — surgical history trim before every model call.

Zero LLM cost. Acts as a pre-filter so SummarizationMiddleware fires less often.
Trims message history only — system_message is never touched.

Calibration: 2.5 chars/token (security tool output is code-dense).
Default budget: 30,000 tokens ≈ 75,000 chars.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


def _char_estimate(msg: Any) -> int:
    """Estimate character count for a single message (used as token_counter)."""
    c = getattr(msg, "content", "")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(
            len(b.get("text", "")) if isinstance(b, dict) else 0
            for b in c
        )
    return 0


class MessageCompressionMiddleware(AgentMiddleware):
    """Trim conversation history before every model call.

    Keeps the most recent messages that fit within the token budget.
    System messages are excluded from trimming (they are never moved or removed).

    Budget is character-based at 2.5 chars/token to match the calibration used
    throughout the RAI stack (same as SummarizationMiddleware token_counter).
    Default: 30,000 tokens → 75,000 characters.

    Fail-open: any exception during trimming passes the original request through
    unchanged so the agent is never blocked.
    """

    def __init__(self, max_history_tokens: int = 30_000) -> None:
        # 2.5 chars per token — calibrated for code-heavy security workloads
        self._max_chars = int(max_history_tokens * 2.5)

    def _trim(self, request: ModelRequest) -> ModelRequest:
        from langchain_core.messages import SystemMessage, trim_messages

        messages = list(request.messages)
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        history_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

        if not history_msgs:
            return request

        # Fast-path: skip trim when already within budget
        total_chars = sum(_char_estimate(m) for m in history_msgs)
        if total_chars <= self._max_chars:
            return request

        try:
            trimmed = trim_messages(
                history_msgs,
                max_tokens=self._max_chars,
                token_counter=_char_estimate,
                strategy="last",
                start_on="human",
                include_system=False,
                allow_partial=False,
            )
            return request.override(messages=system_msgs + trimmed)
        except Exception:
            logger.debug(
                "MessageCompressionMiddleware: trim_messages failed, passing through",
                exc_info=True,
            )
            return request

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        return handler(self._trim(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        return await handler(self._trim(request))
