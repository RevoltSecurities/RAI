"""05_multi_agent_registry.py — ClaudeAgentRegistry with multiple named agents.

Demonstrates:
    ClaudeAgentRegistry.default() — auto-discovers all ~/.rai/agents/ dirs
    registry.build(name) — fresh RunnableClaudeAgent per agent
    Running multiple agents sequentially (pentest pipeline)
    Custom agent registration alongside auto-discovered agents
    plan_mode() — get structured plan before executing

Usage:
    python examples/claude/05_multi_agent_registry.py --target example.com
    python examples/claude/05_multi_agent_registry.py --list
    python examples/claude/05_multi_agent_registry.py --agent recon --target api.example.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rai.sdk import (
    ClaudeAgent,
    ClaudeAgentBuilder,
    ClaudeAgentRegistry,
    ResultMessage,
    full_text,
    iter_tokens,
    iter_tool_calls,
    TaskStartedMessage,
    TaskNotificationMessage,
)


async def stream_to_stdout(agent, prompt: str) -> str:
    """Stream agent response to stdout; return the full text."""
    chunks: list[str] = []
    async for msg in agent.stream(prompt):
        for token in iter_tokens(msg):
            sys.stdout.write(token)
            sys.stdout.flush()
            chunks.append(token)
        for call in iter_tool_calls(msg):
            print(f"\n  [tool] {call['name']}: {str(call['input'])[:60]}")
        if isinstance(msg, TaskStartedMessage):
            print(f"\n  [subagent] {msg.description}")
        if isinstance(msg, TaskNotificationMessage):
            print(f"\n  [subagent:{msg.status}] {msg.summary[:80]}")
        if isinstance(msg, ResultMessage):
            break
    print()
    return "".join(chunks)


async def run_pipeline(target: str) -> None:
    """Run a recon → analysis pipeline against target."""
    registry = ClaudeAgentRegistry.default()
    print(f"Registered agents: {registry.list()}\n")

    # Step 1 — Recon
    if "recon" in registry.list():
        print("=" * 60)
        print(f"[1/2] Recon agent — target: {target}")
        print("=" * 60)
        async with registry.build("recon") as recon:
            recon_findings = await stream_to_stdout(
                recon,
                f"Perform passive reconnaissance on {target}. "
                "List all discovered subdomains, IP ranges, and open services."
            )
        print(f"\nRecon cost: ${recon.last_result.total_cost_usd:.4f}\n")
    else:
        recon_findings = f"Target: {target}"
        print("(recon agent not found — using target string directly)\n")

    # Step 2 — SAST / vulnerability analysis
    if "sast-analyzer" in registry.list():
        print("=" * 60)
        print("[2/2] SAST analyzer — reviewing recon findings")
        print("=" * 60)
        async with registry.build("sast-analyzer") as sast:
            await stream_to_stdout(
                sast,
                f"Based on these recon findings, identify potential attack vectors "
                f"and recommend next exploitation steps:\n\n{recon_findings[:2000]}"
            )
        print(f"\nAnalysis cost: ${sast.last_result.total_cost_usd:.4f}")
    else:
        print("(sast-analyzer agent not found — pipeline complete)")


async def run_single(agent_name: str, target: str, plan_first: bool) -> None:
    """Run a single named agent from the registry."""
    registry = ClaudeAgentRegistry.default()

    if agent_name not in registry.list():
        print(f"Agent '{agent_name}' not found. Available: {registry.list()}")
        return

    if plan_first:
        print(f"[plan mode] agent={agent_name}\n")
        async with ClaudeAgent.builder().agent_name(agent_name).plan_mode().build() as agent:
            plan_text = await stream_to_stdout(
                agent,
                f"Create a structured recon and attack plan for target: {target}. "
                "List numbered steps."
            )
        proceed = input("\n\nExecute this plan? [y/N] ").strip().lower()
        if proceed != "y":
            return

    print(f"\n[execute] agent={agent_name}  target={target}\n")
    async with registry.build(agent_name) as agent:
        await stream_to_stdout(
            agent,
            f"Execute against target: {target}. Be thorough."
        )
    print(f"\nTotal cost: ${agent.last_result.total_cost_usd:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-agent registry example")
    parser.add_argument("--target", default="localhost", help="Target to assess")
    parser.add_argument("--agent", default=None, help="Run a single named agent")
    parser.add_argument("--list", action="store_true", help="List registered agents")
    parser.add_argument("--plan", action="store_true", help="Plan mode before execution")
    args = parser.parse_args()

    if args.list:
        reg = ClaudeAgentRegistry.default()
        print("Registered agents:")
        for name in reg.list():
            print(f"  {name}")
    elif args.agent:
        asyncio.run(run_single(args.agent, args.target, args.plan))
    else:
        asyncio.run(run_pipeline(args.target))
