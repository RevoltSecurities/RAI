"""03_multi_turn_chat.py — Multi-turn interactive REPL using a single session.

Demonstrates:
    Keeping ClaudeSDKClient alive across multiple query() calls
    session_id persisting across turns (same subprocess)
    resume() — reconnect to an existing session by ID
    ClaudeAgentBuilder chaining

Usage:
    python examples/claude/03_multi_turn_chat.py
    python examples/claude/03_multi_turn_chat.py --agent recon
    python examples/claude/03_multi_turn_chat.py --resume <session-id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rai.sdk import (
    ClaudeAgent,
    ResultMessage,
    iter_tokens,
    iter_tool_calls,
    TaskStartedMessage,
    TaskNotificationMessage,
)

BANNER = """
╭─ RAI Claude — multi-turn chat ─────────────────────────────────────╮
│  Commands: /quit  /session  /cost                                   │
╰─────────────────────────────────────────────────────────────────────╯
"""


async def chat_loop(agent_name: str, resume_id: str | None, model: str | None) -> None:
    builder = ClaudeAgent.builder().agent_name(agent_name).without_hitl().with_partial_messages()
    if model:
        builder = builder.model(model)
    if resume_id:
        builder = builder.resume(resume_id)
        print(f"Resuming session {resume_id[:16]}…")

    print(BANNER)

    async with builder.build() as agent:
        turn = 0
        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                continue
            if user_input == "/quit":
                break
            if user_input == "/session":
                print(f"  session_id: {agent.session_id or 'none yet'}")
                continue
            if user_input == "/cost":
                r = agent.last_result
                if r:
                    print(f"  cost=${r.total_cost_usd:.4f}  turns={r.num_turns}")
                else:
                    print("  no run yet")
                continue

            turn += 1
            print("claude> ", end="", flush=True)

            async for msg in agent.stream(user_input):
                for token in iter_tokens(msg):
                    sys.stdout.write(token)
                    sys.stdout.flush()

                for call in iter_tool_calls(msg):
                    print(f"\n  [tool] {call['name']}")

                if isinstance(msg, TaskStartedMessage):
                    print(f"\n  [subagent] {msg.description}")
                elif isinstance(msg, TaskNotificationMessage):
                    print(f"\n  [subagent:{msg.status}] {msg.summary[:60]}")

                if isinstance(msg, ResultMessage):
                    break

            print()  # newline after streaming response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-turn chat REPL")
    parser.add_argument("--agent", default="rai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--resume", default=None, metavar="SESSION_ID",
                        help="Resume an existing session by UUID")
    args = parser.parse_args()
    asyncio.run(chat_loop(args.agent, args.resume, args.model))
