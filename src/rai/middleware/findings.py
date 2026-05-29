"""Findings enrichment middleware — injects running findings count as <system-reminder>."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _prepend_reminder(request: ModelRequest, reminder: str) -> ModelRequest:
    """Prepend a <system-reminder> block to the last HumanMessage (ephemeral, not saved)."""
    from langchain_core.messages import HumanMessage

    msgs = list(request.messages)
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return request
    last = msgs[-1]
    tag = f"<system-reminder>\n{reminder}\n</system-reminder>\n\n"
    content = last.content
    if isinstance(content, str):
        new_content = tag + content
    elif isinstance(content, list):
        new_content = [{"type": "text", "text": tag}] + list(content)
    else:
        return request
    msgs[-1] = HumanMessage(content=new_content, additional_kwargs=last.additional_kwargs)
    return request.override(messages=msgs)


class FindingsEnrichmentMiddleware(AgentMiddleware):
    """Inject running findings count as <system-reminder> in the last user message.

    Uses a count-based cache so the reminder text is rebuilt only when count changes.
    Injected ephemerally (not saved to LangGraph state) — never modifies system prompt,
    so the static system prompt prefix always hits the prompt cache.
    """

    def __init__(self) -> None:
        self._cached_count: int = -1
        self._cached_summary: str = ""

    def _findings_summary(self) -> str:
        from rai.tools.core.findings import _get_findings  # lazy import to avoid circular

        findings = _get_findings()
        n = len(findings)
        if n == 0:
            self._cached_count = 0
            self._cached_summary = ""
            return ""
        if n == self._cached_count:
            return self._cached_summary
        counts: dict[str, int] = {}
        for f in findings:
            s = f.get("severity", "unknown")
            counts[s] = counts.get(s, 0) + 1
        parts = [f"{v} {k}" for k, v in counts.items()]
        self._cached_summary = f"Session findings: {n} total — {', '.join(parts)}"
        self._cached_count = n
        return self._cached_summary

    def _inject(self, request: ModelRequest) -> ModelRequest:
        summary = self._findings_summary()
        if not summary:
            return request
        return _prepend_reminder(request, summary)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._inject(request))
