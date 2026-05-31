"""ThreadsAPI — thread CRUD, HITL, compact, inject, notifications, subagents."""

from __future__ import annotations

from typing import Any

from rai.client._config import ClientConfig
from rai.client._sse import SSEStream
from rai.client._transport import AsyncTransport
from rai.client._types import (
    AskUserAnswersRequest,
    CompactResponse,
    CompactStatusResponse,
    InjectMessageRequest,
    InterruptDecisionRequest,
    InterruptResponse,
    ThreadInfo,
    ThreadStateResponse,
    ThreadSummaryResponse,
)


class ThreadsAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def list(
        self,
        *,
        agent: str | None = None,
        limit: int = 20,
        sort: str = "updated",
    ) -> list[ThreadInfo]:
        params: dict[str, Any] = {"limit": limit, "sort": sort}
        if agent:
            params["agent"] = agent
        data = await self._t.get("/threads", params=params)
        return [ThreadInfo(**t) for t in data]

    async def get(self, thread_id: str) -> ThreadInfo:
        data = await self._t.get(f"/threads/{thread_id}")
        return ThreadInfo(**data)

    async def state(self, thread_id: str) -> ThreadStateResponse:
        data = await self._t.get(f"/threads/{thread_id}/state")
        return ThreadStateResponse(**data)

    async def history(
        self, thread_id: str, *, limit: int = 50, offset: int = 0
    ) -> dict:
        return await self._t.get(
            f"/threads/{thread_id}/history",
            params={"limit": limit, "offset": offset},
        )

    async def delete(self, thread_id: str) -> dict:
        return await self._t.delete(f"/threads/{thread_id}")

    async def runs(
        self,
        thread_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return await self._t.get(f"/threads/{thread_id}/runs", params=params)

    async def compact(self, thread_id: str) -> CompactResponse:
        data = await self._t.post(f"/threads/{thread_id}/compact")
        return CompactResponse(**data)

    async def compact_status(self, thread_id: str) -> CompactStatusResponse:
        data = await self._t.get(f"/threads/{thread_id}/compact/status")
        return CompactStatusResponse(**data)

    async def summary(self, thread_id: str) -> ThreadSummaryResponse:
        data = await self._t.get(f"/threads/{thread_id}/summary")
        return ThreadSummaryResponse(**data)

    async def inject_message(
        self,
        thread_id: str,
        content: str,
        *,
        agent_name: str | None = None,
    ) -> dict:
        return await self._t.post(
            f"/threads/{thread_id}/messages",
            InjectMessageRequest(content=content, agent_name=agent_name),
        )

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    async def get_interrupt(self, thread_id: str) -> InterruptResponse:
        data = await self._t.get(f"/threads/{thread_id}/interrupt")
        return InterruptResponse(**data)

    async def submit_interrupt(
        self, thread_id: str, decision: dict[str, Any]
    ) -> dict:
        return await self._t.post(
            f"/threads/{thread_id}/interrupt",
            InterruptDecisionRequest(**decision),
        )

    async def submit_ask_user(
        self, thread_id: str, answers: list[str], status: str = "answered"
    ) -> dict:
        return await self._t.post(
            f"/threads/{thread_id}/ask_user",
            AskUserAnswersRequest(status=status, answers=answers),
        )

    def interrupt_stream(self, thread_id: str, *, typed: bool = True) -> SSEStream:
        return SSEStream(self._t, self._cfg, f"/threads/{thread_id}/interrupt/stream", typed=typed)

    async def approve(self, thread_id: str) -> dict:
        return await self.submit_interrupt(thread_id, {"decision": "approve"})

    async def reject(self, thread_id: str, message: str = "") -> dict:
        d: dict[str, Any] = {"decision": "reject"}
        if message:
            d["message"] = message
        return await self.submit_interrupt(thread_id, d)

    async def respond(self, thread_id: str, message: str) -> dict:
        return await self.submit_interrupt(
            thread_id, {"decision": "respond", "message": message}
        )

    async def edit(self, thread_id: str, edited_action: dict[str, Any]) -> dict:
        return await self.submit_interrupt(
            thread_id, {"decision": "edit", "edited_action": edited_action}
        )

    async def approve_for_session(self, thread_id: str) -> dict:
        return await self.submit_interrupt(thread_id, {"decision": "approve_for_session"})

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def notifications(self, thread_id: str) -> dict:
        return await self._t.get(f"/threads/{thread_id}/notifications")

    def notification_stream(self, thread_id: str, *, typed: bool = True) -> SSEStream:
        return SSEStream(
            self._t, self._cfg,
            f"/threads/{thread_id}/notifications/stream",
            typed=typed,
        )

    # ------------------------------------------------------------------
    # Thread-scoped subagents
    # ------------------------------------------------------------------

    async def subagents(
        self,
        thread_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return await self._t.get(f"/threads/{thread_id}/subagents", params=params)
