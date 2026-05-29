"""rai http — serve and interact with RAI HTTP streaming server.

Subcommands
-----------
    rai http serve      Serve RAI agents via the HTTP streaming API
    rai http run        Post a task and stream output to the terminal
    rai http agents     List registered agents
    rai http threads    List conversation threads
    rai http tasks      List background task pool
    rai http subagents  List spawned subagents
    rai http status     Show run status
    rai http logs       Tail SSE stream of an existing run
    rai http approve    Respond to a pending HITL interrupt
    rai http cancel     Cancel a running run

All client commands accept --server/-s (default: $RAI_HTTP_SERVER or http://127.0.0.1:8000)
and --api-key/-k (default: $RAI_SERVER_KEY).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Annotated, Optional

import typer

# ── App ───────────────────────────────────────────────────────────────────────

http_app = typer.Typer(
    name="http",
    help="RAI HTTP server and client harness.",
    invoke_without_command=True,
    no_args_is_help=True,
)

# ── Shared client helpers ─────────────────────────────────────────────────────

_SERVER_ENV = "RAI_HTTP_SERVER"
_KEY_ENV = "RAI_SERVER_KEY"
_DEFAULT_SERVER = "http://127.0.0.1:8000"

_TOKEN_COLOR = typer.colors.WHITE
_TOOL_COLOR = typer.colors.CYAN
_SUBAGENT_COLOR = typer.colors.YELLOW
_ERROR_COLOR = typer.colors.RED
_DIM = typer.colors.BRIGHT_BLACK


def _debug_startup_enabled(verbose: bool) -> bool:
    return verbose or os.environ.get("DEV") == "1" or os.environ.get("RAI_DEBUG_LOG_CALLS") == "1"


def launch_tui(
    *,
    base_url: str,
    agent: str,
    api_key: str = "",
    thread_id: str | None = None,
) -> None:
    from rai.tui import RaiHttpTUI

    RaiHttpTUI(
        base_url=base_url,
        agent=agent,
        api_key=api_key,
        thread_id=thread_id,
    ).run()


def _server_url(server: str) -> str:
    return (server or os.environ.get(_SERVER_ENV, _DEFAULT_SERVER)).rstrip("/")


def _headers(api_key: str) -> dict:
    key = api_key or os.environ.get(_KEY_ENV, "")
    return {"X-API-Key": key} if key else {}


def _client(server: str, api_key: str):
    try:
        import httpx
    except ImportError:
        typer.echo("httpx is required for client commands: pip install httpx", err=True)
        raise typer.Exit(1)
    return httpx.Client(base_url=_server_url(server), headers=_headers(api_key), timeout=30.0)


def _async_client(server: str, api_key: str):
    try:
        import httpx
    except ImportError:
        typer.echo("httpx is required for client commands: pip install httpx", err=True)
        raise typer.Exit(1)
    return httpx.AsyncClient(base_url=_server_url(server), headers=_headers(api_key), timeout=None)


# ── serve ─────────────────────────────────────────────────────────────────────

@http_app.command("serve")
def serve_command(
    agents:     Annotated[list[str], typer.Option("--agent", "-a", help="Agent name(s) to serve")] = [],
    port:       Annotated[int,  typer.Option("--port",      "-p")] = 8000,
    host:       Annotated[str,  typer.Option("--host")] = "127.0.0.1",
    model:      Annotated[str,  typer.Option("--model",     "-m")] = "",
    api_key:    Annotated[str,  typer.Option("--api-key",        envvar="RAI_API_KEY")] = "",
    server_key: Annotated[str,  typer.Option("--server-key")] = "",
    base_url:   Annotated[str,  typer.Option("--base-url")] = "",
    hitl:       Annotated[bool, typer.Option("--hitl/--no-hitl")] = False,
    cors:       Annotated[list[str], typer.Option("--cors")] = [],
    log_level:  Annotated[str,  typer.Option("--log-level")] = "info",
    reload:     Annotated[bool, typer.Option("--reload/--no-reload")] = False,
    verbose:    Annotated[bool, typer.Option("--verbose", "-v")] = False,
    tui:        Annotated[bool, typer.Option("--tui/--no-tui", help="Open RAI TUI after server starts")] = False,
) -> None:
    """Serve RAI agents via the HTTP streaming API.

    \b
    Examples
    --------
        rai http serve                               # serve 'rai' agent, port 8000
        rai http serve --agent pentest --port 9000   # serve a named agent
        rai http serve --hitl --server-key secret    # HITL-enabled, secured
        rai http serve --cors http://localhost:3000  # allow a frontend origin
        rai http serve --hitl --tui                  # serve + open TUI

    \b
    API endpoints (once running)
    ----------------------------
        GET  /ok                              health check
        GET  /agents                          list agents
        POST /agents/{name}/runs              create run
        GET  /agents/{name}/runs/{id}/stream  SSE event stream
        GET  /subagents/{id}/stream           per-subagent SSE stream
        POST /subagents/{id}/interrupt        per-subagent HITL
        GET  /threads                         list threads
        GET  /docs                            OpenAPI docs
    """
    http_server(
        agents=agents, port=port, host=host, model=model,
        api_key=api_key, server_key=server_key, base_url=base_url,
        hitl=hitl, cors=cors, log_level=log_level, reload=reload,
        verbose=verbose, tui=tui,
    )


def http_server(
    agents: list[str] = [],
    port: int = 8000,
    host: str = "127.0.0.1",
    model: str = "",
    api_key: str = "",
    server_key: str = "",
    base_url: str = "",
    hitl: bool = False,
    cors: list[str] = [],
    log_level: str = "info",
    reload: bool = False,
    verbose: bool = False,
    tui: bool = False,
    thread_id: str = "",
) -> None:
    """Shared serve implementation (also callable from main.py's legacy `rai http` command)."""
    from rai.cli.main import _bootstrap, _resolve_model
    _bootstrap(verbose=verbose)
    debug_startup = _debug_startup_enabled(verbose)

    from rai.config.agent import load_agent_config
    from rai.engine.model import DEFAULT_MODEL
    from rai.harness import HTTPConfig, RAIHTTPServer
    from rai.sdk import RAIAgent

    resolved_model = _resolve_model(model)
    agent_names = list(agents) or ["rai"]

    if debug_startup:
        typer.echo(
            f"RAI http — agents={agent_names}  model={resolved_model!r}  "
            f"{host}:{port}  hitl={hitl}"
        )

    config = HTTPConfig(
        host=host,
        port=port,
        reload=reload,
        log_level="debug" if verbose else log_level,
        cors_origins=list(cors),
        api_key=server_key,
    )

    server = RAIHTTPServer(config)

    for name in agent_names:
        # Load per-agent config.toml so model/api_key/base_url are respected.
        # Priority: CLI flag > config.toml > DEFAULT_MODEL.
        try:
            cfg = load_agent_config(name)
        except Exception:
            cfg = None

        # Model: CLI --model wins; otherwise use config.toml; fall back to DEFAULT_MODEL.
        effective_model = resolved_model
        if resolved_model == DEFAULT_MODEL and cfg and cfg.model:
            effective_model = cfg.model

        # api_key / base_url: CLI flag wins; otherwise use config.toml value.
        effective_api_key  = api_key  or (cfg.api_key  if cfg else "") or ""
        effective_base_url = base_url or (cfg.base_url if cfg else "") or ""

        if effective_model != resolved_model:
            typer.echo(f"  [{name}] model from config: {effective_model!r}")

        builder = (
            RAIAgent.builder()
            .agent_name(name)
            .model(effective_model)
        )
        if effective_api_key:
            builder = builder.api_key(effective_api_key)
        if effective_base_url:
            builder = builder.base_url(effective_base_url)
        if not hitl:
            builder = builder.without_hitl()
        else:
            builder = builder.with_hitl()

        server.register(builder)

    if tui:
        _serve_with_tui(
            server=server,
            host=host,
            port=port,
            agent_names=agent_names,
            server_key=server_key,
            log_level="debug" if verbose else log_level,
            thread_id=thread_id,
            debug_startup=debug_startup,
        )
    else:
        if debug_startup:
            typer.echo(f"Serving {len(agent_names)} agent(s). Press Ctrl-C to stop.\n")
        server.run()


def _serve_with_tui(
    server,
    host: str,
    port: int,
    agent_names: list[str],
    server_key: str,
    log_level: str,
    thread_id: str = "",
    debug_startup: bool = False,
) -> None:
    """Start the HTTP server in a background thread, then launch the TUI."""
    import os
    import tempfile
    import threading
    import time

    import httpx
    import uvicorn

    # Route all uvicorn log output to a file so it doesn't overlay the TUI.
    log_file = os.path.join(tempfile.gettempdir(), "rai-server.log")
    _tui_log_config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "fmt": "%(asctime)s %(levelname)s %(name)s — %(message)s",
                "datefmt": "%H:%M:%S",
            }
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": log_file,
                "mode": "a",
                "formatter": "default",
            }
        },
        "loggers": {
            "uvicorn":        {"handlers": ["file"], "level": "INFO",    "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO",    "propagate": False},
            "uvicorn.error":  {"handlers": ["file"], "level": "WARNING", "propagate": False},
        },
    }

    fastapi_app = server._build_app()
    uv_config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_config=_tui_log_config,
        access_log=True,
        loop="asyncio",
    )
    uv_server = uvicorn.Server(uv_config)

    def _run_uvicorn() -> None:
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        loop.run_until_complete(uv_server.serve())

    t = threading.Thread(target=_run_uvicorn, daemon=True)
    t.start()

    # Poll until ready (up to 10s)
    base = f"http://{host}:{port}"
    if debug_startup:
        typer.echo(f"Starting server at {base} …", nl=False)
    ready = False
    for _ in range(40):
        try:
            if httpx.get(f"{base}/ok", timeout=0.5, verify=False).status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.25)
        if debug_startup:
            typer.echo(".", nl=False)
    if debug_startup:
        typer.echo()

    if not ready:
        typer.secho("Server did not become ready in time.", fg=typer.colors.RED, err=True)
        return

    if debug_startup:
        typer.echo("Server ready. Opening TUI…")

    launch_tui(
        base_url=base,
        agent=agent_names[0],
        api_key=server_key,
        thread_id=thread_id or None,
    )

    # Signal server to stop after TUI exits
    uv_server.should_exit = True


# ── run (client: POST run + stream SSE to terminal) ───────────────────────────

@http_app.command("run")
def run_command(
    prompt:   Annotated[str, typer.Argument(help="Task prompt to send to the agent")],
    agent:    Annotated[str,  typer.Option("--agent",  "-a", help="Agent name")] = "rai",
    stream:   Annotated[bool, typer.Option("--stream/--no-stream", help="Stream SSE output")] = True,
    thread_id: Annotated[Optional[str], typer.Option("--thread", "-t", help="Reuse a thread ID")] = None,
    model:    Annotated[str,  typer.Option("--model",  "-m", help="Model override")] = "",
    server:   Annotated[str,  typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key:  Annotated[str,  typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """Post a task to an agent and stream its output to the terminal.

    \b
    Examples
    --------
        rai http run "scan example.com"
        rai http run "analyse this JWT" --agent jwt-expert
        rai http run "continue" --thread <thread_id>
        rai http run "check creds" --server http://remote:8000

    \b
    Keyboard shortcuts during streaming
    ------------------------------------
        Ctrl-C  Interrupt (does not cancel the run on the server)
    """
    asyncio.run(_run_and_stream(
        prompt=prompt, agent=agent, stream=stream,
        thread_id=thread_id, model=model,
        server=server, api_key=api_key,
    ))


async def _run_and_stream(
    prompt: str,
    agent: str,
    stream: bool,
    thread_id: Optional[str],
    model: str,
    server: str,
    api_key: str,
) -> None:
    async with _async_client(server, api_key) as client:
        body: dict = {"input": prompt}
        if thread_id:
            body["thread_id"] = thread_id
        if model:
            body["model"] = model

        try:
            resp = await client.post(f"/agents/{agent}/runs", json=body)
            resp.raise_for_status()
        except Exception as exc:
            typer.echo(f"Failed to create run: {exc}", err=True)
            raise typer.Exit(1)

        run = resp.json()
        run_id = run["run_id"]
        thread = run["thread_id"]
        stream_url = run.get("stream_url", f"/agents/{agent}/runs/{run_id}/stream")

        typer.secho(
            f"run_id={run_id}  thread={thread}  agent={agent}",
            fg=_DIM,
        )

        if not stream:
            typer.echo(f"Run started. Stream at: {_server_url(server)}{stream_url}")
            return

        await _stream_sse(
            client=client,
            url=stream_url,
            run_id=run_id,
            thread_id=thread,
            server=server,
            api_key=api_key,
        )


async def _stream_sse(
    client,
    url: str,
    run_id: str,
    thread_id: str,
    server: str,
    api_key: str,
) -> None:
    """Consume an SSE stream and render events to the terminal."""
    current_event_type: str = ""
    current_subagent_id: str = ""

    try:
        async with client.stream("GET", url) as resp:
            async for raw_line in resp.aiter_lines():
                raw_line = raw_line.strip()
                if not raw_line:
                    current_event_type = ""
                    continue

                if raw_line.startswith("event:"):
                    current_event_type = raw_line[6:].strip()
                    continue

                if not raw_line.startswith("data:"):
                    continue

                try:
                    data = json.loads(raw_line[5:].strip())
                except json.JSONDecodeError:
                    continue

                await _handle_sse_event(
                    event_type=current_event_type,
                    data=data,
                    run_id=run_id,
                    thread_id=thread_id,
                    server=server,
                    api_key=api_key,
                )

                if current_event_type in ("run_end", "error"):
                    break

    except KeyboardInterrupt:
        typer.echo("\n[stream interrupted]", err=True)


async def _handle_sse_event(
    event_type: str,
    data: dict,
    run_id: str,
    thread_id: str,
    server: str,
    api_key: str,
) -> None:
    """Render a single SSE event to the terminal."""
    if event_type == "token":
        content = data.get("content", "")
        typer.echo(content, nl=False)

    elif event_type == "subagent_token":
        tid = data.get("task_id", "")[:8]
        content = data.get("content", "")
        typer.secho(f"  [{tid}] ", fg=_SUBAGENT_COLOR, nl=False)
        typer.echo(content, nl=False)

    elif event_type == "subagent_thinking":
        tid = data.get("task_id", "")[:8]
        typer.secho(f"  [{tid}:thinking] {data.get('content','')[:80]}", fg=_DIM)

    elif event_type == "tool_start":
        typer.secho(f"\n[tool: {data.get('tool_name','')}]", fg=_TOOL_COLOR)

    elif event_type == "tool_end":
        pass  # don't print tool output by default

    elif event_type == "subagent_started":
        tid = data.get("task_id", "")[:8]
        typer.secho(
            f"\n[subagent:{tid} started — agent={data.get('agent_name','')}]",
            fg=_SUBAGENT_COLOR,
        )

    elif event_type == "subagent_tool_start":
        tid = data.get("task_id", "")[:8]
        typer.secho(f"  [{tid}:tool: {data.get('tool_name','')}]", fg=_SUBAGENT_COLOR)

    elif event_type == "subagent_completed":
        tid = data.get("task_id", "")[:8]
        status = data.get("status", "")
        typer.secho(
            f"\n[subagent:{tid} {status}]",
            fg=_TOOL_COLOR if status == "completed" else _ERROR_COLOR,
        )

    elif event_type == "subagent_interrupt":
        tid = data.get("task_id", "")[:8]
        typer.echo()
        typer.secho(f"[SUBAGENT HITL — {tid}]", fg=typer.colors.MAGENTA, bold=True)
        _print_action_requests(data.get("action_requests", []))
        decision = _prompt_hitl_decision()
        await _post_subagent_interrupt(
            task_id=data.get("task_id", ""),
            decision=decision,
            server=server,
            api_key=api_key,
        )

    elif event_type == "interrupt":
        typer.echo()
        typer.secho("[HITL INTERRUPT]", fg=typer.colors.MAGENTA, bold=True)
        _print_action_requests(data.get("action_requests", []))
        decision = _prompt_hitl_decision()
        await _post_thread_interrupt(
            thread_id=thread_id,
            decision=decision,
            server=server,
            api_key=api_key,
        )

    elif event_type == "task_created":
        tid = (data.get("task_id") or "")[:12]
        typer.secho(f"\n[task:{tid} created — {data.get('agent_name','')}]", fg=_DIM)

    elif event_type == "task_completed":
        tid = (data.get("task_id") or "")[:12]
        typer.secho(f"[task:{tid} {data.get('status','')}]", fg=_TOOL_COLOR)

    elif event_type == "pipeline_created":
        typer.secho(f"\n[pipeline created — {data.get('pipeline_id','')[:12]}]", fg=_DIM)

    elif event_type == "run_keepalive":
        typer.secho(".", fg=_DIM, nl=False)

    elif event_type == "run_end":
        typer.echo()
        output = data.get("output", "")
        typer.secho("\n--- run complete ---", fg=_DIM)
        if output:
            typer.echo(output)
        meta = {k: v for k, v in data.items() if k in
                ("model", "num_turns", "duration_ms", "stop_reason", "result_subtype")}
        typer.secho(json.dumps(meta), fg=_DIM)

    elif event_type == "error":
        typer.echo()
        typer.secho(f"[error] {data.get('message','')}", fg=_ERROR_COLOR, err=True)

    elif event_type == "rate_limit":
        typer.secho(f"[rate limited — resets_at={data.get('resets_at')}]", fg=_ERROR_COLOR)

    elif event_type == "permission_denied":
        typer.secho(
            f"[permission denied: {data.get('tool_name','')}]",
            fg=_ERROR_COLOR,
        )


# ── HITL helpers ──────────────────────────────────────────────────────────────

def _print_action_requests(action_requests: list[dict]) -> None:
    for req in action_requests:
        name = req.get("name", "")
        args = req.get("args", {})
        typer.echo(f"  Tool: {name}")
        for k, v in args.items():
            typer.echo(f"    {k}: {str(v)[:200]}")


def _prompt_hitl_decision() -> dict:
    choice = typer.prompt(
        "Decision",
        default="approve",
        prompt_suffix=" [approve/reject/edit]: ",
    ).strip().lower()
    if choice.startswith("r"):
        return {"decision": "reject"}
    if choice.startswith("e"):
        edited_json = typer.prompt("Edited action JSON")
        try:
            edited = json.loads(edited_json)
        except json.JSONDecodeError:
            typer.echo("Invalid JSON, approving instead.", err=True)
            return {"decision": "approve"}
        return {"decision": "edit", "edited_action": edited}
    return {"decision": "approve"}


async def _post_thread_interrupt(thread_id: str, decision: dict, server: str, api_key: str) -> None:
    async with _async_client(server, api_key) as c:
        try:
            r = await c.post(f"/threads/{thread_id}/interrupt", json=decision)
            r.raise_for_status()
        except Exception as exc:
            typer.secho(f"Failed to post interrupt decision: {exc}", fg=_ERROR_COLOR, err=True)


async def _post_subagent_interrupt(task_id: str, decision: dict, server: str, api_key: str) -> None:
    async with _async_client(server, api_key) as c:
        try:
            r = await c.post(f"/subagents/{task_id}/interrupt", json=decision)
            r.raise_for_status()
        except Exception as exc:
            typer.secho(f"Failed to post subagent interrupt: {exc}", fg=_ERROR_COLOR, err=True)


# ── agents ────────────────────────────────────────────────────────────────────

@http_app.command("agents")
def agents_command(
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """List all registered agents on the running server."""
    with _client(server, api_key) as c:
        r = c.get("/agents")
        r.raise_for_status()
        for a in r.json():
            typer.echo(
                f"{a['name']:20s}  model={a.get('model','?'):30s}  "
                f"hitl={a.get('hitl_enabled', False)}"
            )


# ── threads ───────────────────────────────────────────────────────────────────

@http_app.command("threads")
def threads_command(
    agent:   Annotated[Optional[str], typer.Option("--agent", "-a")] = None,
    limit:   Annotated[int, typer.Option("--limit", "-n")] = 20,
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """List conversation threads."""
    with _client(server, api_key) as c:
        params: dict = {"limit": limit}
        if agent:
            params["agent"] = agent
        r = c.get("/threads", params=params)
        r.raise_for_status()
        for t in r.json():
            typer.echo(
                f"{t['thread_id']:36s}  agent={t.get('agent_name','?'):16s}  "
                f"updated={t.get('updated_at','')[:19]}"
            )


# ── tasks ─────────────────────────────────────────────────────────────────────

@http_app.command("tasks")
def tasks_command(
    status:  Annotated[Optional[str], typer.Option("--status")] = None,
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """List all active background tasks (from the deepagents task pool)."""
    with _client(server, api_key) as c:
        params = {}
        if status:
            params["status"] = status
        r = c.get("/tasks", params=params)
        r.raise_for_status()
        tasks = r.json()
        if not tasks:
            typer.echo("No tasks.")
            return
        for t in tasks:
            _print_task_row(t)


# ── subagents ─────────────────────────────────────────────────────────────────

@http_app.command("subagents")
def subagents_command(
    status:  Annotated[Optional[str], typer.Option("--status")] = None,
    run_id:  Annotated[Optional[str], typer.Option("--run")] = None,
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """List all HTTP subagents (spawned via http_spawn_agent)."""
    with _client(server, api_key) as c:
        params: dict = {}
        if status:
            params["status"] = status
        if run_id:
            params["parent_run_id"] = run_id
        r = c.get("/subagents", params=params)
        r.raise_for_status()
        items = r.json()
        if not items:
            typer.echo("No subagents.")
            return
        for s in items:
            tid = s.get("task_id", "")[:12]
            stat = s.get("status", "?")
            color = _TOOL_COLOR if stat == "completed" else (
                _ERROR_COLOR if stat in ("failed", "cancelled") else
                _SUBAGENT_COLOR
            )
            typer.secho(
                f"{tid}  {s.get('agent_name','?'):16s}  {stat:12s}  "
                f"{s.get('label',''):16s}  {s.get('created_at','')[:19]}",
                fg=color,
            )


# ── status ────────────────────────────────────────────────────────────────────

@http_app.command("status")
def status_command(
    run_id:  Annotated[str, typer.Argument(help="Run ID")],
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """Show the status and metadata of a run."""
    with _client(server, api_key) as c:
        r = c.get(f"/runs/{run_id}")
        r.raise_for_status()
        run = r.json()
        for k, v in run.items():
            if v is not None:
                typer.echo(f"  {k:20s}: {v}")


# ── logs ──────────────────────────────────────────────────────────────────────

@http_app.command("logs")
def logs_command(
    run_id:      Annotated[str, typer.Argument(help="Run ID to tail")],
    last_event:  Annotated[Optional[int], typer.Option("--from-event", help="Replay from event ID")] = None,
    agent:       Annotated[str, typer.Option("--agent", "-a")] = "rai",
    server:      Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key:     Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """Tail the SSE stream of an existing run (supports --from-event for replay)."""
    asyncio.run(_tail_logs(
        run_id=run_id, last_event=last_event,
        agent=agent, server=server, api_key=api_key,
    ))


async def _tail_logs(
    run_id: str,
    last_event: Optional[int],
    agent: str,
    server: str,
    api_key: str,
) -> None:
    url = f"/agents/{agent}/runs/{run_id}/stream"
    extra_headers = {}
    if last_event is not None:
        extra_headers["Last-Event-ID"] = str(last_event)

    async with _async_client(server, api_key) as c:
        c.headers.update(extra_headers)
        await _stream_sse(
            client=c,
            url=url,
            run_id=run_id,
            thread_id="",
            server=server,
            api_key=api_key,
        )


# ── approve ───────────────────────────────────────────────────────────────────

@http_app.command("approve")
def approve_command(
    thread_id: Annotated[str, typer.Argument(help="Thread ID with a pending interrupt")],
    server:    Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key:   Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """Respond to a pending HITL interrupt for a thread (parent or subagent).

    \b
    Examples
    --------
        rai http approve <thread_id>          # parent-level HITL
        rai http approve <task_id>            # subagent-level HITL (by task_id)
    """
    with _client(server, api_key) as c:
        # Check parent-level first
        r = c.get(f"/threads/{thread_id}/interrupt")
        r.raise_for_status()
        state = r.json()

        if state.get("pending"):
            typer.secho("[HITL interrupt pending]", fg=typer.colors.MAGENTA, bold=True)
            _print_action_requests(state.get("action_requests", []))
            decision = _prompt_hitl_decision()
            pr = c.post(f"/threads/{thread_id}/interrupt", json=decision)
            pr.raise_for_status()
            typer.echo(f"Decision submitted: {decision['decision']}")
        else:
            # Try subagent-level
            rs = c.get(f"/subagents/{thread_id}/interrupt")
            if rs.status_code == 200 and rs.json().get("pending"):
                typer.secho("[Subagent HITL interrupt pending]", fg=typer.colors.MAGENTA, bold=True)
                decision = _prompt_hitl_decision()
                ps = c.post(f"/subagents/{thread_id}/interrupt", json=decision)
                ps.raise_for_status()
                typer.echo(f"Decision submitted: {decision['decision']}")
            else:
                typer.echo("No pending interrupt found.")


# ── cancel ────────────────────────────────────────────────────────────────────

@http_app.command("cancel")
def cancel_command(
    run_id:  Annotated[str, typer.Argument(help="Run ID to cancel")],
    agent:   Annotated[str, typer.Option("--agent", "-a")] = "rai",
    server:  Annotated[str, typer.Option("--server", "-s", envvar=_SERVER_ENV)] = "",
    api_key: Annotated[str, typer.Option("--api-key", "-k", envvar=_KEY_ENV)] = "",
) -> None:
    """Cancel a running run."""
    with _client(server, api_key) as c:
        r = c.post(f"/agents/{agent}/runs/{run_id}/cancel")
        r.raise_for_status()
        result = r.json()
        typer.echo(
            f"run_id={run_id}  cancelled={result.get('cancelled')}  "
            f"status={result.get('status')}"
        )


# ── helper formatters ─────────────────────────────────────────────────────────

def _print_task_row(t: dict) -> None:
    tid = (t.get("task_id") or "")[:12]
    stat = t.get("status", "?")
    color = _TOOL_COLOR if stat in ("success", "completed") else (
        _ERROR_COLOR if stat in ("error", "failed", "cancelled") else
        _SUBAGENT_COLOR
    )
    typer.secho(
        f"{tid}  {t.get('agent_name','?'):16s}  {stat:12s}  "
        f"{t.get('label',''):16s}  {t.get('created_at','')[:19]}",
        fg=color,
    )
