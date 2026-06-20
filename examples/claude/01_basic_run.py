"""01_basic_run.py — Minimal single-turn run with ClaudeAgent.

No API key needed — uses your Claude Code subscription.

Demonstrates:
    ClaudeAgent.builder(), .without_hitl(), .build()
    RunnableClaudeAgent as async context manager
    agent.run() → RunResult (blocking, returns after first ResultMessage)

Usage:
    python examples/claude/01_basic_run.py
    python examples/claude/01_basic_run.py --agent recon --prompt "list open ports on localhost"
    python examples/claude/01_basic_run.py --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import asyncio

from rai.sdk import ClaudeAgent, RunResult


async def main(agent_name: str, model: str | None, prompt: str) -> None:
    builder = (
        ClaudeAgent.builder()
        .agent_name(agent_name)
        .without_hitl()
        .with_partial_messages()
    )
    if model:
        builder = builder.model(model)

    async with builder.build() as agent:
        print(f"agent={agent_name}  session starting…\n")
        result: RunResult = await agent.run(prompt)

    print(f"\n--- result ---")
    print(f"session_id : {result.session_id}")
    print(f"turns      : {result.num_turns}")
    print(f"cost       : ${result.total_cost_usd:.4f}" if result.total_cost_usd else "cost: N/A")
    print(f"error      : {result.is_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Minimal ClaudeAgent run")
    parser.add_argument("--agent", default="rai", help="Agent name (default: rai)")
    parser.add_argument("--model", default=None, help="Model override e.g. claude-opus-4-8")
    parser.add_argument("--prompt", default="Say hello and list 3 common web vulnerabilities.")
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.model, args.prompt))
