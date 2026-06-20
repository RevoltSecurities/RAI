"""04_session_history.py — Browse, read, and manage Claude session history.

Demonstrates:
    ClaudeSessionManager — list, info, messages, subagents
    full_history() — one-call dict with all data
    resume_options() — feed directly into ClaudeAgentBuilder
    rename(), tag(), fork() mutations

Usage:
    python examples/claude/04_session_history.py
    python examples/claude/04_session_history.py --session <uuid>
    python examples/claude/04_session_history.py --list
    python examples/claude/04_session_history.py --session <uuid> --action fork
    python examples/claude/04_session_history.py --session <uuid> --action rename --title "my session"
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from rai.sdk import (
    ClaudeAgent,
    ClaudeSessionManager,
    SDKSessionInfo,
    SessionMessage,
    ResultMessage,
    iter_tokens,
)


def fmt_ts(ms: int | None) -> str:
    if not ms:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def print_session(s: SDKSessionInfo) -> None:
    print(f"  {s.session_id[:16]}…  {fmt_ts(s.last_modified)}  {(s.summary or '')[:60]}")
    if s.tag:
        print(f"    tag={s.tag}")


def print_message(msg: SessionMessage, index: int) -> None:
    role = msg.type.upper()
    content = msg.message
    if isinstance(content, dict):
        parts = content.get("content", "")
        if isinstance(parts, list):
            text = " ".join(
                p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = str(parts)
    else:
        text = str(content)
    print(f"  [{index:3d}] {role:9s} {text[:100]}")


def cmd_list(mgr: ClaudeSessionManager, limit: int) -> None:
    sessions = mgr.list(limit=limit)
    print(f"\n{len(sessions)} sessions (most recent first):\n")
    for s in sessions:
        print_session(s)


def cmd_show(mgr: ClaudeSessionManager, session_id: str) -> None:
    info = mgr.info(session_id)
    if not info:
        print(f"Session {session_id} not found.")
        return

    print(f"\n=== Session {session_id[:16]}… ===")
    print(f"  summary   : {info.summary}")
    print(f"  cwd       : {info.cwd}")
    print(f"  git_branch: {info.git_branch}")
    print(f"  created   : {fmt_ts(info.created_at)}")
    print(f"  modified  : {fmt_ts(info.last_modified)}")
    print(f"  tag       : {info.tag}")

    print(f"\nMessages:")
    for i, msg in enumerate(mgr.messages(session_id)):
        print_message(msg, i)

    agent_ids = mgr.subagents(session_id)
    if agent_ids:
        print(f"\nSubagents ({len(agent_ids)}):")
        for aid in agent_ids:
            sub_msgs = mgr.subagent_messages(session_id, aid)
            print(f"  {aid[:16]}…  ({len(sub_msgs)} messages)")


def cmd_fork(mgr: ClaudeSessionManager, session_id: str) -> None:
    result = mgr.fork(session_id)
    print(f"Forked → new session_id: {result.session_id}")


async def cmd_resume(session_id: str, agent_name: str) -> None:
    mgr = ClaudeSessionManager()
    opts = mgr.resume_options(session_id)
    print(f"Resuming session {session_id[:16]}…\n")

    async with (
        ClaudeAgent.builder()
        .agent_name(agent_name)
        .resume(opts["resume"])
        .without_hitl()
        .with_partial_messages()
        .build()
    ) as agent:
        prompt = input("Continue with> ").strip() or "Summarise what we discussed so far."
        print("claude> ", end="", flush=True)
        import sys
        async for msg in agent.stream(prompt):
            for token in iter_tokens(msg):
                sys.stdout.write(token)
                sys.stdout.flush()
            if isinstance(msg, ResultMessage):
                break
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Session history browser")
    parser.add_argument("--session", default=None, metavar="UUID")
    parser.add_argument("--list", action="store_true", help="List recent sessions")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--agent", default="rai")
    parser.add_argument("--action", choices=["show", "fork", "resume", "rename", "tag"],
                        default="show")
    parser.add_argument("--title", default="", help="New title for rename action")
    parser.add_argument("--tag-value", default="", dest="tag_value",
                        help="Tag string (empty = clear)")
    args = parser.parse_args()

    mgr = ClaudeSessionManager()

    if args.list or not args.session:
        cmd_list(mgr, args.limit)
    elif args.action == "show":
        cmd_show(mgr, args.session)
    elif args.action == "fork":
        cmd_fork(mgr, args.session)
    elif args.action == "resume":
        asyncio.run(cmd_resume(args.session, args.agent))
    elif args.action == "rename":
        if not args.title:
            print("Pass --title 'new name'")
        else:
            mgr.rename(args.session, args.title)
            print(f"Renamed {args.session[:16]}… → {args.title!r}")
    elif args.action == "tag":
        tag = args.tag_value or None
        mgr.tag(args.session, tag)
        print(f"Tagged {args.session[:16]}… → {tag!r}")
