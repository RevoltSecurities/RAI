"""14_custom_subagents.py — Registering fully custom subagents with per-subagent models.

Demonstrates:
  - Defining custom subagents inline via add_subagent() / add_subagents()
  - Giving each subagent a completely different model, API key, and system prompt
  - Mixing custom subagents with built-in subagents
  - Using without_subagents() + custom list for a fully controlled team
  - CompiledSubAgent for injecting a pre-built Pregel graph as a subagent
  - AsyncSubAgent for delegating to a remote Agent Protocol server

Custom subagent dict fields:
    name           (required) — identifier used by the main agent to dispatch
    description    (required) — shown to the LLM so it knows when to delegate
    system_prompt  (required) — full system prompt for this subagent
    model          (optional) — own model; falls back to parent if absent
    api_key        (optional) — own API key; falls back to parent if absent
    base_url       (optional) — own base URL / proxy endpoint
    tools          (optional) — list of extra BaseTool instances

Usage:
    python examples/14_custom_subagents.py --target example.com
    python examples/14_custom_subagents.py \\
        --main-model anthropic:claude-sonnet-4-6 --main-key sk-ant-... \\
        --fast-model groq/llama-3.3-70b-versatile --fast-key gsk_... \\
        --code-model openai/gpt-4o --code-key sk-...
"""

from __future__ import annotations

import argparse
import asyncio

from rai import DEFAULT_MODEL
from rai.sdk import RAIAgentBuilder, get_security_tools, get_web_tools


# ---------------------------------------------------------------------------
# Custom subagent definitions
# ---------------------------------------------------------------------------

def build_subagents(
    fast_model: str,
    fast_key: str,
    code_model: str,
    code_key: str,
) -> list[dict]:
    """
    Define the custom subagent team.

    Each entry is a plain dict. The model/api_key fields are optional —
    omit them to inherit the main agent's credentials.
    """
    return [
        # ── Fast surface mapper — cheap/fast model for high-volume probing ──
        {
            "name": "surface-mapper",
            "description": (
                "Fast attack surface mapping: subdomain enumeration, port scanning, "
                "technology fingerprinting. Use for initial reconnaissance."
            ),
            "system_prompt": """\
You are a rapid attack surface mapping specialist.
Your job: discover as much of the target's external footprint as possible, quickly.

Tasks:
- Enumerate subdomains via DNS brute-force, certificate transparency, Shodan
- Scan open ports (top 1000 TCP) on all discovered hosts
- Fingerprint technologies (web stack, CDN, WAF, frameworks)
- List all HTTP services with status codes and titles

Be fast and broad. Flag anything interesting for deeper investigation.
""",
            "model": fast_model,    # cheap/fast model — high call volume
            "api_key": fast_key,
        },

        # ── CVE researcher — good reasoning model for threat intel ──
        {
            "name": "cve-researcher",
            "description": (
                "CVE research, exploit PoC hunting, HackerOne prior art lookup, "
                "threat intelligence. Use when you need vulnerability context."
            ),
            "system_prompt": """\
You are a vulnerability researcher specialising in CVE analysis and threat intelligence.

When given a service, version, or technology:
1. Search for known CVEs (NVD, MITRE, ExploitDB)
2. Find public PoC exploits or Metasploit modules
3. Check HackerOne for prior bug bounty reports on similar targets
4. Assess exploitability in the given context (CVSS, network access, auth required)
5. Summarise: vulnerable? exploitable? PoC available?

Always cite sources. Flag anything with CVSS ≥ 7.0 as high priority.
""",
            "model": fast_model,
            "api_key": fast_key,
        },

        # ── Exploit coder — best code model for writing exploits ──
        {
            "name": "exploit-coder",
            "description": (
                "Write exploit scripts, PoC builders, Nuclei templates, custom scanners, "
                "and automation tools. Use when code needs to be written."
            ),
            "system_prompt": """\
You are an expert exploit developer and security tool builder.

You write:
- Python/Go exploit scripts for specific CVEs or vulnerability classes
- Nuclei templates for discovered vulnerability patterns
- IDOR enumerators, auth bypass scripts, SSRF probers
- Custom scanners targeting specific attack surfaces

Code requirements:
- Clean, well-commented, ready to run
- Include error handling and rate limiting
- Parameterise targets (never hardcode)
- Include a usage example in the docstring

Test your logic before presenting. Prefer requests/httpx over curl.
""",
            "model": code_model,    # best code model
            "api_key": code_key,
            "tools": get_web_tools(),   # give coder web security tools
        },

        # ── Report writer — inherits main agent model (no model key) ──
        {
            "name": "report-writer",
            "description": (
                "Write structured pentest reports, executive summaries, finding write-ups. "
                "Use when findings need to be documented professionally."
            ),
            "system_prompt": """\
You are a professional penetration testing report writer.

For each finding, write:
- Title: clear, searchable vulnerability name
- Severity: Critical / High / Medium / Low / Informational (with CVSS score)
- Description: what the vulnerability is and why it matters
- Evidence: exact request/response, screenshot description, or log snippet
- Impact: business impact if exploited
- Recommendation: concrete remediation steps

Tone: formal, precise, professional. No marketing language.
Final deliverable: executive summary + full technical report in markdown.
""",
            # No model/api_key → inherits from main agent
        },
    ]


# ---------------------------------------------------------------------------
# Example 1: Fully custom team (replaces built-in subagents entirely)
# ---------------------------------------------------------------------------

async def run_custom_team(
    target: str,
    main_model: str,
    main_key: str,
    fast_model: str,
    fast_key: str,
    code_model: str,
    code_key: str,
) -> None:
    """
    Replace all built-in subagents with a fully custom team.

    without_subagents() skips AGENTS.md loading entirely.
    add_subagents() injects only the specified custom subagents.
    """
    subagents = build_subagents(fast_model, fast_key, code_model, code_key)

    builder = (
        RAIAgentBuilder()
        .agent_name("rai")
        .model(main_model)
        .without_subagents()            # skip AGENTS.md loading
        .add_subagents(subagents)       # inject custom team instead
        .add_tools(get_security_tools())
        .target(target)
        .with_hitl()
    )
    if main_key:
        builder = builder.api_key(main_key)

    async with await builder.build() as agent:
        print("Custom subagent team:")
        for s in subagents:
            model_label = s.get("model", f"{main_model} (inherited)")
            print(f"  {s['name']:<20} → {model_label}")
        print()

        await agent.run(
            f"Run a full security assessment of {target}. "
            "Use surface-mapper for recon, cve-researcher for vulnerability research, "
            "exploit-coder to write targeted exploits, and report-writer for the final report."
        )
        print(f"\nDone. Thread: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Example 2: Mix custom subagents with built-in ones
# ---------------------------------------------------------------------------

async def run_mixed_team(
    target: str,
    main_model: str,
    main_key: str,
    code_model: str,
    code_key: str,
) -> None:
    """
    Keep built-in subagents (recon, researcher, sast-analyzer) and inject
    an additional custom exploit-coder subagent alongside them.

    Custom subagents take precedence over built-in ones on name collision.
    """
    custom_coder = {
        "name": "exploit-coder",        # new name — doesn't clash with built-in "coder"
        "description": "Write targeted exploit scripts and Nuclei templates for confirmed findings.",
        "system_prompt": """\
You are an expert exploit developer. Write clean, runnable Python exploit scripts
and Nuclei templates for vulnerabilities confirmed by the recon and researcher agents.
""",
        "model": code_model,
        "api_key": code_key,
        "tools": get_web_tools(),
    }

    builder = (
        RAIAgentBuilder()
        .agent_name("rai")
        .model(main_model)
        .with_subagents()               # load built-in subagents from AGENTS.md
        .add_subagent(custom_coder)     # add one custom subagent on top
        .target(target)
        .with_hitl()
    )
    if main_key:
        builder = builder.api_key(main_key)

    async with await builder.build() as agent:
        print("Mixed team: built-in (recon, researcher, coder, sast-analyzer) + custom exploit-coder")
        await agent.run(
            f"Assess {target}. Use recon for surface mapping, researcher for CVE intel, "
            "exploit-coder to write any needed exploit scripts, "
            "and sast-analyzer if source code is accessible."
        )
        print(f"\nDone. Thread: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RAI custom subagent registration example")
    parser.add_argument("--target", required=True, help="Target hostname, IP, or URL")
    parser.add_argument("--mode", choices=["custom", "mixed"], default="custom",
                        help="custom=fully custom team, mixed=built-in + custom coder")

    # Main agent
    parser.add_argument("--main-model", default=DEFAULT_MODEL)
    parser.add_argument("--main-key", default="")

    # Fast model (surface-mapper, cve-researcher)
    parser.add_argument("--fast-model", default=DEFAULT_MODEL,
                        help="Cheap/fast model for high-volume subagents (groq, haiku, etc.)")
    parser.add_argument("--fast-key", default="")

    # Code model (exploit-coder)
    parser.add_argument("--code-model", default=DEFAULT_MODEL,
                        help="Best code model for exploit writing (gpt-4o, sonnet, etc.)")
    parser.add_argument("--code-key", default="")

    args = parser.parse_args()

    # Fallback: if fast-key / code-key not set, use main-key
    fast_key  = args.fast_key  or args.main_key
    code_key  = args.code_key  or args.main_key
    fast_model = args.fast_model or args.main_model
    code_model = args.code_model or args.main_model

    if args.mode == "custom":
        asyncio.run(run_custom_team(
            args.target, args.main_model, args.main_key,
            fast_model, fast_key, code_model, code_key,
        ))
    else:
        asyncio.run(run_mixed_team(
            args.target, args.main_model, args.main_key,
            code_model, code_key,
        ))


if __name__ == "__main__":
    main()
