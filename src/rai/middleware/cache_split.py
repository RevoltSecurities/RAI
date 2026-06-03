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

    [~16,700-token static prefix] ← cache_control #1 HERE (this middleware, on system[0])
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
    """Stamps a cache_control breakpoint on system[0] — the large static prefix.

    Tags block 0 explicitly rather than block [-1] so the static prefix is cached
    independently of the dynamic tail added by FindingsEnrichment / OPPLAN.

    Runs unconditionally (no _should_apply_caching gate) — the base class check
    returns False for ChatAnthropic due to a deepagents version quirk. _apply_caching
    is already defensive and no-ops cleanly on non-Anthropic models.

    Must be positioned AFTER all stable-content middlewares (LocalContext, Skills,
    AskUser, Memory) and BEFORE the volatile-tail middlewares (Findings, OPPLAN).
    """

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        request = self._apply_caching(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        request = self._apply_caching(request)
        return await handler(request)

    def _apply_caching(self, request: Any) -> Any:
        # Tag system[0] — the large static prefix — with cache_control.
        # _tag_system_message() tags the LAST block which collides with
        # AnthropicPromptCachingMiddleware's breakpoint on system[-1].
        # Explicitly tagging block 0 keeps both breakpoints independent.
        system_message = request.system_message
        if system_message is None:
            return request

        content = system_message.content
        if not content:
            return request

        try:
            from langchain_core.messages import SystemMessage
            if isinstance(content, str):
                new_content = [{"type": "text", "text": content, "cache_control": self._cache_control}]
            elif isinstance(content, list) and len(content) >= 1:
                new_content = list(content)
                first = new_content[0]
                base = first if isinstance(first, dict) else {"type": "text", "text": str(first)}
                if not base.get("cache_control"):
                    new_content[0] = {**base, "cache_control": self._cache_control}
            else:
                return request

            return request.override(system_message=SystemMessage(content=new_content))
        except Exception as e:
            logger.debug("StaticSystemPromptCacheBreakpointMiddleware: error: %s", e)
            return request
