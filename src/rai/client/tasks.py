"""TasksAPI — thread-scoped and global task endpoints."""

from __future__ import annotations

from typing import Any

from rai.client._config import ClientConfig
from rai.client._transport import AsyncTransport
from rai.client._types import TaskResponse, TaskUpdateRequest


class TasksAPI:
    def __init__(self, transport: AsyncTransport, cfg: ClientConfig) -> None:
        self._t = transport
        self._cfg = cfg

    async def list(self, thread_id: str, *, status: str | None = None) -> list[TaskResponse]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        data = await self._t.get(f"/threads/{thread_id}/tasks", params=params)
        return [TaskResponse(**t) for t in data]

    async def get(self, thread_id: str, task_id: str) -> TaskResponse:
        data = await self._t.get(f"/threads/{thread_id}/tasks/{task_id}")
        return TaskResponse(**data)

    async def progress(self, thread_id: str, task_id: str) -> dict:
        return await self._t.get(f"/threads/{thread_id}/tasks/{task_id}/progress")

    async def cancel(self, thread_id: str, task_id: str) -> dict:
        return await self._t.post(f"/threads/{thread_id}/tasks/{task_id}/cancel")

    async def update(self, thread_id: str, task_id: str, message: str) -> dict:
        return await self._t.post(
            f"/threads/{thread_id}/tasks/{task_id}/update",
            TaskUpdateRequest(message=message),
        )

    async def response(
        self, thread_id: str, task_id: str, *, timeout: float = 120.0
    ) -> dict:
        return await self._t.get(
            f"/threads/{thread_id}/tasks/{task_id}/response",
            params={"timeout": timeout},
        )

    async def list_all(self) -> list[TaskResponse]:
        data = await self._t.get("/tasks")
        return [TaskResponse(**t) for t in data]

    async def get_global(self, task_id: str) -> TaskResponse:
        data = await self._t.get(f"/tasks/{task_id}")
        return TaskResponse(**data)
