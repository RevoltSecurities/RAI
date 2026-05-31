"""Outer agentic loop runner for RAI.

run_agent() is the single entry point for driving a user turn to completion.
It starts SubagentWatcher before the first invocation and does NOT return to
the caller until all background subagent work is fully settled.

The exit condition is evaluated entirely in Python:
  1. _TASK_REGISTRY is empty     — no asyncio tasks still executing
  2. _PENDING_NOTIFICATIONS is empty — no completed results waiting to process
  3. agent busy flag is clear    — agent is not mid-invocation

Only when all three are simultaneously true does _wait_until_all_done() return.
The LLM has no say in this decision.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from rai.agents.background import _NOTIF_LOCK, _PENDING_NOTIFICATIONS, _TASK_REGISTRY
from rai.engine.watcher import SubagentWatcher

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def run_agent(
    user_prompt: str,
    agent: Any,
    thread_id: str,
    *,
    config: dict[str, Any] | None = None,
    agent_name: str = "rai",
    cwd: str = ".",
    timeout: float = 3600.0,
) -> Any:
    """Run one user turn to full completion, including all spawned subagents.

    Starts SubagentWatcher before invoking the agent.  After the initial
    invocation returns, blocks in _wait_until_all_done() until _TASK_REGISTRY,
    _PENDING_NOTIFICATIONS, and the watcher busy flag are all simultaneously
    clear.

    Args:
        user_prompt: The user's input message.
        agent: A compiled LangGraph graph (supports ainvoke and aget_state).
        thread_id: LangGraph thread identifier.
        config: Optional pre-built RunnableConfig.  If None, built from
                rai.sessions.store.build_stream_config using agent_name and cwd.
        agent_name: Agent name stored in checkpoint metadata.
        cwd: Working directory stored in checkpoint metadata.
        timeout: Max wall-clock seconds to wait for quiescence before returning.

    Returns:
        Result of the last agent.ainvoke() call — either the watcher's most
        recent re-invocation result or the initial invocation result if no
        watcher re-invocations occurred.
    """
    if config is None:
        from rai.sessions.store import build_stream_config
        config = build_stream_config(thread_id, agent_name, cwd)

    watcher = SubagentWatcher(agent, config)
    watcher.start()

    try:
        watcher.set_busy()
        try:
            initial_result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_prompt)]},
                config,
            )
        finally:
            watcher.set_idle()

        await _wait_until_all_done(watcher, timeout=timeout)
    finally:
        await watcher.stop()

    # Return the result of the last ainvoke() that ran — watcher's if it re-invoked,
    # otherwise the initial one.
    return watcher.last_result if watcher.last_result is not None else initial_result


async def _wait_until_all_done(
    watcher: SubagentWatcher,
    *,
    timeout: float = 3600.0,
    poll_interval: float = 1.0,
) -> None:
    """Block until no tasks are running, no notifications are pending, and the
    agent is idle — all checked atomically under _NOTIF_LOCK.

    Semantics of each condition:
    - _TASK_REGISTRY empty:          every asyncio.Task has completed/cancelled
    - _PENDING_NOTIFICATIONS empty:  every completion result has been consumed
    - watcher.is_busy() False:       agent is not currently processing a result

    Returns normally on timeout (logs a warning) so the caller can return the
    last captured ainvoke result instead of raising.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _NOTIF_LOCK:
            # Read both notification dicts under the lock so the snapshot is atomic.
            notifs_empty = not _PENDING_NOTIFICATIONS
            tasks_empty = not _TASK_REGISTRY
        agent_idle = not watcher.is_busy()

        if notifs_empty and tasks_empty and agent_idle:
            return

        await asyncio.sleep(poll_interval)

    logger.warning("run_agent: timed out after %.0fs — returning last captured state", timeout)
