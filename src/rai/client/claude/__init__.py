"""rai.client.claude — async Python client for rai claude-serve (FastAPI SSE backend).

Quick start::

    import asyncio
    from rai.client.claude import ClaudeServeClient, ClaudeTokenEvent, ClaudeRunEndEvent

    async def main():
        async with ClaudeServeClient("http://127.0.0.1:8001") as c:
            async for ev in c.chat.stream("scan example.com for vulns"):
                if isinstance(ev, ClaudeTokenEvent):
                    print(ev.text, end="", flush=True)
                elif isinstance(ev, ClaudeRunEndEvent):
                    print(f"\\ncost=${ev.cost:.4f}, turns={ev.turns}")
                    break

    asyncio.run(main())
"""

from __future__ import annotations

from rai.client.claude._config import ClaudeServeConfig
from rai.client.claude._events import (
    ClaudeSessionIdEvent,
    ClaudeTokenEvent,
    ClaudeThinkingEvent,
    ClaudeToolStartEvent,
    ClaudeToolResultEvent,
    ClaudeSubagentStartEvent,
    ClaudeSubagentProgressEvent,
    ClaudeSubagentDoneEvent,
    ClaudeHITLRequestEvent,
    ClaudeHITLResponseEvent,
    ClaudePlanBlockedEvent,
    ClaudeRateLimitEvent,
    ClaudeRunEndEvent,
    ClaudeErrorEvent,
    parse_claude_event,
)
from rai.client.claude._sse import ClaudeSSEStream
from rai.client.claude._transport import ClaudeTransport
from rai.client.claude.chat import ChatAPI
from rai.client.claude.health import HealthAPI
from rai.client.claude.history import HistoryAPI
from rai.client.claude.sessions import SessionsAPI


class ClaudeServeClient:
    """Async client for the RAI claude-serve FastAPI SSE server.

    Usage::

        async with ClaudeServeClient() as c:
            await c.health.ok()

            async for ev in c.chat.stream("hello", session_id="s1"):
                if isinstance(ev, ClaudeTokenEvent):
                    print(ev.text, end="")
                elif isinstance(ev, ClaudeHITLRequestEvent):
                    await c.sessions.resolve_permission(ev.session_id, ev.tool_use_id, "allow")
                elif isinstance(ev, ClaudeRunEndEvent):
                    print(f"cost=${ev.cost:.4f}")

            history = await c.history.list(limit=10)
            await c.sessions.set_permission_mode("s1", "bypassPermissions")
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8001",
        *,
        api_key: str = "",
        config: ClaudeServeConfig | None = None,
    ) -> None:
        self.config = config or ClaudeServeConfig(base_url=base_url, api_key=api_key)
        self._transport = ClaudeTransport(self.config)
        self.chat     = ChatAPI(self._transport, self.config)
        self.sessions = SessionsAPI(self._transport)
        self.history  = HistoryAPI(self._transport)
        self.health   = HealthAPI(self._transport)

    def set_api_key(self, key: str) -> None:
        """Update the API key on the live transport."""
        self.config.api_key = key
        self._transport._client.headers["Authorization"] = f"Bearer {key}"

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> ClaudeServeClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()


__all__ = [
    # Main client
    "ClaudeServeClient",
    "ClaudeServeConfig",
    # Transport / SSE internals
    "ClaudeTransport",
    "ClaudeSSEStream",
    "parse_claude_event",
    # Typed event dataclasses
    "ClaudeSessionIdEvent",
    "ClaudeTokenEvent",
    "ClaudeThinkingEvent",
    "ClaudeToolStartEvent",
    "ClaudeToolResultEvent",
    "ClaudeSubagentStartEvent",
    "ClaudeSubagentProgressEvent",
    "ClaudeSubagentDoneEvent",
    "ClaudeHITLRequestEvent",
    "ClaudeHITLResponseEvent",
    "ClaudePlanBlockedEvent",
    "ClaudeRateLimitEvent",
    "ClaudeRunEndEvent",
    "ClaudeErrorEvent",
    # API namespaces
    "ChatAPI",
    "SessionsAPI",
    "HistoryAPI",
    "HealthAPI",
]
