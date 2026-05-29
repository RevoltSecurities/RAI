"""Empty content sanitizer middleware — Bedrock/Anthropic compatibility."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# ---------------------------------------------------------------------------
# Empty content sanitizer — Bedrock/Anthropic compatibility
# ---------------------------------------------------------------------------


class EmptyContentSanitizerMiddleware(AgentMiddleware):
    """Strip empty text content blocks before each model call.

    Bedrock's Anthropic API rejects messages where a text content block has
    an empty string (``{"type": "text", "text": ""}``).  This happens when
    the model issues a tool call with no preceding text: LangChain stores the
    AIMessage with ``content=""`` and the Anthropic adapter converts it to an
    explicit empty text block that Bedrock then refuses.

    This middleware runs closest to the LLM (add it last in the middleware
    list) and normalises messages before they are serialised:
      - ``AIMessage(content="")``     → ``content=[]``
      - list content with empty text blocks → those blocks are dropped
    """

    @staticmethod
    def _clean(messages: list[Any]) -> list[Any]:
        from langchain_core.messages import AIMessage

        out: list[Any] = []
        for msg in messages:
            if isinstance(msg, AIMessage):
                if msg.content == "":
                    msg = msg.model_copy(update={"content": []})
                elif isinstance(msg.content, list):
                    clean = [
                        b for b in msg.content
                        if not (
                            isinstance(b, dict)
                            and b.get("type") == "text"
                            and not (b.get("text") or "").strip()
                        )
                    ]
                    if len(clean) != len(msg.content):
                        msg = msg.model_copy(update={"content": clean})
            out.append(msg)
        return out

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(request.override(messages=self._clean(request.messages)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(request.override(messages=self._clean(request.messages)))
