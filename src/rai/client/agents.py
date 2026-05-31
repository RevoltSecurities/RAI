"""AgentsAPI — list, get, register, remove agents."""

from __future__ import annotations

from typing import Any

from rai.client._config import ClientConfig
from rai.client._transport import AsyncTransport
from rai.client._types import AgentInfo, RegisterAgentRequest, SubagentInfo


class AgentsAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def list(self) -> list[AgentInfo]:
        data = await self._t.get("/agents")
        return [AgentInfo(**a) for a in data]

    async def get(self, name: str) -> AgentInfo:
        data = await self._t.get(f"/agents/{name}")
        return AgentInfo(**data)

    async def list_subagents(self, name: str) -> list[SubagentInfo]:
        data = await self._t.get(f"/agents/{name}/subagents")
        return [SubagentInfo(**s) for s in data]

    async def register(self, body: RegisterAgentRequest | dict[str, Any]) -> dict:
        return await self._t.post("/agents", body)

    async def remove(self, name: str) -> dict:
        return await self._t.delete(f"/agents/{name}")
