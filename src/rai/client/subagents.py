"""SubagentsAPI — list, stream, HITL, cancel, output for subagents."""

from __future__ import annotations

from typing import Any

from rai.client._config import ClientConfig
from rai.client._sse import SSEStream
from rai.client._transport import AsyncTransport
from rai.client._types import InterruptDecisionRequest


class SubagentsAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def list(
        self,
        *,
        status: str | None = None,
        parent_run_id: str | None = None,
        pipeline_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if parent_run_id:
            params["parent_run_id"] = parent_run_id
        if pipeline_id:
            params["pipeline_id"] = pipeline_id
        return await self._t.get("/subagents", params=params)

    async def get(self, task_id: str) -> dict:
        return await self._t.get(f"/subagents/{task_id}")

    def stream(
        self,
        task_id: str,
        *,
        typed: bool = True,
    ) -> SSEStream:
        return SSEStream(
            self._t, self._cfg,
            f"/subagents/{task_id}/stream",
            typed=typed,
            extra_terminal=frozenset({"subagent_completed"}),
        )

    async def get_interrupt(self, task_id: str) -> dict:
        return await self._t.get(f"/subagents/{task_id}/interrupt")

    async def submit_interrupt(
        self, task_id: str, decision: dict[str, Any]
    ) -> dict:
        return await self._t.post(
            f"/subagents/{task_id}/interrupt",
            InterruptDecisionRequest(**decision),
        )

    async def cancel(self, task_id: str) -> dict:
        return await self._t.post(f"/subagents/{task_id}/cancel")

    async def output(self, task_id: str, *, timeout: float = 120.0) -> dict:
        return await self._t.get(
            f"/subagents/{task_id}/output",
            params={"timeout": timeout},
        )

    async def wait_output(self, task_id: str, *, timeout: float = 300.0) -> str:
        """Block until subagent completes; return output string directly."""
        result = await self.output(task_id, timeout=timeout)
        return result.get("output") or ""

    # ------------------------------------------------------------------
    # HITL shortcuts
    # ------------------------------------------------------------------

    async def approve(self, task_id: str) -> dict:
        return await self.submit_interrupt(task_id, {"decision": "approve"})

    async def reject(self, task_id: str, message: str = "") -> dict:
        d: dict[str, Any] = {"decision": "reject"}
        if message:
            d["message"] = message
        return await self.submit_interrupt(task_id, d)

    async def respond(self, task_id: str, message: str) -> dict:
        return await self.submit_interrupt(
            task_id, {"decision": "respond", "message": message}
        )

    async def edit(self, task_id: str, edited_action: dict[str, Any]) -> dict:
        return await self.submit_interrupt(
            task_id, {"decision": "edit", "edited_action": edited_action}
        )

    async def approve_for_session(self, task_id: str) -> dict:
        return await self.submit_interrupt(task_id, {"decision": "approve_for_session"})

