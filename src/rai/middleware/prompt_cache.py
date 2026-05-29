"""Prompt caching middleware — thin subclass of AnthropicPromptCachingMiddleware.

Applies to:
  • Direct ChatAnthropic instances (Anthropic API).
  • ChatLiteLLM instances whose model string contains "claude" — covers both
    Anthropic API and Bedrock backends. LiteLLM ≥ 1.49 translates
    cache_control content blocks for Bedrock automatically.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_anthropic.chat_models import ChatAnthropic

# Set RAI_DISABLE_PROMPT_CACHE=1 to turn off caching for all models (Anthropic, LiteLLM, Bedrock).
_DISABLE_ENV = "RAI_DISABLE_PROMPT_CACHE"


class RAIPromptCachingMiddleware(AnthropicPromptCachingMiddleware):
    """Prompt caching middleware for ChatAnthropic and ChatLiteLLM-Claude models.

    Bedrock Claude models are included — LiteLLM translates cache_control blocks
    to Bedrock's format transparently. If a Bedrock model does not support caching
    the blocks are silently ignored by the API.

    Set RAI_DISABLE_PROMPT_CACHE=1 to disable caching for all models.
    """

    def _should_apply_caching(self, request: Any) -> bool:
        if os.environ.get(_DISABLE_ENV, "").strip().lower() in ("1", "true", "yes"):
            return False
        if isinstance(request.model, ChatAnthropic):
            return super()._should_apply_caching(request)
        # LiteLLM proxy routing to any Claude model — Anthropic API or Bedrock.
        # NOTE: cannot call super() here — base class checks isinstance(ChatAnthropic)
        # and returns False for all other types. Replicate the message count check directly.
        try:
            from langchain_litellm import ChatLiteLLM
            if isinstance(request.model, ChatLiteLLM):
                model_str = (getattr(request.model, "model", "") or "").lower()
                if "claude" in model_str:
                    messages_count = (
                        len(request.messages) + 1
                        if request.system_message
                        else len(request.messages)
                    )
                    return messages_count >= self.min_messages_to_cache
        except ImportError:
            pass
        return False
