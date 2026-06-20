"""07_plan_then_execute.py — Plan mode first, then autonomous execution.

Mirrors RAI's PlanModeMiddleware pattern:
  1. plan_mode()    → Claude produces a structured plan (no tools fire)
  2. User reviews the plan
  3. without_hitl() → Claude executes the plan autonomously

Also demonstrates:
    Custom system_prompt() with plan-aware instructions
    Passing plan text into the execution prompt
    effort() — tune reasoning depth

Usage:
    python examples/claude/07_plan_then_execute.py --target https://example.com
    python examples/claude/07_plan_then_execute.py --target "src/" --task "find SQL injection"
    python examples/claude/07_plan_then_execute.py --skip-review   # auto-approve
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rai.sdk import (
    ClaudeAgent,
    ResultMessage,
    full_text,
    iter_tokens,
    iter_tool_calls,
    TaskStartedMessage,
    TaskNotificationMessage,
)

_PLANNER_PROMPT = """\
You are a security assessment planner.

When given a target, produce ONLY a numbered attack plan — no execution.
Format each step as:
  N. [PHASE] Action description — expected output

Phases: RECON | ENUM | EXPLOIT | VERIFY | REPORT

Keep to ≤10 steps. Be specific about tools and commands.
"""

_EXECUTOR_PROMPT = """\
You are a security assessment executor.

You will be given a pre-approved attack plan. Execute each step precisely
using Bash, Read, and WebFetch as needed. After completing each step,
print: STEP N COMPLETE: <brief result>

Do not deviate from the plan unless a step is impossible.
"""


async def get_plan(target: str, task: str) -> str:
    """Run in plan_mode — returns the plan text, fires no tools."""
    print("[plan mode — generating plan…]\n")
    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .system_prompt(_PLANNER_PROMPT)
        .plan_mode()
        .with_partial_messages()
        .effort("high")
        .build()
    ) as agent:
        chunks: list[str] = []
        async for msg in agent.stream(
            f"Target: {target}\nTask: {task}\n\nCreate the attack plan."
        ):
            for token in iter_tokens(msg):
                sys.stdout.write(token)
                sys.stdout.flush()
                chunks.append(token)
            if isinstance(msg, ResultMessage):
                break
    print("\n")
    return "".join(chunks)


async def execute_plan(target: str, plan_text: str) -> None:
    """Execute the approved plan autonomously."""
    print("[execution mode — running plan…]\n")
    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .system_prompt(_EXECUTOR_PROMPT)
        .without_hitl()
        .with_partial_messages()
        .effort("high")
        .build()
    ) as agent:
        async for msg in agent.stream(
            f"Target: {target}\n\n"
            f"Approved plan to execute:\n{plan_text}\n\n"
            "Execute each step now."
        ):
            for token in iter_tokens(msg):
                sys.stdout.write(token)
                sys.stdout.flush()
            for call in iter_tool_calls(msg):
                print(f"\n  [tool] {call['name']}: {str(call['input'])[:60]}")
            if isinstance(msg, TaskStartedMessage):
                print(f"\n  [subagent] {msg.description}")
            if isinstance(msg, TaskNotificationMessage):
                print(f"\n  [subagent:{msg.status}] {msg.summary[:80]}")
            if isinstance(msg, ResultMessage):
                cost = agent.last_result.total_cost_usd
                print(f"\n\n[done] cost=${cost:.4f}  turns={agent.last_result.num_turns}")
                break


async def main(target: str, task: str, skip_review: bool) -> None:
    # Phase 1: plan
    plan_text = await get_plan(target, task)

    if not skip_review:
        print("─" * 60)
        print("Review the plan above. Proceed with execution?")
        answer = input("[y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return
        print()

    # Phase 2: execute
    await execute_plan(target, plan_text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plan-then-execute pattern")
    parser.add_argument("--target", default="http://localhost:8080",
                        help="Target URL, path, or description")
    parser.add_argument("--task", default="perform a web application security assessment",
                        help="Task description for the planner")
    parser.add_argument("--skip-review", action="store_true",
                        help="Auto-approve the plan without prompting")
    args = parser.parse_args()
    asyncio.run(main(args.target, args.task, args.skip_review))
