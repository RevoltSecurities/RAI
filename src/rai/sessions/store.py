"""RAI session management — thread persistence via LangGraph SQLite checkpointer.

Mirrors deepagents_cli/sessions.py but uses ~/.rai/sessions.db as the global
store (one DB for all agents, filtered by agent_name in checkpoint metadata).

Thread metadata is injected into every LangGraph stream config:
  - agent_name : which agent owns the thread
  - cwd        : working directory at the time of the call
  - git_branch : active git branch (best-effort, None if not in a repo)
  - updated_at : ISO 8601 UTC timestamp of the turn

This is what enables `rai threads list` to filter/sort by agent, time, branch.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# aiosqlite compatibility shim
# ---------------------------------------------------------------------------

_aiosqlite_patched = False


def _patch_aiosqlite() -> None:
    """Patch aiosqlite.Connection with is_alive() if missing.

    Required by langgraph-checkpoint>=2.1.0.
    """
    global _aiosqlite_patched  # noqa: PLW0603
    if _aiosqlite_patched:
        return

    try:
        import aiosqlite as _aio

        if not hasattr(_aio.Connection, "is_alive"):

            def _is_alive(self: _aio.Connection) -> bool:
                return bool(self._running and self._connection is not None)

            _aio.Connection.is_alive = _is_alive  # type: ignore[attr-defined]
    except ImportError:
        pass

    _aiosqlite_patched = True


# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------

_db_path: Path | None = None


def get_db_path() -> Path:
    """Return path to the global RAI sessions DB: ~/.rai/sessions.db."""
    global _db_path  # noqa: PLW0603
    if _db_path is not None:
        return _db_path
    db_dir = Path.home() / ".rai"
    db_dir.mkdir(parents=True, exist_ok=True)
    _db_path = db_dir / "sessions.db"
    return _db_path


# ---------------------------------------------------------------------------
# Thread ID generation
# ---------------------------------------------------------------------------


def generate_thread_id() -> str:
    """Generate a time-ordered UUID7 thread ID."""
    from uuid_utils import uuid7

    return str(uuid7())


# ---------------------------------------------------------------------------
# Stream config builder
# ---------------------------------------------------------------------------


def _get_git_branch(cwd: str) -> str | None:
    """Return the active git branch name, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=2,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_stream_config(
    thread_id: str,
    agent_name: str,
    cwd: str,
    recursion_limit: int = 100,
) -> dict:
    """Build the LangGraph RunnableConfig for a turn.

    Stores agent_name, cwd, git_branch, and updated_at in the checkpoint
    metadata column so `rai threads list` can query them without deserializing
    the full checkpoint blob.

    Args:
        thread_id: LangGraph thread identifier.
        agent_name: Agent name (stored in checkpoint metadata).
        cwd: Current working directory.
        recursion_limit: LangGraph recursion limit (top-level key). Default 100.

    Returns:
        RunnableConfig dict with `configurable`, `metadata`, and `recursion_limit` keys.
    """
    metadata: dict = {
        "agent_name": agent_name,
        "cwd": cwd,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    branch = _get_git_branch(cwd)
    if branch:
        metadata["git_branch"] = branch
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": metadata,
        "recursion_limit": recursion_limit,
    }


# ---------------------------------------------------------------------------
# Checkpointer context manager
# ---------------------------------------------------------------------------


def _cleanup_stale_wal(db: Path) -> None:
    """Remove orphaned WAL/SHM files that cause 'disk I/O error' on reconnect.

    Happens when sessions.db is deleted but sessions.db-shm survives — SQLite
    finds a shared-memory header with no matching WAL data and errors out.
    Detection: SHM is non-empty while WAL is absent or zero bytes.
    """
    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")
    wal_size = wal.stat().st_size if wal.exists() else 0
    shm_size = shm.stat().st_size if shm.exists() else 0
    if shm_size > 0 and wal_size == 0:
        logger.warning(
            "Stale SQLite SHM detected (%d bytes, no WAL) for %s — removing to prevent disk I/O error.",
            shm_size, db,
        )
        for p in (wal, shm):
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Could not remove %s: %s", p, e)


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """Yield an AsyncSqliteSaver backed by the global RAI sessions DB."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    _patch_aiosqlite()
    db = get_db_path()
    _cleanup_stale_wal(db)
    async with AsyncSqliteSaver.from_conn_string(str(db)) as cp:
        yield cp


# ---------------------------------------------------------------------------
# Thread queries
# ---------------------------------------------------------------------------


class ThreadInfo(TypedDict):
    """Thread metadata returned by list_threads()."""

    thread_id: str
    agent_name: str | None
    updated_at: str | None
    created_at: NotRequired[str | None]
    git_branch: NotRequired[str | None]
    cwd: NotRequired[str | None]
    message_count: NotRequired[int]
    latest_checkpoint_id: NotRequired[str | None]


def _sync_connect() -> sqlite3.Connection:
    return sqlite3.connect(str(get_db_path()), timeout=10)


def _table_exists_sync(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def list_threads_sync(
    agent_name: str | None = None,
    limit: int = 20,
    sort_by: str = "updated",
) -> list[ThreadInfo]:
    """List threads from the SQLite checkpoints table (synchronous).

    Args:
        agent_name: Filter to only this agent's threads. None = all agents.
        limit: Maximum rows to return.
        sort_by: ``"updated"`` or ``"created"``.

    Returns:
        ThreadInfo list sorted by the requested field, newest first.
    """
    try:
        conn = _sync_connect()
    except sqlite3.Error as e:
        logger.debug("Could not open sessions DB: %s", e)
        return []

    try:
        if not _table_exists_sync(conn, "checkpoints"):
            return []

        order_col = "created_at" if sort_by == "created" else "updated_at"

        where_clauses: list[str] = []
        params: list = []

        if agent_name:
            where_clauses.append("json_extract(metadata, '$.agent_name') = ?")
            params.append(agent_name)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT thread_id,
                   json_extract(metadata, '$.agent_name') as agent_name,
                   MAX(json_extract(metadata, '$.updated_at')) as updated_at,
                   MIN(json_extract(metadata, '$.updated_at')) as created_at,
                   MAX(json_extract(metadata, '$.git_branch')) as git_branch,
                   MAX(json_extract(metadata, '$.cwd')) as cwd,
                   MAX(checkpoint_id) as latest_checkpoint_id
            FROM checkpoints
            {where_sql}
            GROUP BY thread_id
            ORDER BY {order_col} DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [
            ThreadInfo(
                thread_id=r[0],
                agent_name=r[1],
                updated_at=r[2],
                created_at=r[3],
                git_branch=r[4],
                cwd=r[5],
                latest_checkpoint_id=r[6],
            )
            for r in rows
        ]
    except sqlite3.Error as e:
        logger.debug("Thread query failed: %s", e)
        return []
    finally:
        conn.close()


def get_most_recent_sync(agent_name: str | None = None) -> str | None:
    """Return the thread_id of the most recently updated thread.

    Args:
        agent_name: Filter to this agent. None = most recent across all agents.

    Returns:
        Thread ID string, or None if no threads exist.
    """
    threads = list_threads_sync(agent_name=agent_name, limit=1, sort_by="updated")
    return threads[0]["thread_id"] if threads else None


def thread_exists_sync(thread_id: str) -> bool:
    """Return True if any checkpoint exists for *thread_id* in the RAI DB."""
    try:
        conn = _sync_connect()
    except sqlite3.Error:
        return False
    try:
        if not _table_exists_sync(conn, "checkpoints"):
            return False
        row = conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def get_thread_by_id_sync(thread_id: str) -> ThreadInfo | None:
    """Return ThreadInfo for a specific thread_id using a direct SQL lookup."""
    try:
        conn = _sync_connect()
    except sqlite3.Error:
        return None
    try:
        if not _table_exists_sync(conn, "checkpoints"):
            return None
        row = conn.execute(
            """
            SELECT thread_id,
                   json_extract(metadata, '$.agent_name') as agent_name,
                   MAX(json_extract(metadata, '$.updated_at')) as updated_at,
                   MIN(json_extract(metadata, '$.updated_at')) as created_at,
                   MAX(json_extract(metadata, '$.git_branch')) as git_branch,
                   MAX(json_extract(metadata, '$.cwd')) as cwd,
                   MAX(checkpoint_id) as latest_checkpoint_id
            FROM checkpoints
            WHERE thread_id = ?
            GROUP BY thread_id
            """,
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return ThreadInfo(
            thread_id=row[0],
            agent_name=row[1],
            updated_at=row[2],
            created_at=row[3],
            git_branch=row[4],
            cwd=row[5],
            latest_checkpoint_id=row[6],
        )
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def delete_thread_sync(thread_id: str) -> bool:
    """Delete all checkpoints for a thread from the global DB.

    Returns:
        True if rows were deleted, False if the thread didn't exist.
    """
    try:
        conn = _sync_connect()
    except sqlite3.Error:
        return False

    try:
        if not _table_exists_sync(conn, "checkpoints"):
            return False
        cursor = conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)
        )
        deleted = cursor.rowcount > 0
        if _table_exists_sync(conn, "writes"):
            conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        conn.commit()
        return deleted
    except sqlite3.Error as e:
        logger.debug("Failed to delete thread %s: %s", thread_id, e)
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Timestamp formatting helpers
# ---------------------------------------------------------------------------


def _fmt_relative(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone()
        delta = datetime.now(tz=dt.tzinfo) - dt
        s = int(delta.total_seconds())
        if s < 0:
            return "just now"
        if s < 60:
            return f"{s}s ago"
        m = s // 60
        if m < 60:
            return f"{m}m ago"
        h = m // 60
        if h < 24:
            return f"{h}h ago"
        d = h // 24
        if d < 30:
            return f"{d}d ago"
        mo = d // 30
        if mo < 12:
            return f"{mo}mo ago"
        return f"{d // 365}y ago"
    except (ValueError, TypeError):
        return ""


def _fmt_path(path: str | None) -> str:
    if not path:
        return ""
    try:
        home = str(Path.home())
        if path.startswith(home + "/"):
            return "~/" + path[len(home) + 1 :]
        return path
    except (RuntimeError, OSError):
        return path or ""


# ---------------------------------------------------------------------------
# CLI commands (Rich output)
# ---------------------------------------------------------------------------


def cmd_threads_list(
    agent_name: str | None = None,
    limit: int = 20,
    sort_by: str = "updated",
    verbose: bool = False,
) -> None:
    """Print a Rich table of recent threads."""
    from rich import box
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    console = Console()
    threads = list_threads_sync(agent_name=agent_name, limit=limit, sort_by=sort_by)

    if not threads:
        if agent_name:
            console.print(f"[yellow]No threads found for agent '{agent_name}'.[/yellow]")
        else:
            console.print("[yellow]No threads found.[/yellow]")
        console.print("[dim]Start a conversation with: rai chat[/dim]")
        return

    label = f" — agent '{agent_name}'" if agent_name else ""
    title = f"Recent Threads{label} (last {limit}, by {sort_by})"

    table = Table(title=title, box=box.ROUNDED, show_header=True)
    table.add_column("Thread ID", style="bold cyan", max_width=36)
    table.add_column("Agent", style="green")
    table.add_column("Updated", style="yellow")
    if verbose:
        table.add_column("Created", style="dim")
        table.add_column("Branch", style="dim")
        table.add_column("Location", style="dim", max_width=30)

    for t in threads:
        row = [
            t["thread_id"],
            escape(t.get("agent_name") or "—"),
            _fmt_relative(t.get("updated_at")),
        ]
        if verbose:
            row += [
                _fmt_relative(t.get("created_at")),
                escape(t.get("git_branch") or "—"),
                escape(_fmt_path(t.get("cwd"))),
            ]
        table.add_row(*row)

    console.print()
    console.print(table)
    if len(threads) >= limit:
        console.print(f"[dim]Showing last {limit}. Use --limit N to see more.[/dim]")
    console.print()


def cmd_threads_delete(thread_id: str, *, dry_run: bool = False, force: bool = False) -> None:
    """Delete a thread with optional confirmation."""
    from rich.console import Console
    from rich.markup import escape

    console = Console()

    if dry_run:
        console.print(f"[dim]Would delete thread '{escape(thread_id)}'.[/dim]")
        console.print("[dim]No changes made.[/dim]")
        return

    if not force:
        console.print(
            f"[yellow]Delete thread '{escape(thread_id)}'? (y/N)[/yellow] ", end=""
        )
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return
        if ans not in {"y", "yes"}:
            console.print("[dim]Cancelled.[/dim]")
            return

    if delete_thread_sync(thread_id):
        console.print(f"[green]✓[/green] Thread '{escape(thread_id)}' deleted.")
    else:
        console.print(f"[yellow]Thread '{escape(thread_id)}' not found.[/yellow]")
