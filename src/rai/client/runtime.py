"""RuntimeAPI — one-shot runtime runs with inline agent definitions."""

from __future__ import annotations

from typing import Any, AsyncIterator

from rai.client._config import ClientConfig
from rai.client._sse import SSEStream
from rai.client._transport import AsyncTransport
from rai.client._types import RuntimeAgentDef, RuntimeRunRequest, RunResponse


class RuntimeAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def create_run(
        self,
        input: str,
        agent_def: RuntimeAgentDef | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> RunResponse:
        if agent_def is None:
            agent_def = RuntimeAgentDef()
        elif isinstance(agent_def, dict):
            agent_def = RuntimeAgentDef(**agent_def)
        body = RuntimeRunRequest(input=input, agent=agent_def, thread_id=thread_id)
        data = await self._t.post("/agents/runtime/runs", body)
        return RunResponse(**data)

    def stream(self, run_id: str, *, typed: bool = True) -> SSEStream:
        return SSEStream(self._t, self._cfg, f"/agents/runtime/runs/{run_id}/stream", typed=typed)

    async def run(
        self,
        input: str,
        agent_def: RuntimeAgentDef | dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Create a runtime run and stream its events."""
        resp = await self.create_run(input, agent_def, thread_id=thread_id)
        async for ev in self.stream(resp.run_id):
            yield ev
