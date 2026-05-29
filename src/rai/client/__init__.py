"""rai.client — async Python SDK for the RAI HTTP harness.

Quick-start
-----------
    import asyncio
    from rai.client import RAIClient, TokenEvent, RunEndEvent

    async def main():
        async with RAIClient() as c:
            async for ev in c.runs.run_once("rai", "hello"):
                if isinstance(ev, TokenEvent):
                    print(ev.content, end="", flush=True)
                elif isinstance(ev, RunEndEvent):
                    print()
                    break

    asyncio.run(main())
"""

from __future__ import annotations

from rai.client._config import ClientConfig
from rai.client._events import (
    SSEEvent,
    AskUserRequestEvent,
    ErrorEvent,
    InterruptAutoApprovedEvent,
    InterruptEvent,
    InterruptResolvedEvent,
    NotificationEvent,
    PermissionDeniedEvent,
    PipelineBatchEvent,
    PipelineBatchStartedEvent,
    PipelineCreatedEvent,
    PipelineEndEvent,
    PipelineErrorEvent,
    PlanApprovedEvent,
    PlanCompletedEvent,
    PlanModeEnteredEvent,
    PlanReadyEvent,
    PlanRejectedEvent,
    RateLimitEvent,
    RunEndEvent,
    RunKeepaliveEvent,
    RunStartEvent,
    SessionApprovedEvent,
    StepBlockedEvent,
    StepCompleteEvent,
    StepStartEvent,
    SubagentCompletedEvent,
    SubagentErrorEvent,
    SubagentInterruptEvent,
    SubagentInterruptResolvedEvent,
    SubagentResumedEvent,
    SubagentStartedEvent,
    SubagentThinkingEvent,
    SubagentTokenEvent,
    SubagentToolEndEvent,
    SubagentToolStartEvent,
    SubagentTurnCompleteEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskStatusEvent,
    ThinkingEvent,
    TokenEvent,
    ToolEndEvent,
    ToolStartEvent,
    WatcherInvokeEvent,
    parse_event,
)
from rai.client._sse import SSEStream
from rai.client._transport import AsyncTransport
from rai.client._types import (
    AgentInfo,
    AskUserAnswersRequest,
    CompactResponse,
    CompactStatusResponse,
    CreateRunRequest,
    InjectMessageRequest,
    InterruptDecisionRequest,
    InterruptResponse,
    PlanDecisionRequest,
    PipelineResponse,
    RegisterAgentRequest,
    RunDetailResponse,
    RunResponse,
    RuntimeAgentDef,
    RuntimeRunRequest,
    StatsResponse,
    SubagentInfo,
    TaskResponse,
    TaskUpdateRequest,
    ThreadInfo,
    ThreadStateResponse,
    ThreadSummaryResponse,
    UsageInfo,
)
from rai.client.agents import AgentsAPI
from rai.client.pipelines import PipelinesAPI
from rai.client.runs import PlanTimeoutError, RunsAPI
from rai.client.runtime import RuntimeAPI
from rai.client.subagents import SubagentsAPI
from rai.client.system import SystemAPI
from rai.client.tasks import TasksAPI
from rai.client.threads import ThreadsAPI


class RAIClient:
    """Main entry point for the RAI HTTP harness async client.

    Usage:
        async with RAIClient("http://127.0.0.1:8000") as c:
            async for ev in c.runs.run_once("rai", "hello"):
                ...
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: str = "",
        config: ClientConfig | None = None,
        **cfg_kwargs,
    ) -> None:
        self.config = config or ClientConfig(
            base_url=base_url, api_key=api_key, **cfg_kwargs
        )
        self._transport = AsyncTransport(self.config)
        self.system = SystemAPI(self._transport, self.config)
        self.agents = AgentsAPI(self._transport, self.config)
        self.runs = RunsAPI(self._transport, self.config)
        self.runtime = RuntimeAPI(self._transport, self.config)
        self.threads = ThreadsAPI(self._transport, self.config)
        self.tasks = TasksAPI(self._transport, self.config)
        self.pipelines = PipelinesAPI(self._transport, self.config)
        self.subagents = SubagentsAPI(self._transport, self.config)

    def set_allowed_tools(self, tools: list[str] | None) -> None:
        """Mutate session allowed_tools. All future runs pick this up."""
        self.config.allowed_tools = tools

    def set_api_key(self, key: str) -> None:
        """Update the API key on the live transport."""
        self.config.api_key = key
        self._transport._client.headers["X-API-Key"] = key

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> RAIClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()


__all__ = [
    # Main client
    "RAIClient",
    "ClientConfig",
    "PlanTimeoutError",
    # Transport / SSE internals (advanced use)
    "AsyncTransport",
    "SSEStream",
    "SSEEvent",
    "parse_event",
    # Typed event dataclasses
    "RunStartEvent",
    "RunEndEvent",
    "RunKeepaliveEvent",
    "RateLimitEvent",
    "ErrorEvent",
    "TokenEvent",
    "ThinkingEvent",
    "ToolStartEvent",
    "ToolEndEvent",
    "PermissionDeniedEvent",
    "InterruptEvent",
    "InterruptResolvedEvent",
    "InterruptAutoApprovedEvent",
    "SessionApprovedEvent",
    "PlanModeEnteredEvent",
    "PlanReadyEvent",
    "PlanApprovedEvent",
    "PlanRejectedEvent",
    "PlanCompletedEvent",
    "StepBlockedEvent",
    "StepCompleteEvent",
    "StepStartEvent",
    "TaskCreatedEvent",
    "TaskStatusEvent",
    "TaskCompletedEvent",
    "NotificationEvent",
    "PipelineCreatedEvent",
    "PipelineBatchStartedEvent",
    "PipelineBatchEvent",
    "PipelineEndEvent",
    "PipelineErrorEvent",
    "WatcherInvokeEvent",
    "SubagentStartedEvent",
    "SubagentTokenEvent",
    "SubagentThinkingEvent",
    "SubagentToolStartEvent",
    "SubagentToolEndEvent",
    "SubagentInterruptEvent",
    "SubagentInterruptResolvedEvent",
    "SubagentErrorEvent",
    "SubagentCompletedEvent",
    "SubagentResumedEvent",
    "SubagentTurnCompleteEvent",
    # Request/response types
    "CreateRunRequest",
    "RunResponse",
    "RunDetailResponse",
    "UsageInfo",
    "RuntimeAgentDef",
    "RuntimeRunRequest",
    "RegisterAgentRequest",
    "InterruptResponse",
    "InterruptDecisionRequest",
    "PlanDecisionRequest",
    "ThreadInfo",
    "ThreadStateResponse",
    "ThreadSummaryResponse",
    "TaskResponse",
    "TaskUpdateRequest",
    "PipelineResponse",
    "AgentInfo",
    "SubagentInfo",
    "StatsResponse",
    "CompactStatusResponse",
    "CompactResponse",
    "InjectMessageRequest",
    # API namespaces
    "SystemAPI",
    "AgentsAPI",
    "RunsAPI",
    "RuntimeAPI",
    "ThreadsAPI",
    "TasksAPI",
    "PipelinesAPI",
    "SubagentsAPI",
]
