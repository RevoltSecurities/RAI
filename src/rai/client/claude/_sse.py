"""ClaudeSSEStream — POST-based SSE iterator for rai claude-serve /chat.

The claude-serve /chat endpoint is a POST that returns text/event-stream.
Events are newline-delimited JSON: `data: {...}\n\n`
There is no `event:` field — the event type lives inside the JSON as `"type"`.
Terminal condition: raw line "data: [DONE]" (server sends this after all events).
No reconnect: per-call stream; server session state lives in a subprocess.

Debug logging:
  RAI_CLAUDE_TUI_DEBUG=1  — log all SSE events to ~/.rai/debug/claude-tui.jsonl
  RAI_CLAUDE_TUI_DEBUG_FILE=<path>  — override log path
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from rai.client.claude._events import parse_claude_event
from rai.client.claude._transport import ClaudeTransport


def _debug_log_path() -> Path | None:
    if os.environ.get("RAI_CLAUDE_TUI_DEBUG", "0") != "1":
        return None
    default = str(Path.home() / ".rai" / "debug" / "claude-tui.jsonl")
    return Path(os.environ.get("RAI_CLAUDE_TUI_DEBUG_FILE", default))


def _write_debug(log_path: Path, entry: dict) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


class ClaudeSSEStream:
    def __init__(self, transport: ClaudeTransport, body: dict[str, Any]) -> None:
        self._transport = transport
        self._body = body

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        log_path = _debug_log_path()
        pending = ""
        async with self._transport.make_sse_client() as client:
            async with client.stream("POST", "/chat", json=self._body) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    pending += chunk
                    while "\n\n" in pending:
                        block, pending = pending.split("\n\n", 1)
                        for line in block.splitlines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                if log_path:
                                    _write_debug(log_path, {"ts": datetime.now().isoformat(), "raw": "[DONE]"})
                                return
                            try:
                                d = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if log_path:
                                _write_debug(log_path, {"ts": datetime.now().isoformat(), "event": d})
                            ev = parse_claude_event(d)
                            yield ev
