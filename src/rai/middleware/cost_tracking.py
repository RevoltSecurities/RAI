"""Cost tracking — model pricing table and cost calculation utility.

Provides compute_cost() used by the harness runner to calculate total_cost_usd
from per-model token usage breakdown. Pricing is per 1M tokens.

Also exports CostTrackingMiddleware which accumulates cost in a contextvar
for per-request real-time cost tracking (optional — runner can also compute
cost directly from _model_usage at run_end).
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Awaitable, Callable
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

# Per 1M tokens (USD)
_PRICING: dict[str, dict[str, float]] = {
    # Claude 4.x
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-4-8": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    # Claude 3.5
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Claude 3
    "claude-3-opus-20240229": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-3-sonnet-20240229": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.30},
    # Haiku 4.5
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # GPT-4o (via LiteLLM)
    "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25, "cache_write": 2.50},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.15},
}

# Aliases for partial model name matching
_ALIASES: dict[str, str] = {
    "sonnet-4": "claude-sonnet-4-6",
    "opus-4": "claude-opus-4-8",
    "haiku-4": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5-20251001",
}

# Contextvar for per-run cost accumulation (optional middleware use)
run_cost: contextvars.ContextVar[float] = contextvars.ContextVar("rai_run_cost", default=0.0)


def _resolve_pricing(model_id: str) -> dict[str, float] | None:
    """Resolve model ID to pricing dict, handling partial matches."""
    if not model_id:
        return None
    if model_id in _PRICING:
        return _PRICING[model_id]
    # Try aliases
    lower = model_id.lower()
    for alias, canonical in _ALIASES.items():
        if alias in lower:
            return _PRICING.get(canonical)
    # Fuzzy: find longest prefix match
    for key in sorted(_PRICING.keys(), key=len, reverse=True):
        if key in model_id or model_id in key:
            return _PRICING[key]
    return None


def compute_cost(model_usage: dict[str, dict[str, int]]) -> float:
    """Compute total cost USD from per-model usage breakdown.

    Args:
        model_usage: {model_id: {input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens}}

    Returns:
        Total cost in USD. Returns 0.0 if no pricing found.
    """
    total = 0.0
    for model_id, usage in model_usage.items():
        pricing = _resolve_pricing(model_id)
        if not pricing:
            logger.debug("No pricing for model '%s', skipping cost calc", model_id)
            continue
        input_cost = (usage.get("input_tokens", 0) / 1_000_000) * pricing["input"]
        output_cost = (usage.get("output_tokens", 0) / 1_000_000) * pricing["output"]
        cache_read_cost = (usage.get("cache_read_tokens", 0) / 1_000_000) * pricing["cache_read"]
        cache_write_cost = (usage.get("cache_creation_tokens", 0) / 1_000_000) * pricing["cache_write"]
        total += input_cost + output_cost + cache_read_cost + cache_write_cost
    return round(total, 6)


class CostTrackingMiddleware(AgentMiddleware):
    """Model-call middleware that accumulates per-run cost in a contextvar.

    Optional — the runner can also call compute_cost() directly at run_end.
    This middleware provides real-time cost accumulation during the run.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)

        # Extract usage from response messages
        for msg in response.result:
            usage = getattr(msg, "usage_metadata", None) or {}
            if not usage:
                continue
            in_toks = usage.get("input_tokens", 0)
            out_toks = usage.get("output_tokens", 0)
            details = usage.get("input_token_details", {}) or {}
            cache_creation = (
                (details.get("ephemeral_5m_input_tokens") or 0)
                + (details.get("ephemeral_1h_input_tokens") or 0)
            ) or (details.get("cache_creation") or 0)
            cache_read = details.get("cache_read") or 0

            # Detect model
            meta = getattr(msg, "response_metadata", {}) or {}
            model_id = (
                meta.get("model_id")
                or meta.get("model")
                or meta.get("model_name")
                or ""
            )
            pricing = _resolve_pricing(model_id)
            if not pricing:
                continue

            cost = (
                (in_toks / 1_000_000) * pricing["input"]
                + (out_toks / 1_000_000) * pricing["output"]
                + (cache_read / 1_000_000) * pricing["cache_read"]
                + (cache_creation / 1_000_000) * pricing["cache_write"]
            )
            try:
                current = run_cost.get()
                run_cost.set(current + cost)
            except LookupError:
                run_cost.set(cost)

        return response
