"""SSEStream — reconnect-aware async iterator with Last-Event-ID replay."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from rai.client._config import ClientConfig
from rai.client._events import SSEEvent, parse_event
from rai.client._transport import AsyncTransport

logger = logging.getLogger(__name__)

_TERMINAL_EVENTS = frozenset({"run_end"})


def _parse_sse_frames(raw_text: str) -> list[SSEEvent]:
    """Parse a raw SSE chunk into zero or more SSEEvent objects."""
    events: list[SSEEvent] = []
    event_type = ""
    data_lines: list[str] = []
    event_id: str | None = None

    for line in raw_text.splitlines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line.startswith("id:"):
            event_id = line[3:].strip()
        elif line == "" and (event_type or data_lines):
            raw_data = "\n".join(data_lines)
            parsed: dict[str, Any] = {}
            if raw_data:
                try:
                    parsed = json.loads(raw_data)
                except json.JSONDecodeError:
                    parsed = {"raw": raw_data}
            events.append(SSEEvent(
                event=event_type,
                data=parsed,
                id=event_id,
                raw=raw_data,
            ))
            event_type = ""
            data_lines = []
            event_id = None

    return events


class SSEStream:
    """Reconnect-aware async iterator over a server-sent events endpoint.

    Yields typed event dataclasses when typed=True (default), or raw SSEEvent
    objects when typed=False. Terminates on run_end (subagent_completed is
    terminal only for subagent streams via extra_terminal, not parent runs).
    """

    def __init__(
        self,
        transport: AsyncTransport,
        cfg: ClientConfig,
        path: str,
        *,
        typed: bool = True,
        extra_terminal: frozenset[str] | None = None,
        last_event_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._cfg = cfg
        self._path = path
        self._typed = typed
        self._terminal = _TERMINAL_EVENTS | (extra_terminal or frozenset())
        self._last_event_id = last_event_id

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        reconnects = 0
        last_id: str | None = None
        pending = ""

        while True:
            try:
                headers: dict[str, str] = {}
                event_id = self._last_event_id if last_id is None else last_id
                if event_id is not None:
                    headers["Last-Event-ID"] = event_id

                async with self._transport.make_sse_client() as client:
                    async with client.stream("GET", self._path, headers=headers) as resp:
                        resp.raise_for_status()
                        reconnects = 0
                        async for chunk in resp.aiter_text():
                            pending += chunk
                            # Process complete event blocks (terminated by blank line)
                            while "\n\n" in pending:
                                block, pending = pending.split("\n\n", 1)
                                for raw_ev in _parse_sse_frames(block + "\n\n"):
                                    if raw_ev.id:
                                        last_id = raw_ev.id
                                    ev = parse_event(raw_ev) if self._typed else raw_ev
                                    yield ev
                                    if raw_ev.event in self._terminal:
                                        return
                return  # clean EOF
            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                if not self._cfg.sse_reconnect or reconnects >= self._cfg.sse_max_reconnects:
                    raise
                reconnects += 1
                delay = 0.5 * reconnects
                logger.debug("SSEStream %s disconnected (%s); reconnect %d in %.1fs",
                             self._path, exc, reconnects, delay)
                await asyncio.sleep(delay)
                pending = ""
