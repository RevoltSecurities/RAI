"""SystemAPI — health check and stats."""

from __future__ import annotations

from rai.client._config import ClientConfig
from rai.client._transport import AsyncTransport
from rai.client._types import StatsResponse


class SystemAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def health(self) -> dict:
        return await self._t.get("/ok")

    async def stats(self) -> StatsResponse:
        data = await self._t.get("/stats")
        return StatsResponse(**data)
