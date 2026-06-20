"""RunnableClaudeAgent — async context manager wrapping ClaudeSDKClient."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Result returned by RunnableClaudeAgent.run()."""

    session_id: str
    total_cost_usd: float | None
    num_turns: int
    is_error: bool


class RunnableClaudeAgent:
    """Wraps ClaudeSDKClient with a clean multi-turn streaming API.

    Use as an async context manager — the claude subprocess stays alive for
    the duration, allowing multiple query() calls in the same session.

    Example::

        async with ClaudeAgent.builder().agent_name("rai").without_hitl().build() as agent:
            result = await agent.run("what is 2+2?")
            print(result.total_cost_usd)

            # stream tokens
            async for msg in agent.stream("explain XSS"):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            print(block.text, end="", flush=True)
    """

    def __init__(self, options: Any, agent_name: str) -> None:
        self._options = options
        self._agent_name = agent_name
        self._client: Any = None
        self._session_id: str | None = None
        self._last_result: RunResult | None = None

    async def __aenter__(self) -> "RunnableClaudeAgent":
        from claude_agent_sdk import ClaudeSDKClient
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as exc:
                logger.debug("Disconnect error: %s", exc)
            self._client = None

    async def run(self, prompt: str) -> RunResult:
        """Send prompt and drain receive_response() — returns after the first ResultMessage."""
        from claude_agent_sdk.types import ResultMessage

        if self._client is None:
            raise RuntimeError("Not connected — use 'async with agent' context manager")

        await self._client.query(prompt)

        result = RunResult(
            session_id="",
            total_cost_usd=None,
            num_turns=0,
            is_error=False,
        )
        async for msg in self._client.receive_response():
            if isinstance(msg, ResultMessage):
                result = RunResult(
                    session_id=msg.session_id,
                    total_cost_usd=msg.total_cost_usd,
                    num_turns=msg.num_turns,
                    is_error=msg.is_error,
                )
                self._session_id = msg.session_id
                break

        self._last_result = result
        return result

    async def stream(self, prompt: str) -> AsyncIterator[Any]:
        """Send prompt and yield raw SDK messages until the turn completes.

        Yields all messages including ResultMessage. If subagents were spawned
        during the turn, continues yielding TaskNotificationMessage events
        after ResultMessage until all subagents have reported back.
        """
        from claude_agent_sdk.types import ResultMessage, TaskStartedMessage, TaskNotificationMessage

        if self._client is None:
            raise RuntimeError("Not connected — use 'async with agent' context manager")

        await self._client.query(prompt)

        pending_subagents: int = 0

        async for msg in self._client.receive_messages():
            yield msg

            if isinstance(msg, TaskStartedMessage):
                pending_subagents += 1

            if isinstance(msg, TaskNotificationMessage):
                pending_subagents = max(0, pending_subagents - 1)

            if isinstance(msg, ResultMessage):
                self._session_id = msg.session_id
                self._last_result = RunResult(
                    session_id=msg.session_id,
                    total_cost_usd=msg.total_cost_usd,
                    num_turns=msg.num_turns,
                    is_error=msg.is_error,
                )
                if pending_subagents <= 0:
                    break
                # Subagents still running — keep reading for notifications

    async def interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def set_permission_mode(self, mode: str) -> None:
        if self._client is not None:
            await self._client.set_permission_mode(mode)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def last_result(self) -> RunResult | None:
        return self._last_result
