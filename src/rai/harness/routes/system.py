"""System endpoints: /ok, /stats."""

from __future__ import annotations

from fastapi import APIRouter, Request

from rai import __version__
from rai.harness.models import StatsResponse
from rai.harness.runner import _RUN_REGISTRY
from rai.harness.tasks import get_all_live_tasks
from rai.sessions.store import list_threads_sync

router = APIRouter(tags=["system"])


@router.get("/ok")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    pool = request.app.state.pool
    active_runs = sum(
        1 for r in _RUN_REGISTRY.values() if r.get("status") == "running"
    )
    live_tasks = len(get_all_live_tasks())
    agents = pool.agent_names()
    total_threads = len(list_threads_sync(limit=10000))
    return StatsResponse(
        active_runs=active_runs,
        live_tasks=live_tasks,
        registered_agents=agents,
        total_threads=total_threads,
    )
