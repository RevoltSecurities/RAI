"""ModelOverride middleware — per-call model switching via RAI_MODEL_OVERRIDE env var."""

from __future__ import annotations

import os

from langchain.agents.middleware.types import AgentMiddleware


class ModelOverrideMiddleware(AgentMiddleware):
    """Switch the LLM model for every call when RAI_MODEL_OVERRIDE is set.

    Set the environment variable to any model string supported by RAI's
    ``_build_llm()`` helper (e.g. ``claude-opus-4-7``, ``gpt-4o``,
    ``bedrock/anthropic.claude-3-5-sonnet``).

    Zero overhead when the env var is unset — the request passes through
    unchanged.
    """

    def __init__(self, default_model: str = "") -> None:
        self._default = default_model
        self._cached_model: tuple[str, object] | None = None

    def wrap_model_call(self, request, handler):
        return handler(self._maybe_override(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._maybe_override(request))

    def _maybe_override(self, request):
        override = os.environ.get("RAI_MODEL_OVERRIDE", self._default).strip()
        if not override:
            return request
        try:
            # Cache the built LLM to avoid rebuilding on every call.
            if self._cached_model is None or self._cached_model[0] != override:
                from rai.engine.model import _build_llm
                new_llm = _build_llm(override)
                self._cached_model = (override, new_llm)
            return request.override(model=self._cached_model[1])
        except Exception:
            return request
