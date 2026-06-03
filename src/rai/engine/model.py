"""LLM model resolution helpers for RAI.

Provides _build_llm(), build_model(), ModelConfig, and list_providers()
for resolving model strings to BaseChatModel instances.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

DEFAULT_AGENT_NAME = "rai"
DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Bedrock proxy routing
# ---------------------------------------------------------------------------

# Maps Anthropic shortnames to the bedrock-*(US) aliases a LiteLLM proxy expects.
# When base_url is set and provider is "anthropic" we route through ChatLiteLLM
# using one of these names so the proxy does not reject with 401.
_ANTHROPIC_TO_BEDROCK_US: dict[str, str] = {
    "claude-opus-4-7":            "bedrock-claude-opus-4.7-(US)",
    "claude-opus-4-6":            "bedrock-claude-opus-4.6-(US)",
    "claude-sonnet-4-6":          "bedrock-claude-sonnet-4.6-(US)",
    "claude-sonnet-4-5":          "bedrock-claude-sonnet-4.5-(US)",
    "claude-sonnet-4-5-20251001": "bedrock-claude-sonnet-4.5-(US)",
    "claude-haiku-4-5":           "bedrock-claude-haiku-4.5-(US)",
    "claude-haiku-4-5-20251001":  "bedrock-claude-haiku-4.5-(US)",
}

# Provider-level credential environment variable lookup tables.
_PROVIDER_KEY_ENVS: dict[str, list[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai":    ["OPENAI_API_KEY"],
    "google":    ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "mistral":   ["MISTRAL_API_KEY"],
    "cohere":    ["COHERE_API_KEY"],
    "groq":      ["GROQ_API_KEY"],
    "together":  ["TOGETHER_API_KEY"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "litellm":   ["LITELLM_API_KEY"],
}

_PROVIDER_BASE_ENVS: dict[str, list[str]] = {
    "anthropic": ["ANTHROPIC_BASE_URL", "LITELLM_BASE_URL"],
    "openai":    ["OPENAI_BASE_URL", "OPENAI_API_BASE"],
    "litellm":   ["LITELLM_BASE_URL"],
}


# ---------------------------------------------------------------------------
# Public SDK types
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    """Configuration bundle passed to build_model()."""

    model: str = DEFAULT_MODEL
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_litellm_format(model_str: str) -> bool:
    """Return True for LiteLLM model strings (contain '/' or 'litellm:' prefix)."""
    return model_str.startswith("litellm:") or "/" in model_str


def _resolve_key(provider: str, explicit: str) -> str:
    if explicit:
        return explicit
    for env in _PROVIDER_KEY_ENVS.get(provider, []):
        v = os.environ.get(env, "")
        if v:
            return v
    return ""


def _resolve_base_url(provider: str, explicit: str) -> str:
    if explicit:
        return explicit
    for env in _PROVIDER_BASE_ENVS.get(provider, []):
        v = os.environ.get(env, "")
        if v:
            return v
    return ""


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------


def _is_claude_model(model_str: str) -> bool:
    """Return True if model_str refers to a Claude/Anthropic model."""
    s = model_str.lower()
    return "claude" in s or "anthropic" in s


def _build_llm(
    model: str | BaseChatModel,
    *,
    api_key: str = "",
    base_url: str = "",
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Resolve a model string to a BaseChatModel.

    Routing rules (in priority order):
      1. Already a BaseChatModel → return as-is.
      2. 'chatanthropic:<model>' prefix → ChatAnthropic directly (proper /v1/messages
         wire format, cache_control preserved, thinking support).
      3. 'litellm:<model>' prefix or '<provider>/<model>' where model is Claude
         → upgrade to ChatAnthropic for prompt caching. Falls back to ChatLiteLLM
         if langchain-anthropic is not installed.
      4. 'litellm:<model>' or '<provider>/<model>' (non-Claude) → ChatLiteLLM.
      5. 'anthropic:<model>' + base_url set → proxy routing via ChatAnthropic
         (was ChatLiteLLM — upgraded to preserve cache_control).
      6. Any '<provider>:<model>' → init_chat_model with credentials.
    """
    from langchain_core.language_models import BaseChatModel as _Base

    if isinstance(model, _Base):
        return model

    model_str: str = model

    # chatanthropic: prefix — force ChatAnthropic directly (proper /v1/messages
    # wire format, cache_control preserved, extended thinking support).
    if model_str.startswith("chatanthropic:"):
        raw = model_str[len("chatanthropic:"):]
        return _make_chat_anthropic(raw, api_key=api_key, base_url=base_url,
                                    temperature=temperature, max_tokens=max_tokens)

    # LiteLLM prefix strip
    if model_str.startswith("litellm:"):
        model_str = model_str[len("litellm:"):]

    if _is_litellm_format(model_str):
        # Upgrade Claude models to ChatAnthropic for prompt caching.
        # LiteLLM routes to /chat/completions which strips cache_control — no caching.
        # ChatAnthropic routes to /v1/messages which preserves cache_control.
        # Falls back to ChatLiteLLM if langchain-anthropic is unavailable.
        if _is_claude_model(model_str):
            try:
                return _make_chat_anthropic(model_str, api_key=api_key, base_url=base_url,
                                            temperature=temperature, max_tokens=max_tokens)
            except ImportError:
                pass  # langchain-anthropic not installed, fall through to ChatLiteLLM
        return _make_litellm(model_str, api_key=api_key, base_url=base_url,
                             temperature=temperature, max_tokens=max_tokens)

    # Detect provider:model format
    provider = ""
    model_name = model_str
    if ":" in model_str:
        provider, model_name = model_str.split(":", 1)

    effective_base = _resolve_base_url(provider, base_url)
    effective_key  = _resolve_key(provider, api_key)

    # Route anthropic models through ChatAnthropic (was ChatLiteLLM).
    # ChatAnthropic preserves cache_control on /v1/messages; ChatLiteLLM strips it.
    if provider == "anthropic" and effective_base:
        try:
            return _make_chat_anthropic(model_name, api_key=effective_key, base_url=effective_base,
                                        temperature=temperature, max_tokens=max_tokens)
        except ImportError:
            # langchain-anthropic not installed — fall back to LiteLLM proxy routing
            bedrock_name = _ANTHROPIC_TO_BEDROCK_US.get(model_name, model_name)
            prefix = os.environ.get("RAI_LITELLM_PROXY_PREFIX", "openai")
            litellm_model = f"{prefix}/{bedrock_name}"
            return _make_litellm(litellm_model, api_key=effective_key, base_url=effective_base,
                                 temperature=temperature, max_tokens=max_tokens)

    from langchain.chat_models import init_chat_model
    kw: dict[str, Any] = {}
    if effective_key:
        kw["api_key"] = effective_key
    if effective_base:
        kw["base_url"] = effective_base
    return init_chat_model(model_str, **kw)


def _make_chat_anthropic(
    model_str: str,
    *,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> "BaseChatModel":
    """Instantiate ChatAnthropic directly.

    Sends /v1/messages (Anthropic wire format) instead of /chat/completions,
    which preserves cache_control blocks through LiteLLM proxies. Also enables
    extended thinking and sets the interleaved-thinking beta header.

    Requires langchain-anthropic — raises ImportError if not installed.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ImportError(
            "ChatAnthropic requires 'langchain-anthropic'.\n"
            "Install it with:  pip install langchain-anthropic"
        ) from exc

    effective_key  = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    effective_base = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")

    # Extended thinking — enabled by default, disable with RAI_THINKING=0.
    # Requires temperature=1 (Anthropic enforces this).
    thinking_enabled = os.environ.get("RAI_THINKING", "1") not in ("0", "false", "no")
    thinking: dict[str, Any] | None = None
    if thinking_enabled:
        thinking = {"type": "enabled", "budget_tokens": max(1024, max_tokens - 1)}
        temperature = 1.0  # required by Anthropic when thinking is enabled

    kwargs: dict[str, Any] = {
        "model": model_str,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "streaming": True,
        "default_headers": {
            "Anthropic-Beta": "interleaved-thinking-2025-05-14",
        },
    }
    if thinking is not None:
        kwargs["thinking"] = thinking
    if effective_key:
        kwargs["anthropic_api_key"] = effective_key
    if effective_base:
        kwargs["anthropic_api_url"] = effective_base
    return ChatAnthropic(**kwargs)


def _make_litellm(
    model_str: str,
    *,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> BaseChatModel:
    try:
        from langchain_litellm import ChatLiteLLM
    except ImportError as exc:
        raise ImportError(
            "LiteLLM model requested but 'langchain-litellm' is not installed.\n"
            "Install it with:  pip install langchain-litellm"
        ) from exc

    effective_key  = api_key or os.environ.get("LITELLM_API_KEY", "")
    effective_base = base_url or os.environ.get("LITELLM_BASE_URL", "")
    kwargs: dict[str, Any] = {
        "model": model_str,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "streaming": True,
    }
    if effective_key:
        kwargs["api_key"] = effective_key
    if effective_base:
        kwargs["api_base"] = effective_base
    return ChatLiteLLM(**kwargs)


# ---------------------------------------------------------------------------
# Public SDK API
# ---------------------------------------------------------------------------


def list_providers() -> list[str]:
    """Return all provider strings that _build_llm understands natively."""
    return sorted({
        "anthropic", "openai", "google", "mistral", "cohere",
        "groq", "together", "fireworks", "litellm", "ollama",
        "bedrock", "azure", "huggingface",
    })


def build_model(
    model: str | ModelConfig | BaseChatModel = DEFAULT_MODEL,
    *,
    api_key: str = "",
    base_url: str = "",
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Public wrapper around _build_llm that also accepts a ModelConfig."""
    from langchain_core.language_models import BaseChatModel as _Base

    if isinstance(model, _Base):
        return model
    if isinstance(model, ModelConfig):
        return _build_llm(
            model.model,
            api_key=model.api_key or api_key,
            base_url=model.base_url or base_url,
            temperature=model.temperature,
            max_tokens=model.max_tokens,
        )
    return _build_llm(
        model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )
