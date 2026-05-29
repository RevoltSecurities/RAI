"""Persistent prompt history for the RAI TUI.

Stored as ~/.rai/history.jsonl — one JSON object per line:
    {"ts": "ISO-8601", "agent": "rai", "text": "the prompt text"}

Max 1 000 entries loaded per session; file is append-only (no compaction).
All I/O errors are suppressed — history is best-effort, never fatal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_HISTORY_PATH = Path.home() / ".rai" / "history.jsonl"
_MAX_ENTRIES = 1000


def load_history(agent: str = "") -> list[str]:
    """Return prompt texts from history, newest first.

    Filters by agent name when provided. Returns [] on any I/O or parse error.
    Caps at _MAX_ENTRIES so navigation stays snappy regardless of file size.
    """
    try:
        if not _HISTORY_PATH.exists():
            return []
        lines = _HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        # Take last _MAX_ENTRIES lines before parsing to avoid reading huge files
        lines = lines[-_MAX_ENTRIES:]
        texts: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if agent and entry.get("agent", "") != agent:
                continue
            text = entry.get("text", "")
            if text:
                texts.append(text)
        texts.reverse()  # newest first
        return texts
    except Exception:
        return []


def append_history(text: str, agent: str = "rai") -> None:
    """Append one prompt to the history file.

    Skips empty/whitespace-only text and consecutive duplicates.
    Creates ~/.rai/ if it does not exist.
    """
    if not text or not text.strip():
        return
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Consecutive dedup: skip if identical to the last line in the file
        if _HISTORY_PATH.exists():
            try:
                with _HISTORY_PATH.open("rb") as f:
                    # Read last line efficiently
                    f.seek(0, 2)
                    size = f.tell()
                    if size > 0:
                        pos = max(0, size - 4096)
                        f.seek(pos)
                        tail = f.read().decode("utf-8", errors="replace")
                        last_line = tail.rstrip("\n").rsplit("\n", 1)[-1].strip()
                        if last_line:
                            prev = json.loads(last_line)
                            if prev.get("text", "") == text:
                                return
            except Exception:
                pass  # dedup failed → append anyway

        entry = json.dumps({
            "ts": datetime.now(UTC).isoformat(),
            "agent": agent,
            "text": text,
        }, ensure_ascii=False)
        with _HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass
