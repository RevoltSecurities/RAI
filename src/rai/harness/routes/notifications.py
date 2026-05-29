"""Notification endpoints: snapshot and SSE stream for task completions."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from rai.harness.sse import ThreadNotifBus
from rai.harness.tasks import get_pending_notifs_snapshot

router = APIRouter(tags=["notifications"])


@router.get("/threads/{thread_id}/notifications")
async def get_notifications(thread_id: str) -> dict:
    """Non-destructive snapshot of pending notifications for this thread."""
    all_notifs = get_pending_notifs_snapshot()
    # Filter to notifications likely belonging to this thread (best effort via prefix)
    # Since _PENDING_NOTIFICATIONS keys are task_ids or pipeline composite keys,
    # we return all of them and let the client filter — thread scoping requires
    # reading LangGraph state to map task_id → thread.
    return {"thread_id": thread_id, "notifications": all_notifs}


@router.get("/threads/{thread_id}/notifications/stream")
async def stream_notifications(thread_id: str, request: Request) -> StreamingResponse:
    """SSE stream of task completion and pipeline events for this thread."""
    bus = ThreadNotifBus.create(thread_id)

    async def _gen():
        try:
            async for frame in bus.subscribe():
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            bus.detach()

    return StreamingResponse(_gen(), media_type="text/event-stream")
