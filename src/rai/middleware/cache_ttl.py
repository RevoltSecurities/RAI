"""CacheControlTTLUpgradeMiddleware — strips ttl from cache_control blocks.

langchain-anthropic's AnthropicPromptCachingMiddleware defaults ttl="5m" — cache
expires every 5 minutes. Claude Code sends no ttl; Anthropic then defaults to 1h,
giving 12x longer cache lifetime.

This middleware strips 'ttl' from all cache_control blocks (system, tools,
model_settings) so the final request matches Claude Code's no-ttl behavior.

deepagents appends AnthropicPromptCachingMiddleware unconditionally at its tail
(outermost = runs first). This middleware sits inside it and strips ttl after it runs.
The monkey-patch in factory.py also patches the class-level _cache_control property
as a belt-and-suspenders approach.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse


def _strip_ttl(obj: Any) -> Any:
    """Recursively strip 'ttl' from any cache_control dict."""
    if isinstance(obj, dict):
        if "cache_control" in obj and isinstance(obj["cache_control"], dict):
            cc = {k: v for k, v in obj["cache_control"].items() if k != "ttl"}
            obj = {**obj, "cache_control": cc}
        return {k: _strip_ttl(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_ttl(i) for i in obj]
    return obj


def _strip_tool_ttl(tools: list[Any]) -> list[Any]:
    """Strip ttl from the last tool's extras (BaseTool) or cache_control (dict)."""
    if not tools:
        return tools
    result = list(tools)
    last = result[-1]
    # BaseTool pydantic object — cache_control lives in .extras
    extras = getattr(last, "extras", None)
    if isinstance(extras, dict) and "cache_control" in extras:
        cc = {k: v for k, v in extras["cache_control"].items() if k != "ttl"}
        result[-1] = last.model_copy(update={"extras": {**extras, "cache_control": cc}})
    # Plain dict tool
    elif isinstance(last, dict) and "cache_control" in last:
        cc = {k: v for k, v in last["cache_control"].items() if k != "ttl"}
        result[-1] = {**last, "cache_control": cc}
    return result


def _upgrade_request(request: ModelRequest) -> ModelRequest:
    overrides: dict[str, Any] = {}

    # Strip ttl from system message content blocks
    sys_msg = request.system_message
    if sys_msg is not None:
        content = sys_msg.content
        if isinstance(content, list):
            from langchain_core.messages import SystemMessage
            overrides["system_message"] = SystemMessage(content=_strip_ttl(content))

    # Strip ttl from last tool (BaseTool.extras or plain dict cache_control)
    if request.tools:
        overrides["tools"] = _strip_tool_ttl(list(request.tools))

    # Strip ttl from model_settings cache_control
    settings = request.model_settings or {}
    if "cache_control" in settings:
        cc = {k: v for k, v in settings["cache_control"].items() if k != "ttl"}
        overrides["model_settings"] = {**settings, "cache_control": cc}

    return request.override(**overrides) if overrides else request


class CacheControlTTLUpgradeMiddleware(AgentMiddleware):
    """Strips ttl from all cache_control blocks — Anthropic defaults to 1h without it."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_upgrade_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(_upgrade_request(request))
