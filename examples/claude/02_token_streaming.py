"""02_token_streaming.py — Token-by-token streaming with all event types.

Demonstrates:
    agent.stream() — yields every SDK message type
    iter_tokens()  — extract text tokens from StreamEvent
    iter_thinking() — extract thinking chunks
    iter_tool_calls() — extract completed tool calls from AssistantMessage
    TaskStartedMessage / TaskProgressMessage / TaskNotificationMessage — subagents
    RateLimitEvent — rate limit warnings
    ResultMessage — final cost + session_id

Usage:
    python examples/claude/02_token_streaming.py
    python examples/claude/02_token_streaming.py --prompt "scan localhost for open services"
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rai.sdk import (
    ClaudeAgent,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    RateLimitEvent,
    AssistantMessage,
    full_text,
    iter_thinking,
    iter_tokens,
    iter_tool_calls,
)


async def main(agent_name: str, prompt: str) -> None:
    async with (
        ClaudeAgent.builder()
        .agent_name(agent_name)
        .without_hitl()
        .with_partial_messages()
        .build()
    ) as agent:
        thinking_buffer: list[str] = []
        tool_calls: list[dict] = []

        print(f"[session starting — agent={agent_name}]\n")

        async for msg in agent.stream(prompt):

            # --- text tokens (stream to stdout in real time) ---
            for token in iter_tokens(msg):
                sys.stdout.write(token)
                sys.stdout.flush()

            # --- thinking chunks (buffer silently, print at end) ---
            for chunk in iter_thinking(msg):
                thinking_buffer.append(chunk)

            # --- completed tool calls (from AssistantMessage) ---
            for call in iter_tool_calls(msg):
                tool_calls.append(call)
                print(f"\n  [tool] {call['name']} ← {list(call['input'].keys())}")

            # --- subagent lifecycle ---
            if isinstance(msg, TaskStartedMessage):
                print(f"\n  [subagent:start] {msg.task_id[:8]} — {msg.description}")
            elif isinstance(msg, TaskProgressMessage):
                print(f"\n  [subagent:tick]  {msg.task_id[:8]} — {msg.last_tool_name} "
                      f"({msg.usage['total_tokens']} tokens)")
            elif isinstance(msg, TaskNotificationMessage):
                print(f"\n  [subagent:{msg.status}] {msg.task_id[:8]} — {msg.summary[:80]}")

            # --- rate limit warning ---
            elif isinstance(msg, RateLimitEvent):
                info = msg.rate_limit_info
                print(f"\n  [rate-limit] status={info.status} "
                      f"utilization={info.utilization:.0%}")

            # --- end of turn ---
            elif isinstance(msg, ResultMessage):
                break

    result = agent.last_result
    print(f"\n\n--- done ---")
    print(f"session  : {result.session_id[:16]}…")
    print(f"turns    : {result.num_turns}")
    print(f"cost     : ${result.total_cost_usd:.4f}" if result.total_cost_usd else "cost: N/A")
    if thinking_buffer:
        print(f"\n--- thinking ({len(''.join(thinking_buffer))} chars) ---")
        print("".join(thinking_buffer)[:300], "…" if len("".join(thinking_buffer)) > 300 else "")
    if tool_calls:
        print(f"\n--- tool calls ({len(tool_calls)}) ---")
        for c in tool_calls:
            print(f"  {c['name']}: {str(c['input'])[:80]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Token streaming example")
    parser.add_argument("--agent", default="rai")
    parser.add_argument("--prompt", default="List the OWASP Top 5 vulnerabilities with one-line descriptions.")
    args = parser.parse_args()
    asyncio.run(main(args.agent, args.prompt))
