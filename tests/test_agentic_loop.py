"""Tests for the self-driving agentic loop.

Verifies that SubagentWatcher and run_agent() together enforce loop
continuation in Python code — not via LLM instructions.

Test scenario
-------------
* run_agent() is called with a prompt that spawns two mock subagents:
  recon (completes in 3 s) and exploit (completes in 7 s).
* The mock agent's ainvoke simulates awrap_model_call by consuming
  _PENDING_NOTIFICATIONS on every watcher-triggered re-invocation.
* Assertions:
    - Total wall-clock time < 10 s  (tasks ran in parallel, not serially)
    - Agent was re-invoked at least twice by the watcher (once per task)
    - _PENDING_NOTIFICATIONS is empty when run_agent returns
    - _TASK_REGISTRY is empty when run_agent returns
    - run_agent returned exactly once to the caller (one final response)
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import rai.agents.background as _mod
from rai.agents.background import (
    _NOTIF_LOCK,
    _PENDING_NOTIFICATIONS,
    _TASK_REGISTRY,
    _on_done,
)
from rai.engine.runner import run_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StateSnapshot:
    """Minimal stand-in for LangGraph's StateSnapshot."""

    def __init__(self, messages: list) -> None:
        self.values = {"messages": messages}


async def _mock_subagent(sleep_time: float, name: str) -> dict[str, Any]:
    """Sleeps then returns a success dict, mimicking a subagent invocation."""
    await asyncio.sleep(sleep_time)
    return {"messages": [AIMessage(content=f"{name} completed after {sleep_time}s")]}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_global_state():
    """Reset module-level dicts before and after every test.

    Because _TASK_REGISTRY and _PENDING_NOTIFICATIONS are imported by
    reference (they are the actual dict objects), mutating them in-place
    is visible everywhere — no reassignment needed.
    """
    _TASK_REGISTRY.clear()
    with _NOTIF_LOCK:
        _PENDING_NOTIFICATIONS.clear()
    yield
    # Cancel any stray asyncio tasks left by a failed test
    for task in list(_TASK_REGISTRY.values()):
        task.cancel()
    _TASK_REGISTRY.clear()
    with _NOTIF_LOCK:
        _PENDING_NOTIFICATIONS.clear()
    # Clean up temp output files
    for p in Path("/tmp").glob("test_rai_*.json"):
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agentic_loop_redrives_on_subagent_completion():
    """The watcher must re-invoke the agent once per subagent completion,
    both subagents must run in parallel, and run_agent must not return until
    both notifications have been processed.
    """

    invocation_count = 0
    watcher_invocations: list[int] = []  # invocation numbers triggered by watcher

    async def mock_ainvoke(state: dict, config: dict | None = None) -> dict:
        nonlocal invocation_count
        invocation_count += 1
        current = invocation_count

        if current == 1:
            # ── User turn: spawn two background tasks ──────────────────────
            for sleep_time, name in [(3.0, "recon"), (7.0, "exploit")]:
                task_id = uuid.uuid4().hex
                out_path = Path(f"/tmp/test_rai_{task_id[:8]}.json")
                out_path.write_text(
                    json.dumps({"status": "running", "output": ""}),
                    encoding="utf-8",
                )
                task = asyncio.create_task(
                    _mock_subagent(sleep_time, name),
                    name=f"test-{name}",
                )
                # Register the done callback exactly as _launch_task does.
                task.add_done_callback(
                    functools.partial(
                        _on_done, task_id=task_id, out_path=out_path, agent_name=name
                    )
                )
                _TASK_REGISTRY[task_id] = task

        else:
            # ── Watcher-triggered turn: consume pending notifications ───────
            # This mirrors what awrap_model_call does via _pop_notification_text.
            watcher_invocations.append(current)
            with _NOTIF_LOCK:
                _PENDING_NOTIFICATIONS.clear()

        return {"messages": [AIMessage(content=f"Response {current}")]}

    # aget_state always returns an AIMessage as the last message so the watcher
    # never sees a HumanMessage-last state and can safely re-invoke.
    async def mock_aget_state(config: dict | None = None) -> _StateSnapshot:
        return _StateSnapshot([AIMessage(content="previous response")])

    mock_agent = type(
        "MockAgent",
        (),
        {
            "ainvoke": mock_ainvoke,
            "aget_state": mock_aget_state,
        },
    )()

    thread_config = {"configurable": {"thread_id": "test-thread-agentic-loop"}}

    # ── Run ────────────────────────────────────────────────────────────────
    start = time.monotonic()
    final = await run_agent(
        user_prompt="Run recon and exploit agents in parallel",
        agent=mock_agent,
        thread_id="test-thread-agentic-loop",
        config=thread_config,
    )
    elapsed = time.monotonic() - start

    # ── Assertions ──────────────────────────────────────────────────────────

    # 1. Parallel execution: both tasks run concurrently so total time is
    #    bounded by the longer one (7 s) plus at most one poll cycle (2 s).
    assert elapsed < 10.0, (
        f"Expected parallel execution (<10 s), got {elapsed:.2f} s — "
        "tasks may have run serially"
    )

    # 2. The watcher must have re-invoked the agent at least twice —
    #    once for recon (3 s) and once for exploit (7 s).
    assert len(watcher_invocations) >= 2, (
        f"Expected at least 2 watcher-triggered re-invocations, "
        f"got {len(watcher_invocations)}: {watcher_invocations}"
    )

    # 3. Total invocation count: initial (user) + at least 2 watcher = >= 3.
    assert invocation_count >= 3, (
        f"Expected at least 3 total invocations, got {invocation_count}"
    )

    # 4. All notifications consumed before run_agent returned.
    with _NOTIF_LOCK:
        assert not _PENDING_NOTIFICATIONS, (
            f"_PENDING_NOTIFICATIONS not empty on return: {list(_PENDING_NOTIFICATIONS)}"
        )

    # 5. All asyncio tasks settled before run_agent returned.
    assert not _TASK_REGISTRY, (
        f"_TASK_REGISTRY not empty on return: {list(_TASK_REGISTRY)}"
    )

    # 6. run_agent returned exactly one value — the result of the last ainvoke() call.
    assert final is not None, "run_agent returned None"
    assert isinstance(final, dict), (
        f"Expected dict (last ainvoke result), got {type(final)}"
    )
    assert "messages" in final, "Expected 'messages' key in final ainvoke result"
