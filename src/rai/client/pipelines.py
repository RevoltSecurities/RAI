"""PipelinesAPI — list and get pipeline state."""

from __future__ import annotations

from rai.client._config import ClientConfig
from rai.client._transport import AsyncTransport
from rai.client._types import PipelineResponse


class PipelinesAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def list(self, thread_id: str) -> list[dict]:
        return await self._t.get(f"/threads/{thread_id}/pipelines")

    async def get(self, thread_id: str, pipeline_id: str) -> PipelineResponse:
        data = await self._t.get(f"/threads/{thread_id}/pipelines/{pipeline_id}")
        return PipelineResponse(**data)
