from __future__ import annotations

from rai.client.claude._transport import ClaudeTransport


class HealthAPI:
    def __init__(self, transport: ClaudeTransport) -> None:
        self._t = transport

    async def ok(self) -> dict:
        """GET /ok — returns {"status": "ok"} when the server is up."""
        return await self._t.get("/ok")
