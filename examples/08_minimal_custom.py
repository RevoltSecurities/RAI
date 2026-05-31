"""08_minimal_custom.py — Minimal agent with zero native tools and custom tool patterns.

Demonstrates: without_native_tools(), without_shell(), without_memory(),
              without_skills(), without_subagents(), @tool decorator,
              StructuredTool.from_function(), and direct RAIAgentBuilder wiring.

This is the smallest possible RAI agent — useful as a starting template.

Both flat and modular imports work identically:
    # Flat (recommended for most use cases):
    from rai.sdk import RAIAgentBuilder, CompositeBackend

    # Modular (use when importing only a specific sub-package):
    from rai.sdk.builder import RAIAgentBuilder
    from deepagents.backends import CompositeBackend

Usage:
    python examples/08_minimal_custom.py --task "check if 443 is open on 1.1.1.1"
    python examples/08_minimal_custom.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import socket
from typing import Annotated

from langchain_core.tools import StructuredTool, tool
from pydantic import BaseModel, Field

from rai import DEFAULT_MODEL
from rai.sdk import CompositeBackend, RAIAgentBuilder


# ---------------------------------------------------------------------------
# Custom tools — two patterns
# ---------------------------------------------------------------------------


@tool
def check_port(host: str, port: int) -> str:
    """Check if a TCP port is open on a host. Returns 'open' or 'closed'."""
    try:
        with socket.create_connection((host, port), timeout=3):
            return f"{host}:{port} is open"
    except OSError:
        return f"{host}:{port} is closed"


class ReverseDnsInput(BaseModel):
    ip: Annotated[str, Field(description="IPv4 address to reverse-resolve")]


def _reverse_dns(ip: str) -> str:
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return f"{ip} → {hostname}"
    except socket.herror:
        return f"{ip} → no PTR record"


reverse_dns = StructuredTool.from_function(
    func=_reverse_dns,
    name="reverse_dns",
    description="Perform a reverse DNS lookup for an IP address.",
    args_schema=ReverseDnsInput,
)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


async def run_minimal(task: str, model: str, api_key: str, base_url: str) -> None:
    builder = (
        RAIAgentBuilder()
        .model(model)
        .without_native_tools()
        .without_shell()
        .without_memory()
        .without_skills()
        .without_subagents()
        .without_audit_log()
        .backend(CompositeBackend(backends=[]))
        .add_tools([check_port, reverse_dns])
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        result = await agent.run(task)
        print(f"\nResult: {result}")
        print(f"Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI Minimal Custom-Tools Agent")
    parser.add_argument("--task", default="Check if port 443 is open on 1.1.1.1 and do a reverse DNS lookup.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    asyncio.run(run_minimal(args.task, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
