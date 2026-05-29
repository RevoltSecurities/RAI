"""Self-learning loop: post-run lesson extraction for the HTTP harness.

After each opt-in run (self_learn=True), rule-based insights are appended to
~/.rai/agents/<name>/memory/lessons.md. On the next run, MemoryMiddleware loads
this file and the agent benefits from accumulated lessons without any extra LLM call.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def run_self_learn(
    run_id: str,
    agent_name: str,
    registry_entry: dict[str, Any],
    bus: Any,
) -> None:
    """Extract rule-based lessons from a completed run and append to lessons.md.

    Called by execute_run() after the run finishes when self_learn=True.
    Never raises — failures are logged as warnings so the run result is unaffected.
    """
    try:
        lessons: list[str] = []
        ts = datetime.now(UTC).strftime("%Y-%m-%d")

        stop = registry_entry.get("stop_reason", "")
        subtype = registry_entry.get("result_subtype", "")
        plan = registry_entry.get("plan", "")
        num_turns = registry_entry.get("num_turns", 0)
        task_input = (registry_entry.get("input") or "")[:120]

        if subtype == "error_max_turns":
            lessons.append(
                f"- [{ts}] **Max turns exceeded** on: `{task_input}` "
                f"({num_turns} turns) — consider breaking into smaller sub-tasks "
                f"or using parallel subagents."
            )

        if plan and stop in ("end_turn", "stop_sequence", "stop"):
            plan_lines = len(plan.splitlines())
            lessons.append(
                f"- [{ts}] **Plan-mode run succeeded** ({num_turns} turns, "
                f"{plan_lines}-line plan) — this plan structure works for similar tasks."
            )

        if not lessons:
            return

        lessons_path = (
            Path.home() / ".rai" / "agents" / agent_name / "memory" / "lessons.md"
        )
        lessons_path.parent.mkdir(parents=True, exist_ok=True)
        with lessons_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lessons) + "\n")

        await bus.publish("self_learn_complete", {
            "run_id": run_id,
            "lessons_count": len(lessons),
            "lessons_file": str(lessons_path),
        })
    except Exception as exc:
        logger.warning("Self-learn failed for run %s: %s", run_id, exc)
