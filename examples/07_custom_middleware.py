"""07_custom_middleware.py — Custom AgentMiddleware subclasses + built-in middleware.

Demonstrates: AgentMiddleware API (before_request, after_response, on_tool_start,
              on_tool_end), add_middleware(), composing multiple middleware layers,
              ModelCallLoggerMiddleware from rai.sdk.middleware.

Custom middleware implemented:
  1. RequestLoggerMiddleware   — logs every LLM request to stderr
  2. TokenBudgetMiddleware     — aborts after N input tokens (circuit breaker)
  3. ScopeEnforcerMiddleware   — blocks tool calls targeting out-of-scope hosts
  4. FindingsTaggerMiddleware  — auto-tags tool results containing CVEs
  5. TimingMiddleware          — records per-tool execution latency

Built-in middleware used:
  6. ModelCallLoggerMiddleware — structured JSONL log of every model invocation
     (rai.sdk.middleware or RAI_DEBUG_LOG_CALLS=1 env var)

Modular import alternative:
    from rai.sdk.middleware import ModelCallLoggerMiddleware, RateLimitMiddleware
    from rai.sdk.builder   import RAIAgentBuilder
    from rai.sdk.tools     import get_security_tools, get_builtin_tools

Usage:
    python examples/07_custom_middleware.py --task "scan 192.168.1.1"
    python examples/07_custom_middleware.py --log-calls  # enable model call logger
    python examples/07_custom_middleware.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from typing import Any

from rai import DEFAULT_MODEL
from rai.sdk import RAIAgentBuilder, get_builtin_tools, get_security_tools
from rai.sdk.middleware import ModelCallLoggerMiddleware

# AgentMiddleware is the deepagents base class for all middleware
try:
    from langchain.agents.middleware.types import AgentMiddleware
except ImportError:
    from deepagents.middleware.base import AgentMiddleware  # type: ignore[no-redef]


class RequestLoggerMiddleware(AgentMiddleware):
    """Log every LLM request message count and estimated token size."""

    def before_request(self, messages: list[Any], **kwargs: Any) -> list[Any]:
        total_chars = sum(len(str(m)) for m in messages)
        print(f"[RequestLogger] {len(messages)} messages, ~{total_chars // 4} tokens", file=sys.stderr)
        return messages


class TokenBudgetMiddleware(AgentMiddleware):
    """Abort the agent turn when input token budget is exceeded."""

    def __init__(self, max_tokens: int = 50_000) -> None:
        self._max = max_tokens
        self._spent = 0

    def before_request(self, messages: list[Any], **kwargs: Any) -> list[Any]:
        estimated = sum(len(str(m)) for m in messages) // 4
        self._spent += estimated
        if self._spent > self._max:
            raise RuntimeError(
                f"[TokenBudget] Budget exceeded: {self._spent} > {self._max} tokens"
            )
        return messages


class ScopeEnforcerMiddleware(AgentMiddleware):
    """Block tool calls targeting hosts outside the allowed scope."""

    _HOST_RE = re.compile(r"(?:https?://)?([a-zA-Z0-9.\-_]+)")

    def __init__(self, allowed_prefixes: list[str]) -> None:
        self._allowed = allowed_prefixes

    def on_tool_start(self, tool_name: str, tool_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        combined = " ".join(str(v) for v in tool_input.values())
        for m in self._HOST_RE.finditer(combined):
            host = m.group(1)
            if not any(host.startswith(p) for p in self._allowed):
                raise PermissionError(f"[ScopeEnforcer] Blocked: {host} not in scope {self._allowed}")
        return tool_input


class FindingsTaggerMiddleware(AgentMiddleware):
    """Auto-print a notice whenever a tool result contains a CVE identifier."""

    _CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

    def on_tool_end(self, tool_name: str, tool_output: Any, **kwargs: Any) -> Any:
        text = str(tool_output)
        cves = self._CVE_RE.findall(text)
        if cves:
            unique = sorted(set(cves))
            print(f"[FindingsTagger] {tool_name} → found CVEs: {', '.join(unique)}", file=sys.stderr)
        return tool_output


class TimingMiddleware(AgentMiddleware):
    """Record per-tool wall-clock latency and print a summary after each turn."""

    def __init__(self) -> None:
        self._starts: dict[str, float] = {}
        self._totals: dict[str, float] = {}

    def on_tool_start(self, tool_name: str, tool_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self._starts[tool_name] = time.monotonic()
        return tool_input

    def on_tool_end(self, tool_name: str, tool_output: Any, **kwargs: Any) -> Any:
        elapsed = time.monotonic() - self._starts.pop(tool_name, time.monotonic())
        self._totals[tool_name] = self._totals.get(tool_name, 0.0) + elapsed
        print(f"[Timing] {tool_name}: {elapsed:.2f}s (total: {self._totals[tool_name]:.2f}s)", file=sys.stderr)
        return tool_output


async def run_with_middleware(
    task: str,
    model: str,
    api_key: str,
    base_url: str,
    log_calls: bool = False,
) -> None:
    tools = get_security_tools() + get_builtin_tools()

    builder = (
        RAIAgentBuilder()
        .model(model)
        .add_tools(tools)
        .without_hitl()
        .add_middleware(RequestLoggerMiddleware())
        .add_middleware(TokenBudgetMiddleware(max_tokens=100_000))
        .add_middleware(ScopeEnforcerMiddleware(allowed_prefixes=["192.168.", "10.", "localhost"]))
        .add_middleware(FindingsTaggerMiddleware())
        .add_middleware(TimingMiddleware())
    )
    if log_calls:
        # ModelCallLoggerMiddleware writes every model invocation to
        # ~/.rai/debug/model-calls.jsonl (override with RAI_DEBUG_LOG_FILE).
        builder = builder.add_middleware(ModelCallLoggerMiddleware())
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(task)
        print(f"\nTask complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI Custom Middleware Demo")
    parser.add_argument("--task", default="List all available security tools and describe each one.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--log-calls", action="store_true",
                        help="Enable ModelCallLoggerMiddleware (writes to ~/.rai/debug/model-calls.jsonl)")
    args = parser.parse_args()
    asyncio.run(run_with_middleware(args.task, args.model, args.api_key, args.base_url, args.log_calls))


if __name__ == "__main__":
    main()
