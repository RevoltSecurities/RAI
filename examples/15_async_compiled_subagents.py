"""15_async_compiled_subagents.py — AsyncSubAgent and CompiledSubAgent patterns.

Demonstrates three advanced subagent patterns beyond plain SubAgent dicts:

  AsyncSubAgent    — delegates to a remote Agent Protocol / LangGraph server.
                     The main agent dispatches work and monitors it as a
                     background task. Compatible with LangGraph Platform
                     (managed) and any self-hosted LangGraph server.

  CompiledSubAgent — injects a pre-built Pregel graph (LangGraph StateGraph)
                     directly as a subagent. The graph runs in-process; the
                     main agent waits for its final message and receives it
                     as a ToolMessage. Useful for embedding a highly customised
                     graph that you built outside RAI.

  Mixed team       — combine all three types (SubAgent dict, AsyncSubAgent,
                     CompiledSubAgent) in a single add_subagents() call.

Key differences from plain SubAgent dicts:
  SubAgent dict    → built from a system_prompt; RAI creates the graph at startup
  AsyncSubAgent    → connects to an external server via Agent Protocol (HTTP)
  CompiledSubAgent → you supply a ready-made Runnable / Pregel graph

Notes:
  - AsyncSubAgent and CompiledSubAgent are passed through unchanged by RAI;
    model resolution and EmptyContentSanitizerMiddleware injection only apply
    to plain SubAgent specs.
  - The remote server for AsyncSubAgent must expose the LangGraph API
    (rai serve, LangGraph Platform, or any Agent Protocol server).
  - CompiledSubAgent runnable state schema must include a 'messages' key.

Usage:
    # AsyncSubAgent example (requires a running RAI server):
    rai serve --agent recon --port 2024 &
    python examples/15_async_compiled_subagents.py --mode async --target example.com

    # CompiledSubAgent example (runs fully in-process):
    python examples/15_async_compiled_subagents.py --mode compiled --target example.com

    # Mixed team:
    python examples/15_async_compiled_subagents.py --mode mixed --target example.com
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from rai import DEFAULT_MODEL
from rai.sdk import (
    AsyncSubAgent,
    CompiledSubAgent,
    RAIAgentBuilder,
    get_security_tools,
)


# ---------------------------------------------------------------------------
# Pattern 1 — AsyncSubAgent (remote Agent Protocol server)
# ---------------------------------------------------------------------------

def make_async_subagents(
    recon_url: str = "http://127.0.0.1:2024",
    api_key: str = "",
) -> list[AsyncSubAgent]:
    """
    Define subagents that delegate to remote LangGraph / Agent Protocol servers.

    Each AsyncSubAgent connects to a server via HTTP and runs its graph_id
    as a background task. The main agent dispatches and monitors it.

    Authentication:
      - LangGraph Platform: set LANGGRAPH_API_KEY env var (auto-detected by SDK)
      - Self-hosted with auth: pass custom headers={"Authorization": "Bearer ..."}
      - Self-hosted without auth: no headers needed
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return [
        AsyncSubAgent(
            name="remote-recon",
            description=(
                "Remote reconnaissance agent running on a separate server. "
                "Use for attack surface mapping, subdomain enum, port scanning."
            ),
            graph_id="recon",           # graph name registered on the remote server
            url=recon_url,              # remote RAI/LangGraph server URL
            headers=headers,
        ),
        AsyncSubAgent(
            name="remote-researcher",
            description=(
                "Remote CVE research agent. Use for vulnerability research, "
                "exploit hunting, threat intelligence."
            ),
            graph_id="researcher",
            url=recon_url,              # can point to the same server (multiple graphs)
            headers=headers,
        ),
    ]


async def run_async_subagents(
    target: str,
    model: str,
    api_key: str,
    server_url: str,
    server_key: str,
) -> None:
    """
    Main agent that delegates to remote subagents via AsyncSubAgent.

    The remote server must be running (e.g. `rai serve --port 2024`).
    """
    async_subagents = make_async_subagents(
        recon_url=server_url,
        api_key=server_key,
    )

    builder = (
        RAIAgentBuilder()
        .agent_name("rai")
        .model(model)
        .without_subagents()                    # skip local AGENTS.md loading
        .add_subagents(async_subagents)         # use remote subagents instead
        .add_tools(get_security_tools())
        .target(target)
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)

    async with await builder.build() as agent:
        print(f"AsyncSubAgent example — delegating to remote server at {server_url}")
        print(f"Target: {target}\n")
        await agent.run(
            f"Assess {target}. Use remote-recon for surface mapping and "
            "remote-researcher for CVE intelligence. Summarise findings."
        )
        print(f"\nDone. Thread: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Pattern 2 — CompiledSubAgent (pre-built in-process graph)
# ---------------------------------------------------------------------------

def build_compiled_subagent() -> CompiledSubAgent:
    """
    Build a custom LangGraph StateGraph and wrap it as a CompiledSubAgent.

    The runnable's state schema MUST include a 'messages' key — RAI extracts
    the final message and returns it as a ToolMessage to the parent agent.

    This pattern is useful when you need full control over the subagent's
    internal graph structure (custom state, conditional edges, tool selection)
    without going through create_rai_agent().
    """
    from typing import TypedDict

    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langgraph.graph import END, StateGraph

    class ReconState(TypedDict):
        messages: list[BaseMessage]
        target: str
        findings: list[str]

    # Use a lightweight model for this in-process subagent
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        max_tokens=2048,
    )

    async def analyse_node(state: ReconState) -> dict[str, Any]:
        """Core analysis step — calls LLM directly."""
        human_msg = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            None,
        )
        prompt = human_msg.content if human_msg else f"Analyse target: {state['target']}"

        system = SystemMessage(content=(
            "You are a lightweight security analyst. Given a target or task, "
            "provide a concise structured analysis. Be brief and factual."
        ))
        response = await llm.ainvoke([system, HumanMessage(content=str(prompt))])
        return {
            "messages": state["messages"] + [response],
            "findings": state["findings"] + [str(response.content)],
        }

    def should_continue(state: ReconState) -> str:
        """Simple termination — stop after one analysis turn."""
        last = state["messages"][-1] if state["messages"] else None
        if isinstance(last, AIMessage):
            return END
        return "analyse"

    graph = StateGraph(ReconState)
    graph.add_node("analyse", analyse_node)
    graph.set_entry_point("analyse")
    graph.add_conditional_edges("analyse", should_continue)
    compiled = graph.compile()

    return CompiledSubAgent(
        name="in-process-analyst",
        description=(
            "Lightweight in-process security analyst. Use for quick analysis tasks "
            "that don't need shell access or external tools."
        ),
        runnable=compiled,
    )


async def run_compiled_subagent(target: str, model: str, api_key: str) -> None:
    """
    Main agent that uses a pre-built LangGraph graph as a subagent.
    The graph runs in-process — no network call, no separate server.
    """
    compiled_sub = build_compiled_subagent()

    builder = (
        RAIAgentBuilder()
        .agent_name("rai")
        .model(model)
        .without_subagents()
        .add_subagent(compiled_sub)         # inject the compiled graph
        .add_tools(get_security_tools())
        .target(target)
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)

    async with await builder.build() as agent:
        print("CompiledSubAgent example — in-process LangGraph subagent")
        print(f"Target: {target}\n")
        await agent.run(
            f"Analyse {target} for potential attack vectors. "
            "Use in-process-analyst for a quick initial assessment, "
            "then expand on the most critical findings."
        )
        print(f"\nDone. Thread: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Pattern 3 — Mixed team (SubAgent dict + AsyncSubAgent + CompiledSubAgent)
# ---------------------------------------------------------------------------

async def run_mixed_team(
    target: str,
    model: str,
    api_key: str,
    server_url: str,
    server_key: str,
) -> None:
    """
    Combine all three subagent types in a single team.

    The main agent decides which specialist to call based on the task:
      - Plain SubAgent dict  → RAI builds its graph at startup (most common)
      - AsyncSubAgent        → delegates to a remote server
      - CompiledSubAgent     → runs a pre-built in-process graph
    """
    # Plain SubAgent dict — RAI builds this graph internally
    coder_subagent: dict = {
        "name": "coder",
        "description": "Write exploit scripts and security automation tools.",
        "system_prompt": (
            "You are an expert exploit developer. "
            "Write clean, parameterised Python scripts for confirmed vulnerabilities."
        ),
        "model": model,
    }

    # AsyncSubAgent — delegates to a remote server
    remote_recon = AsyncSubAgent(
        name="remote-recon",
        description="Remote recon agent for attack surface mapping.",
        graph_id="recon",
        url=server_url,
        headers={"Authorization": f"Bearer {server_key}"} if server_key else {},
    )

    # CompiledSubAgent — in-process pre-built graph
    compiled_analyst = build_compiled_subagent()

    builder = (
        RAIAgentBuilder()
        .agent_name("rai")
        .model(model)
        .without_subagents()
        .add_subagents([coder_subagent, remote_recon, compiled_analyst])
        .add_tools(get_security_tools())
        .target(target)
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)

    async with await builder.build() as agent:
        print("Mixed team: plain SubAgent + AsyncSubAgent + CompiledSubAgent")
        print(f"  coder           → plain SubAgent (built in-process by RAI)")
        print(f"  remote-recon    → AsyncSubAgent (remote server at {server_url})")
        print(f"  in-process-analyst → CompiledSubAgent (pre-built LangGraph graph)")
        print(f"\nTarget: {target}\n")
        await agent.run(
            f"Assess {target}. Use in-process-analyst for quick initial triage, "
            "remote-recon for deep surface mapping, and coder to write any needed "
            "exploit scripts for confirmed vulnerabilities."
        )
        print(f"\nDone. Thread: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAI AsyncSubAgent and CompiledSubAgent examples"
    )
    parser.add_argument("--target", required=True, help="Target hostname, IP, or URL")
    parser.add_argument(
        "--mode",
        choices=["async", "compiled", "mixed"],
        default="compiled",
        help=(
            "async=remote AsyncSubAgent, "
            "compiled=in-process CompiledSubAgent, "
            "mixed=all three types combined"
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY", ""))
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:2024",
        help="Remote RAI/LangGraph server URL (for async mode)",
    )
    parser.add_argument(
        "--server-key",
        default="",
        help="Remote server API key (for async mode)",
    )
    args = parser.parse_args()

    if args.mode == "async":
        asyncio.run(run_async_subagents(
            args.target, args.model, args.api_key,
            args.server_url, args.server_key,
        ))
    elif args.mode == "compiled":
        asyncio.run(run_compiled_subagent(args.target, args.model, args.api_key))
    else:
        asyncio.run(run_mixed_team(
            args.target, args.model, args.api_key,
            args.server_url, args.server_key,
        ))


if __name__ == "__main__":
    main()
