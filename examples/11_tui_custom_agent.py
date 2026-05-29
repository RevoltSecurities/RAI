"""11_tui_custom_agent.py — Custom agent launched with HTTP server + TUI window.

Demonstrates: RAIAgentBuilder (modular rai.sdk.builder), RAIHTTPServer / HTTPConfig
              (rai.sdk.harness), RaiHttpTUI (rai.sdk.tui), background server thread,
              server readiness poll, custom system prompt and security tools.

The script:
  1. Builds a pentest agent with RAIAgentBuilder.
  2. Registers it with RAIHTTPServer on a configurable port.
  3. Starts uvicorn in a background thread.
  4. Polls until the server is ready.
  5. Opens the Textual TUI window connected to the running server.
  6. On TUI exit, signals the server to stop.

Usage:
    python examples/11_tui_custom_agent.py
    python examples/11_tui_custom_agent.py --port 9001 --agent mybot
    python examples/11_tui_custom_agent.py --model anthropic:claude-opus-4-7 --hitl
    python examples/11_tui_custom_agent.py --help
"""

from __future__ import annotations

import argparse
import os
import tempfile
import threading
import time

import httpx
import uvicorn

# Modular SDK imports — each sub-package is independently importable
from rai.sdk.builder import RAIAgentBuilder
from rai.sdk.harness import HTTPConfig, RAIHTTPServer
from rai.sdk.tui import RaiHttpTUI
from rai.sdk.tools import get_security_tools, get_builtin_tools, get_memory_tools
from rai.sdk.engine import DEFAULT_MODEL


SYSTEM_PROMPT = """\
You are a senior penetration tester and security researcher.

When given a target or task:
1. Enumerate attack surface (ports, services, technologies).
2. Identify vulnerabilities using your tools.
3. Confirm exploitability with careful PoC steps.
4. Document every finding with severity (CVSS), description, and remediation.
5. Never go out of scope; request approval before destructive actions.

You have access to network tools (nmap, nuclei, http_request) and a bash shell.
"""


# ---------------------------------------------------------------------------
# Server + TUI launcher (same pattern as `rai http serve --tui`)
# ---------------------------------------------------------------------------


def _start_server_background(server: RAIHTTPServer, host: str, port: int) -> uvicorn.Server:
    """Spin up uvicorn in a daemon thread and return the server handle."""
    log_file = os.path.join(tempfile.gettempdir(), "rai-tui-example.log")
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
    """Return True when the server responds to GET /ok, False on timeout."""
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
    agent_name: str,
    model: str,
    host: str,
    port: int,
    api_key: str,
    base_url: str,
    hitl: bool,
    thread_id: str,
) -> None:
    # 1. Build the agent
    builder = (
        RAIAgentBuilder()
        .agent_name(agent_name)
        .model(model)
        .system_prompt(SYSTEM_PROMPT)
        .add_tools(get_security_tools() + get_builtin_tools() + get_memory_tools(agent_name))
    )
    if hitl:
        builder = builder.with_hitl()
    else:
        builder = builder.without_hitl()
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    # 2. Register with HTTP server
    config = HTTPConfig(host=host, port=port)
    server = RAIHTTPServer(config)
    server.register(builder)

    # 3. Start server in background
    uv = _start_server_background(server, host, port)
    base = f"http://{host}:{port}"
    print(f"Starting RAI server at {base} …", end="", flush=True)

    if not _wait_until_ready(base):
        print("\nServer did not start in time.")
        return
    print(" ready.")

    # 4. Open TUI window — blocks until user closes it.
    #    Pass `banner=` to replace the default RAI ASCII art with custom Rich markup.
    #    Use `banner=""` to suppress the banner entirely.
    tui = RaiHttpTUI(
        base_url=base,
        agent=agent_name,
        api_key="",
        thread_id=thread_id or None,
        banner=(
            "[bold cyan]╔══════════════════════════════╗[/bold cyan]\n"
            "[bold cyan]║  🛡  Custom Pentest Agent  🛡  ║[/bold cyan]\n"
            "[bold cyan]╚══════════════════════════════╝[/bold cyan]\n"
            "[dim]Scope: authorized targets only · HITL required for exploits[/dim]"
        ),
    )
    tui.run()

    # 5. Shut down server
    uv.should_exit = True


def main() -> None:
    p = argparse.ArgumentParser(description="RAI Custom Agent — TUI mode")
    p.add_argument("--agent",    default="pentest",        help="Agent name")
    p.add_argument("--model",    default=DEFAULT_MODEL,    help="LLM model string")
    p.add_argument("--host",     default="127.0.0.1",      help="Bind host")
    p.add_argument("--port",     default=8001, type=int,   help="Bind port")
    p.add_argument("--api-key",  default="",               help="Model API key override")
    p.add_argument("--base-url", default="",               help="Model base URL override")
    p.add_argument("--hitl",     action="store_true",      help="Enable human-in-the-loop")
    p.add_argument("--thread",   default="",               help="Resume existing thread ID")
    args = p.parse_args()
    run(
        agent_name=args.agent,
        model=args.model,
        host=args.host,
        port=args.port,
        api_key=args.api_key,
        base_url=args.base_url,
        hitl=args.hitl,
        thread_id=args.thread,
    )


if __name__ == "__main__":
    main()
