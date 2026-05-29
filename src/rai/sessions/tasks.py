"""Persistent subagent task registry backed by ~/.rai/tasks.db.

Mirrors store.py patterns: same DB directory, same aiosqlite connection
lifecycle, same sync-fallback helpers for startup scanning.

Persisted fields = all SubagentMeta fields  +  model, system_prompt, cwd
(the three extra fields needed to recompile a graph after server restart).
api_key / base_url are intentionally NOT stored — loaded from
load_agent_config(agent_name) at recovery time.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subagent_tasks (
    task_id          TEXT PRIMARY KEY,
    agent_name       TEXT NOT NULL DEFAULT '',
    parent_run_id    TEXT NOT NULL DEFAULT '',
    parent_thread_id TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'running',
    created_at       TEXT NOT NULL DEFAULT '',
    input            TEXT NOT NULL DEFAULT '',
    output           TEXT,
    output_file      TEXT NOT NULL DEFAULT '',
    label            TEXT,
    pipeline_id      TEXT,
    depends_on       TEXT,
    model            TEXT NOT NULL DEFAULT '',
    system_prompt    TEXT,
    cwd              TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_st_status        ON subagent_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_st_parent_run    ON subagent_tasks(parent_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_st_parent_thread ON subagent_tasks(parent_thread_id)",
]

# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------

_db_path: Path | None = None


def get_tasks_db_path() -> Path:
    """Return path to the subagent tasks DB: ~/.rai/tasks.db."""
    global _db_path  # noqa: PLW0603
    if _db_path is not None:
        return _db_path
    db_dir = Path.home() / ".rai"
    db_dir.mkdir(parents=True, exist_ok=True)
    _db_path = db_dir / "tasks.db"
    return _db_path


# ---------------------------------------------------------------------------
# TaskStore
# ---------------------------------------------------------------------------

class TaskStore:
    """Async SQLite store for subagent orchestration metadata."""

    def __init__(self, conn: Any) -> None:  # aiosqlite.Connection
        self._conn = conn

    async def setup(self) -> None:
        """Create table and indexes if they don't exist."""
        await self._conn.execute(_CREATE_TABLE)
        for idx in _CREATE_INDEXES:
            await self._conn.execute(idx)
        await self._conn.commit()

    async def upsert(self, row: dict) -> None:
        """Insert or replace a full task record."""
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """INSERT OR REPLACE INTO subagent_tasks
               (task_id, agent_name, parent_run_id, parent_thread_id, status,
                created_at, input, output, output_file, label, pipeline_id,
                depends_on, model, system_prompt, cwd, updated_at)
               VALUES
               (:task_id, :agent_name, :parent_run_id, :parent_thread_id, :status,
                :created_at, :input, :output, :output_file, :label, :pipeline_id,
                :depends_on, :model, :system_prompt, :cwd, :updated_at)""",
            {
                "task_id":          row.get("task_id", ""),
                "agent_name":       row.get("agent_name", ""),
                "parent_run_id":    row.get("parent_run_id", ""),
                "parent_thread_id": row.get("parent_thread_id", ""),
                "status":           row.get("status", "running"),
                "created_at":       row.get("created_at", now),
                "input":            row.get("input", ""),
                "output":           row.get("output"),
                "output_file":      row.get("output_file", ""),
                "label":            row.get("label"),
                "pipeline_id":      row.get("pipeline_id"),
                "depends_on":       row.get("depends_on"),
                "model":            row.get("model", ""),
                "system_prompt":    row.get("system_prompt"),
                "cwd":              row.get("cwd", ""),
                "updated_at":       now,
            },
        )
        await self._conn.commit()

    async def update_status(self, task_id: str, status: str) -> None:
        """Update only status and updated_at."""
        await self._conn.execute(
            "UPDATE subagent_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (status, datetime.now(UTC).isoformat(), task_id),
        )
        await self._conn.commit()

    async def update_output(
        self,
        task_id: str,
        status: str,
        output: str,
        output_file: str,
    ) -> None:
        """Update terminal fields: status, output, output_file, updated_at."""
        await self._conn.execute(
            """UPDATE subagent_tasks
               SET status = ?, output = ?, output_file = ?, updated_at = ?
               WHERE task_id = ?""",
            (status, output, output_file, datetime.now(UTC).isoformat(), task_id),
        )
        await self._conn.commit()

    async def list_incomplete(self) -> list[dict]:
        """Return all tasks with status 'running' or 'interrupted'."""
        cursor = await self._conn.execute(
            "SELECT * FROM subagent_tasks WHERE status IN ('running', 'interrupted')"
            " ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get(self, task_id: str) -> dict | None:
        """Fetch a single task record by task_id."""
        cursor = await self._conn.execute(
            "SELECT * FROM subagent_tasks WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Context manager (server-lifetime connection)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_task_store() -> AsyncIterator[TaskStore]:
    """Yield a TaskStore backed by ~/.rai/tasks.db for the server lifetime."""
    import aiosqlite  # transitive dep via langgraph-checkpoint-sqlite

    async with aiosqlite.connect(str(get_tasks_db_path())) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        store = TaskStore(conn)
        await store.setup()
        yield store
