"""dev_log.py — Write ERROR/WARNING/CRITICAL log records to /tmp/rai-errors.json.

Active only when DEV=1 is set in the environment.  Call install() once at
bootstrap; the file is truncated on each new process so every 'rai' run
produces a fresh log.

Format: one JSON object per line (JSONL), fields:
  ts       — ISO-8601 timestamp
  level    — WARNING / ERROR / CRITICAL
  logger   — logger name (e.g. "rai.mcp.loader")
  msg      — formatted log message
  exc      — traceback string, present only when an exception was attached
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone

_LOG_PATH = "/tmp/rai-errors.json"
_installed = False


class _DevJsonHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        # Truncate on process start so each session gets a clean file.
        try:
            open(_LOG_PATH, "w").close()  # noqa: WPS515
        except OSError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            }
            if record.exc_info:
                entry["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            with open(_LOG_PATH, "a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:  # noqa: BLE001
            pass  # never let the dev logger itself crash the process


def install() -> None:
    """Install the JSON dev handler on the root logger (idempotent)."""
    global _installed  # noqa: PLW0603
    if _installed or os.environ.get("DEV") != "1":
        return
    handler = _DevJsonHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    _installed = True
    logging.getLogger(__name__).debug("DEV=1: error log → %s", _LOG_PATH)
