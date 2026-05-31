"""12_tui_multi_agent.py — Multiple specialized agents under one HTTP server with TUI.

Demonstrates: registering multiple RAIAgentBuilder instances on a single RAIHTTPServer,
              per-agent system prompts and tool sets, switching between agents in the TUI,
              rai.sdk.* modular imports.

Agents registered:
  pentest  — network / web penetration tester  (security + builtin tools)
  sast     — static code analysis engineer      (filesystem backend, no shell)
  recon    — OSINT / reconnaissance specialist  (web + security tools, no shell)

The TUI opens on the first agent; use the TUI agent-switcher to hop between them.

Usage:
    python examples/12_tui_multi_agent.py
    python examples/12_tui_multi_agent.py --agents pentest sast
    python examples/12_tui_multi_agent.py --port 9002 --model anthropic:claude-opus-4-7
    python examples/12_tui_multi_agent.py --help
"""

from __future__ import annotations

import argparse
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from rai.sdk.builder import RAIAgentBuilder
from rai.sdk.harness import HTTPConfig, RAIHTTPServer
from rai.sdk.tui import RaiHttpTUI
from rai.sdk.tools import (
    get_security_tools,
    get_builtin_tools,
    get_web_tools,
    get_memory_tools,
    FindingsAddTool,
    FindingsListTool,
    FindingsExportTool,
)
from rai.sdk.engine import DEFAULT_MODEL

try:
    from deepagents.backends.filesystem import FilesystemBackend
except ImportError:
    FilesystemBackend = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Per-agent system prompts
# ---------------------------------------------------------------------------

_PENTEST_PROMPT = """\
You are a senior penetration tester.
Enumerate the target, identify vulnerabilities, confirm exploitability, and document
every finding with CVSS severity and remediation steps.
Request HITL approval before running any exploit against live targets.
"""

_SAST_PROMPT = """\
You are an application security engineer performing static analysis.
Scan the codebase for OWASP Top 10 issues, hardcoded secrets, vulnerable dependencies,
and logic flaws. Report each finding with CWE ID, file:line, severity, and fix guidance.
Export a SARIF report when the analysis is complete.
"""

_RECON_PROMPT = """\
You are an OSINT and reconnaissance specialist.
Collect public information about the target: DNS records, WHOIS, certificate transparency,
linked infrastructure, technology stack, and exposed services.
Document your findings and flag anything that raises a security concern.
"""


# ---------------------------------------------------------------------------
# Builder factories
# ---------------------------------------------------------------------------

def _pentest_builder(model: str, cwd: Path) -> RAIAgentBuilder:
    builder = (
        RAIAgentBuilder()
        .agent_name("pentest")
        .model(model)
        .system_prompt(_PENTEST_PROMPT)
        .cwd(cwd)
        .add_tools(
            get_security_tools()
            + get_builtin_tools()
            + get_memory_tools("pentest")
            + [FindingsAddTool(), FindingsListTool(), FindingsExportTool()]
        )
        .without_hitl()
    )
    return builder


def _sast_builder(model: str, cwd: Path) -> RAIAgentBuilder:
    builder = (
        RAIAgentBuilder()
        .agent_name("sast")
        .model(model)
        .system_prompt(_SAST_PROMPT)
        .cwd(cwd)
        .add_tools(
            get_security_tools()
            + get_memory_tools("sast")
            + [FindingsAddTool(), FindingsListTool(), FindingsExportTool()]
        )
        .without_hitl()
    )
    if FilesystemBackend is not None:
        builder = builder.backend(FilesystemBackend(root_dir=cwd)).without_shell()
    return builder


def _recon_builder(model: str, cwd: Path) -> RAIAgentBuilder:
    builder = (
        RAIAgentBuilder()
        .agent_name("recon")
        .model(model)
        .system_prompt(_RECON_PROMPT)
        .cwd(cwd)
        .add_tools(get_web_tools() + get_security_tools() + get_memory_tools("recon"))
        .without_shell()
        .without_hitl()
    )
    return builder


_BUILDER_FACTORIES = {
    "pentest": _pentest_builder,
    "sast":    _sast_builder,
    "recon":   _recon_builder,
}


# ---------------------------------------------------------------------------
# Server helpers
# ---------------------------------------------------------------------------

def _start_server_background(server: RAIHTTPServer, host: str, port: int) -> uvicorn.Server:
    log_file = os.path.join(tempfile.gettempdir(), "rai-multi-tui.log")
    log_cfg = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"d": {"fmt": "%(asctime)s %(levelname)s — %(message)s"}},
        "handlers": {
            "f": {"class": "logging.FileHandler", "filename": log_file, "mode": "a", "formatter": "d"}
        },
        "loggers": {
            "uvicorn":        {"handlers": ["f"], "level": "INFO",    "propagate": False},
            "uvicorn.access": {"handlers": ["f"], "level": "INFO",    "propagate": False},
            "uvicorn.error":  {"handlers": ["f"], "level": "WARNING", "propagate": False},
        },
    }

    app = server._build_app()
    uv_cfg = uvicorn.Config(app, host=host, port=port, log_config=log_cfg, loop="asyncio")
    uv = uvicorn.Server(uv_cfg)

    def _run() -> None:
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        loop.run_until_complete(uv.serve())

    threading.Thread(target=_run, daemon=True).start()
    return uv


def _wait_until_ready(base: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/ok", timeout=0.5, verify=False).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    agent_names: list[str],
    model: str,
    host: str,
    port: int,
    cwd: str,
    thread_id: str,
) -> None:
    resolved_cwd = Path(cwd).resolve()

    # 1. Build + register each requested agent
    config = HTTPConfig(host=host, port=port)
    server = RAIHTTPServer(config)

    registered: list[str] = []
    for name in agent_names:
        factory = _BUILDER_FACTORIES.get(name)
        if factory is None:
            print(f"Unknown agent '{name}'. Available: {list(_BUILDER_FACTORIES)}")
            continue
        server.register(factory(model, resolved_cwd))
        registered.append(name)
        print(f"  Registered agent: {name}")

    if not registered:
        print("No valid agents — aborting.")
        return

    # 2. Start server
    uv = _start_server_background(server, host, port)
    base = f"http://{host}:{port}"
    print(f"Starting server at {base} …", end="", flush=True)

    if not _wait_until_ready(base):
        print("\nServer did not start in time.")
        return
    print(" ready.")
    print(f"TUI opening on agent '{registered[0]}'. Use /switch to change agents.\n")

    agents_label = " · ".join(f"[cyan]{n}[/cyan]" for n in registered)

    # 3. Open TUI on the first registered agent.
    #    Custom banner — replace RAI logo with your own Rich markup.
    tui = RaiHttpTUI(
        base_url=base,
        agent=registered[0],
        thread_id=thread_id or None,
        banner=(
            "[bold yellow]┌─────────────────────────────────┐[/bold yellow]\n"
            "[bold yellow]│  ⚔  RAI Security Platform  ⚔   │[/bold yellow]\n"
            "[bold yellow]└─────────────────────────────────┘[/bold yellow]\n"
            f"[dim]Active agents:[/dim]  {agents_label}\n"
            "[dim]Type /switch <name> to change the active agent[/dim]"
        ),
    )
    tui.run()

    # 4. Shut down
    uv.should_exit = True


def main() -> None:
    p = argparse.ArgumentParser(description="RAI Multi-Agent TUI")
    p.add_argument(
        "--agents", nargs="+",
        default=["pentest", "sast", "recon"],
        help="Agent names to register (pentest, sast, recon)",
    )
    p.add_argument("--model",   default=DEFAULT_MODEL, help="LLM model string")
    p.add_argument("--host",    default="127.0.0.1",   help="Bind host")
    p.add_argument("--port",    default=8001, type=int, help="Bind port")
    p.add_argument("--cwd",     default=".",           help="Working directory for agents")
    p.add_argument("--thread",  default="",            help="Resume existing thread ID")
    args = p.parse_args()
    run(
        agent_names=args.agents,
        model=args.model,
        host=args.host,
        port=args.port,
        cwd=args.cwd,
        thread_id=args.thread,
    )


if __name__ == "__main__":
    main()
