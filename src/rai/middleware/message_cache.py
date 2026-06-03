"""LastHumanMessageCacheMiddleware — caches the last human message.

Claude Code stamps cache_control: ephemeral on the last content block of the last
user message so the full conversation history up to that point is cached on the next
turn. Without this, every turn re-processes the entire message history at full cost.

This middleware replicates that behavior: finds the last HumanMessage and adds
cache_control to its last content block. The <system-reminder> injection blocks
(skills, memory, plan enforcement) that get prepended to the same message are NOT
cached — they change every turn so caching them would constantly bust the cache.
Only the actual user text at the end gets the ephemeral breakpoint.

Run innermost (after all other middleware have finished injecting content) so it
sees the fully assembled message list.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_CACHE_CONTROL = {"type": "ephemeral"}


def _tag_last_human_message(request: ModelRequest) -> ModelRequest:
    messages = list(request.messages)
    if not messages:
        return request

    # Find the last HumanMessage index
    last_human_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break

    if last_human_idx is None:
        return request

    msg = messages[last_human_idx]
    content = msg.content

    if isinstance(content, str):
        new_content: list[Any] = [{"type": "text", "text": content, "cache_control": _CACHE_CONTROL}]
    elif isinstance(content, list) and content:
        new_content = list(content)
        last_block = new_content[-1]
        if isinstance(last_block, dict) and not last_block.get("cache_control"):
            new_content[-1] = {**last_block, "cache_control": _CACHE_CONTROL}
        else:
            return request  # already tagged or not a dict block
    else:
        return request

    messages[last_human_idx] = HumanMessage(content=new_content)
    return request.override(messages=messages)


class LastHumanMessageCacheMiddleware(AgentMiddleware):
    """Stamps cache_control: ephemeral on the last HumanMessage's last content block.

    Caches the full conversation history up to the latest human turn so subsequent
    turns don't re-process the entire history. Matches Claude Code's caching strategy.
    """

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_tag_last_human_message(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(_tag_last_human_message(request))
