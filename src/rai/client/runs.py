"""RunsAPI — create, list, stream, cancel, plan mode, global run list."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from rai.client._config import ClientConfig
from rai.client._events import PlanReadyEvent
from rai.client._sse import SSEStream
from rai.client._transport import AsyncTransport
from rai.client._types import CreateRunRequest, PlanDecisionRequest, RunDetailResponse, RunResponse

_SENTINEL = object()


class PlanTimeoutError(TimeoutError):
    pass


class RunsAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def create(
        self,
        agent: str,
        input: str,
        *,
        allowed_tools: Any = _SENTINEL,
        model: str | None = None,
        thread_id: str | None = None,
        plan_mode: bool = False,
        max_turns: int | None = None,
        recursion_limit: int | None = None,
        self_learn: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> RunResponse:
        tools = self._cfg.allowed_tools if allowed_tools is _SENTINEL else allowed_tools
        body = CreateRunRequest(
            input=input,
            thread_id=thread_id,
            model=model,
            allowed_tools=tools,
            plan_mode=plan_mode,
            max_turns=max_turns,
            recursion_limit=recursion_limit,
            self_learn=self_learn,
            metadata=metadata,
        )
        data = await self._t.post(f"/agents/{agent}/runs", body)
        return RunResponse(**data)

    async def list(
        self,
        agent: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return await self._t.get(f"/agents/{agent}/runs", params=params)

    async def get(self, agent: str, run_id: str) -> RunDetailResponse:
        data = await self._t.get(f"/agents/{agent}/runs/{run_id}")
        return RunDetailResponse(**data)

    def stream(
        self,
        agent: str,
        run_id: str,
        *,
        last_event_id: str | None = None,
        typed: bool = True,
    ) -> SSEStream:
        path = f"/agents/{agent}/runs/{run_id}/stream"
        s = SSEStream(self._t, self._cfg, path, typed=typed, last_event_id=last_event_id)
        return s

    async def cancel(self, agent: str, run_id: str) -> dict:
        return await self._t.post(f"/agents/{agent}/runs/{run_id}/cancel")

    async def get_plan(self, agent: str, run_id: str) -> dict:
        return await self._t.get(f"/agents/{agent}/runs/{run_id}/plan")

    async def approve_plan(self, agent: str, run_id: str) -> dict:
        return await self._t.post(f"/agents/{agent}/runs/{run_id}/plan/approve")

    async def reject_plan(self, agent: str, run_id: str, feedback: str = "") -> dict:
        return await self._t.post(
            f"/agents/{agent}/runs/{run_id}/plan/reject",
            PlanDecisionRequest(feedback=feedback or None),
        )

    async def edit_plan(self, agent: str, run_id: str, feedback: str = "") -> dict:
        return await self._t.post(
            f"/agents/{agent}/runs/{run_id}/plan/edit",
            PlanDecisionRequest(feedback=feedback or None),
        )

    async def respond_plan(self, agent: str, run_id: str, feedback: str = "") -> dict:
        return await self._t.post(
            f"/agents/{agent}/runs/{run_id}/plan/respond",
            PlanDecisionRequest(feedback=feedback or None),
        )

    async def list_all(
        self,
        *,
        status: str | None = None,
        agent: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if agent:
            params["agent"] = agent
        return await self._t.get("/runs", params=params)

    async def get_by_id(self, run_id: str) -> RunDetailResponse:
        data = await self._t.get(f"/runs/{run_id}")
        return RunDetailResponse(**data)

    async def run_once(
        self,
        agent: str,
        input: str,
        **create_kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Create a run and stream its events. Async generator yielding typed events."""
        resp = await self.create(agent, input, **create_kwargs)
        async for ev in self.stream(agent, resp.run_id):
            yield ev

    async def await_plan(
        self,
        agent: str,
        run_id: str,
        *,
        timeout: float = 3600.0,
    ) -> PlanReadyEvent:
        """Stream events until plan_ready arrives. Raises PlanTimeoutError on timeout."""
        async def _wait() -> PlanReadyEvent:
            async for ev in self.stream(agent, run_id):
                if isinstance(ev, PlanReadyEvent):
                    return ev
            raise PlanTimeoutError(f"run {run_id} ended without emitting plan_ready")

        try:
            return await asyncio.wait_for(_wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise PlanTimeoutError(
                f"plan_ready not received within {timeout}s for run {run_id}"
            ) from exc
