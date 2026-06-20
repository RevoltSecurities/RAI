from __future__ import annotations

from typing import Any

from rai.client.claude._transport import ClaudeTransport


class HistoryAPI:
    def __init__(self, transport: ClaudeTransport) -> None:
        self._t = transport

    async def list(self, *, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        """GET /history — list Claude sessions from ~/.claude/ (paginated)."""
        return await self._t.get("/history", params={"limit": limit, "offset": offset})

    async def get(self, session_id: str) -> dict[str, Any]:
        """GET /history/{session_id} — metadata for one session."""
        return await self._t.get(f"/history/{session_id}")

    async def messages(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """GET /history/{session_id}/messages — paginated message list."""
        return await self._t.get(
            f"/history/{session_id}/messages",
            params={"limit": limit, "offset": offset},
        )

    async def subagents(self, session_id: str) -> dict[str, Any]:
        """GET /history/{session_id}/subagents — list subagent IDs for a session."""
        return await self._t.get(f"/history/{session_id}/subagents")

    async def subagent_messages(
        self, session_id: str, agent_id: str, *, limit: int = 50
    ) -> dict[str, Any]:
        """GET /history/{session_id}/subagents/{agent_id}/messages."""
        return await self._t.get(
            f"/history/{session_id}/subagents/{agent_id}/messages",
            params={"limit": limit},
        )

    async def delete(self, session_id: str) -> dict[str, Any]:
        """DELETE /history/{session_id} — hard-delete a session from ~/.claude/."""
        return await self._t.delete(f"/history/{session_id}")
