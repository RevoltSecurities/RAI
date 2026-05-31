"""13_builtin_subagents.py — Using RAI's built-in subagent team with per-subagent model config.

Demonstrates:
  - How the default subagents (recon, researcher, coder, sast-analyzer, agent-creator) are
    loaded automatically from ~/.rai/agents/<name>/AGENTS.md + config.toml
  - Giving each subagent a different model via RAIAgentBuilder or config.toml
  - Running a multi-agent engagement where the main agent dispatches to specialists
  - Per-subagent model override via subagent_tools_map and config priority order

Built-in subagents loaded by default (from ~/.rai/agents/):
    recon          — network/asset reconnaissance, subdomain enum, port scanning
    researcher     — CVE research, OSINT, HackerOne prior art, threat intel
    coder          — exploit scripts, PoC builders, Nuclei templates
    sast-analyzer  — semgrep, bandit, gosec, secret scanning, dependency audits
    agent-creator  — designs and registers new subagents interactively

Model priority order for each subagent:
    1. Explicit "model" key in custom_subagents dict     ← highest
    2. ~/.rai/agents/<name>/config.toml  model field
    3. AGENTS.md frontmatter model field
    4. Parent agent model (inherited)                    ← lowest

Usage:
    # First configure subagent models (one-time setup):
    rai agents config-set recon      --model groq/llama-3.3-70b-versatile --api-key gsk_...
    rai agents config-set researcher --model gemini/gemini-2.0-flash       --api-key AIza...
    rai agents config-set coder      --model openai/gpt-4o                 --api-key sk-...
    rai agents config-set sast-analyzer --model anthropic:claude-haiku-4-5 --api-key sk-ant-...

    # Then run:
    python examples/13_builtin_subagents.py --target example.com
    python examples/13_builtin_subagents.py --target example.com --model anthropic:claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import asyncio

from rai import DEFAULT_MODEL
from rai.sdk import RAIAgentBuilder


# ---------------------------------------------------------------------------
# System prompt — instructs the main agent to delegate to specialists
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an AI security operator coordinating a specialist team for a full security assessment.

Your team:
  - recon       → delegate all asset discovery, subdomain enum, port scanning
  - researcher  → delegate CVE research, exploit PoC hunting, threat intelligence
  - coder       → delegate exploit script writing, Nuclei template creation, automation
  - sast-analyzer → delegate static code analysis, secret scanning, dependency audits

Workflow:
1. Start with recon to map the attack surface
2. Run researcher in parallel to gather CVE/threat intelligence on discovered services
3. Use coder to write targeted exploit scripts or Nuclei templates for confirmed issues
4. Run sast-analyzer on any code repositories in scope
5. Synthesise all findings into a structured report

Always delegate specialised work to the right subagent — do not do it yourself.
"""


async def run(target: str, model: str, api_key: str, base_url: str) -> None:
    """
    Build a main agent that automatically loads all built-in subagents.

    Each subagent reads its own ~/.rai/agents/<name>/config.toml for model,
    api_key, and base_url. If no config exists for a subagent, it inherits
    the main agent's model and credentials.
    """
    builder = (
        RAIAgentBuilder()
        .agent_name("rai")          # loads ~/.rai/agents/rai/AGENTS.md which defines
        .model(model)               # recon, researcher, coder, sast-analyzer, agent-creator
        .system_prompt(SYSTEM_PROMPT)
        .target(target)
        .with_hitl()
        # with_subagents() is the default — no need to call it explicitly.
        # Subagents are loaded from:
        #   1. ~/.rai/agents/rai/AGENTS.md (agent definitions)
        #   2. ~/.rai/agents/rai/subagents/*.toml (TOML overrides)
        # Each subagent picks up its own model from its config.toml.
        # .without_subagents() would skip all of this.
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        print(f"Starting multi-agent assessment of {target}")
        print(f"Main agent: {model}")
        print("Subagents: recon, researcher, coder, sast-analyzer (each with own model)\n")

        await agent.run(
            f"Conduct a full security assessment of {target}. "
            "Start with reconnaissance, then research, then exploitation assistance, "
            "then static analysis if source code is available. "
            "Compile all findings into a structured pentest report."
        )
        print(f"\nAssessment complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI built-in subagent team example")
    parser.add_argument("--target", required=True, help="Target hostname, IP, or URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Main agent model")
    parser.add_argument("--api-key", default="", help="API key for main agent")
    parser.add_argument("--base-url", default="", help="Custom API base URL")
    args = parser.parse_args()
    asyncio.run(run(args.target, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
