"""SSE event buses for the RAI HTTP server.

RunEventBus    — per-run event stream (tokens, tools, task lifecycle, HITL)
ThreadNotifBus — per-thread notification stream (task completions, pipeline events)

D3: RunEventBus maintains a capped replay buffer (max 500 frames, ~last few minutes
of a long scan) so clients can reconnect with Last-Event-ID and receive all
events they missed without duplicates.
"""

from __future__ import annotations

import asyncio
import collections
import itertools
import json
import logging
from collections.abc import AsyncIterator
from typing import ClassVar

logger = logging.getLogger(__name__)

_SENTINEL = object()
_REPLAY_BUFFER_SIZE = 500


def sse_frame(event_type: str, data: dict, event_id: int | None = None) -> str:
    """Build a single SSE frame, optionally prefixed with `id:` for replay."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data)}")
    lines.append("")  # blank line terminates the frame
    return "\n".join(lines) + "\n"


class RunEventBus:
    """One asyncio.Queue per run_id, multiple consumers supported via fan-out.

    D3: Keeps a capped deque of all emitted (event_id, frame) tuples so that
    reconnecting subscribers can replay events they missed by passing a
    Last-Event-ID value to subscribe().
    """

    _buses: ClassVar[dict[str, "RunEventBus"]] = {}
    _closed: ClassVar[set[str]] = set()
    _counter: ClassVar[itertools.count[int]] = itertools.count(1)

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._queues: list[asyncio.Queue] = []
        self._replay: collections.deque[tuple[int, str]] = collections.deque(
            maxlen=_REPLAY_BUFFER_SIZE
        )

    @classmethod
    def create(cls, run_id: str) -> "RunEventBus":
        if run_id not in cls._buses:
            cls._buses.setdefault(run_id, cls(run_id))
        return cls._buses[run_id]

    async def publish(self, event_type: str, data: dict) -> None:
        data = {"run_id": self._run_id, **data}
        event_id = next(RunEventBus._counter)
        frame = sse_frame(event_type, data, event_id=event_id)
        # Store in replay buffer before dispatching to live subscribers
        self._replay.append((event_id, frame))
        for q in list(self._queues):
            await q.put(frame)

    async def subscribe(self, last_event_id: int | None = None) -> AsyncIterator[str]:
        """Subscribe to the bus.

        If last_event_id is given, replay all buffered events whose ID is
        strictly greater than last_event_id before switching to live delivery.

        If the run is already closed, replays the buffer (if requested) and
        returns immediately without hanging.
        """
        # D3 — replay buffered events before live subscription
        if last_event_id is not None:
            for eid, frame in list(self._replay):
                if eid > last_event_id:
                    yield frame

        # If already closed: no live events to wait for — exit after replay
        if self._run_id in RunEventBus._closed:
            return

        queue: asyncio.Queue = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        except asyncio.CancelledError:
            pass
        finally:
            if queue in self._queues:
                self._queues.remove(queue)

    def close(self, run_id: str) -> None:
        RunEventBus._closed.add(run_id)
        # Use self._queues directly so cancel_run's early cleanup() call doesn't
        # prevent sentinels from reaching subscribers that are still connected.
        for q in list(self._queues):
            q.put_nowait(_SENTINEL)
        try:
            asyncio.get_running_loop().call_later(60.0, RunEventBus.cleanup, run_id)
        except RuntimeError:
            # No event loop running (e.g. shutdown) — clean up immediately
            RunEventBus.cleanup(run_id)

    @classmethod
    def is_closed(cls, run_id: str) -> bool:
        return run_id in cls._closed

    @classmethod
    def cleanup(cls, run_id: str) -> None:
        cls._buses.pop(run_id, None)
        cls._closed.discard(run_id)


class ThreadNotifBus:
    """Per-thread SSE bus for notification-only events (subagent completions, pipelines)."""

    _buses: ClassVar[dict[str, list[asyncio.Queue]]] = {}

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id
        self._queue: asyncio.Queue = asyncio.Queue()
        ThreadNotifBus._buses.setdefault(thread_id, []).append(self._queue)

    @classmethod
    def create(cls, thread_id: str) -> "ThreadNotifBus":
        return cls(thread_id)

    async def publish(self, thread_id: str, event_type: str, data: dict) -> None:
        data = {"thread_id": thread_id, **data}
        frame = sse_frame(event_type, data)
        queues = ThreadNotifBus._buses.get(thread_id, [])
        for q in list(queues):
            await q.put(frame)

    async def subscribe(self) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield sse_frame("heartbeat", {"thread_id": self._thread_id})
                    continue
                if item is _SENTINEL:
                    break
                yield item
        except asyncio.CancelledError:
            pass

    def detach(self) -> None:
        queues = ThreadNotifBus._buses.get(self._thread_id, [])
        if self._queue in queues:
            queues.remove(self._queue)
        self._queue.put_nowait(_SENTINEL)

    @classmethod
    def close_thread(cls, thread_id: str) -> None:
        queues = cls._buses.pop(thread_id, [])
        for q in queues:
            q.put_nowait(_SENTINEL)
