"""StaticSystemPromptCacheBreakpointMiddleware — two-breakpoint prompt caching.

Why this exists
---------------
AnthropicPromptCachingMiddleware (the SDK's tail) stamps a single cache_control
breakpoint at the VERY END of the assembled system message. With RAI's middleware
stack, the end of the system message is the dynamic tail:

    [~16,700-token static prefix]  +  [FindingsEnrichmentMiddleware]  +  [OPPLANMiddleware]
                                                                       ↑ cache_control HERE

When either Findings or OPPLAN changes, the breakpoint content changes → the entire
17,000-token system prompt cache-misses and is re-sent + re-charged on every turn.

This middleware adds a SECOND cache_control breakpoint right at the boundary between
the stable prefix (LocalContextMiddleware and earlier) and the volatile tail, so the
static prefix can be cached independently:

    [~16,700-token static prefix] ← cache_control #1 HERE (this middleware)
    [FindingsEnrichmentMiddleware]
    [OPPLANMiddleware]            ← cache_control #2 HERE (AnthropicPromptCachingMiddleware)

Result: when OPPLAN or Findings change, only the ~700-token tail misses.
The ~16,700-token static prefix still hits. Cost drops by ~95% on volatile turns.

Anthropic supports up to 4 cache_control breakpoints per request — we use 2.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from rai.middleware.prompt_cache import RAIPromptCachingMiddleware

logger = logging.getLogger(__name__)


class StaticSystemPromptCacheBreakpointMiddleware(RAIPromptCachingMiddleware):
    """Stamps a cache_control breakpoint at the current end of system_message.

    Inherits RAIPromptCachingMiddleware so _should_apply_caching supports both
    ChatAnthropic and ChatLiteLLM-Claude (Bedrock) models.

    Must be positioned AFTER all stable-content middlewares (LocalContext, Skills,
    AskUser, Memory) and BEFORE the volatile-tail middlewares (Findings, OPPLAN).
    """

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        if self._should_apply_caching(request):
            request = self._apply_caching(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        if self._should_apply_caching(request):
            request = self._apply_caching(request)
        return await handler(request)

    def _apply_caching(self, request: Any) -> Any:
        # Only stamp the system message — do NOT tag tools here.
        # Tools are tagged once by AnthropicPromptCachingMiddleware at the end.
        try:
            from langchain_anthropic.middleware.prompt_caching import _tag_system_message
        except ImportError:
            return request

        system_message = _tag_system_message(request.system_message, self._cache_control)
        if system_message is not request.system_message:
            return request.override(system_message=system_message)
        return request
