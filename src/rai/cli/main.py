"""RAI CLI entry point.

Commands:
  rai chat    — Interactive security agent REPL
  rai run     — Non-interactive single-prompt execution
  rai explore — Browse agents, skills, MCP servers, memory
  rai agents  — Manage agents (list, show, reset)
  rai config  — Show or edit configuration
  rai mcp     — Manage MCP servers (add, remove, list, get)
  rai skills  — Manage skills (list, create, info, delete, add)
  rai refs    — Manage offline reference repositories
  rai version — Print version
  rai update  — Check for updates and optionally upgrade
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from rai import __version__
from rai.dev import dev_log as _dev_log
from rai.config.settings import settings

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="rai",
    help="RAI — the open-source AI security operator for the full cybersecurity spectrum: threat modeling, SAST, pentesting, red team, bug bounty, VAPT, and SOC operations.",
    rich_markup_mode="rich",
    invoke_without_command=True,
)

explore_app = typer.Typer(help="Browse and manage agents, skills, MCP servers, and memory")
agents_app = typer.Typer(help="Manage RAI agents")
config_app = typer.Typer(help="View and edit RAI configuration")
skills_app = typer.Typer(help="Manage RAI skills (list, create, info, delete)")
threads_app = typer.Typer(help="Manage conversation threads (list, delete)")
mcp_app = typer.Typer(help="Manage MCP servers for an agent (add, remove, list, get)")

from rai.cli.refs import refs_app  # noqa: E402

app.add_typer(explore_app, name="explore")
app.add_typer(agents_app, name="agents")
app.add_typer(config_app, name="config")
app.add_typer(skills_app, name="skills")
app.add_typer(threads_app, name="threads")
app.add_typer(mcp_app, name="mcp")
app.add_typer(refs_app, name="refs")

console = Console()


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Launch the interactive TUI when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        chat()


logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Shared options type aliases
# ---------------------------------------------------------------------------

ModelOpt      = Annotated[str, typer.Option("--model", "-m", envvar="RAI_MODEL", help="Model string (e.g. 'anthropic:claude-sonnet-4-6')")]
AgentOpt      = Annotated[str, typer.Option("--agent", "-a", help="Agent name (maps to ~/.rai/agents/<name>/)")]
TargetOpt     = Annotated[Optional[str], typer.Option("--target", "-t", help="Target name — loads shared memory from ~/.rai/targets/<name>/ (global across all agents)")]
ApiKeyOpt     = Annotated[Optional[str], typer.Option("--api-key", envvar="RAI_API_KEY", help="API key override (highest priority)")]
BaseUrlOpt    = Annotated[Optional[str], typer.Option("--base-url", envvar="RAI_BASE_URL", help="Custom API base URL override")]
AutoApproveOpt = Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all tool calls (no HITL prompts)")]
RateLimitOpt  = Annotated[Optional[str], typer.Option("--rate-limit", envvar="RAI_RATE_LIMIT_PROFILE", help="Rate-limit profile: aggressive, normal, stealth")]
NoMCPOpt      = Annotated[bool, typer.Option("--no-mcp", help="Disable MCP tool loading")]
NoRTKOpt      = Annotated[bool, typer.Option("--no-rtk", help="Disable RTK command rewriting")]
VerboseOpt    = Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")]


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _bootstrap(verbose: bool = False) -> None:
    """Load .env, set up logging, ensure ~/.rai/ directory tree exists."""
    dev = os.environ.get("DEV") == "1"
    if verbose:
        level = logging.DEBUG
    elif dev:
        level = logging.WARNING   # DEV=1: warnings captured by dev_log → /tmp/rai-errors.json
    else:
        level = logging.ERROR     # silent by default — no startup noise

    logging.basicConfig(level=level, format="%(name)s: %(message)s")
    logging.root.setLevel(level)  # force even if basicConfig was a no-op

    # Silence noisy third-party loggers unless verbose
    _noisy = ("httpx", "httpcore", "LiteLLM", "litellm")
    _tp_level = logging.DEBUG if verbose else logging.ERROR
    for _name in _noisy:
        logging.getLogger(_name).setLevel(_tp_level)

    _dev_log.install()  # DEV=1 → write errors to /tmp/rai-errors.json

    # Load .env from project tree then ~/.rai/.env
    load_dotenv()
    rai_env = settings.user_rai_dir / ".env"
    if rai_env.exists():
        load_dotenv(rai_env)

    settings.user_rai_dir.mkdir(parents=True, exist_ok=True)
    # Seed global user profile on every command so ~/.rai/user/ exists before
    # any agent, memory command, or subagent tries to read it.
    settings.ensure_global_user_profile()


def _resolve_model(model_str: str) -> str:
    """Return model string, falling back to _DEFAULT_MODEL if not set."""
    return model_str or os.environ.get("RAI_MODEL") or _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# MCP config helpers
# ---------------------------------------------------------------------------


def _get_mcp_path(agent: str, scope: str) -> Path:
    if scope == "global":
        return settings.user_mcp_config_path        # ~/.rai/.mcp.json
    settings.ensure_agent_dir(agent)
    return settings.agent_dir(agent) / "mcp.json"  # ~/.rai/agents/<agent>/mcp.json


def _read_mcp_json(path: Path) -> dict:
    if not path.exists():
        return {"mcpServers": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_mcp_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _parse_kv_list(items: list[str] | None, sep: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (items or []):
        k, _, v = item.partition(sep)
        if k:
            result[k.strip()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# Streaming agent runner
# ---------------------------------------------------------------------------


async def _run_agent_streaming(
    agent: object,
    initial_message: str,
    config: dict,
    *,
    interactive: bool = True,
) -> None:
    """Stream agent events to the console."""
    from langchain_core.messages import HumanMessage, AIMessageChunk

    state = {"messages": [HumanMessage(content=initial_message)]}

    current_text = ""
    in_tool_call = False
    tool_name = ""

    try:
        async for event in agent.astream_events(state, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if isinstance(chunk, AIMessageChunk) and chunk.content:
                    for block in (chunk.content if isinstance(chunk.content, list) else [{"type": "text", "text": chunk.content}]):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            current_text += text
                            console.print(text, end="", markup=False)
                        elif isinstance(block, str):
                            current_text += block
                            console.print(block, end="", markup=False)

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = event.get("data", {}).get("input", {})
                if current_text:
                    console.print()
                    current_text = ""
                console.print(f"\n[dim]⚙ {tool_name}[/dim] ", end="")
                if isinstance(tool_input, dict):
                    first_val = next(iter(tool_input.values()), "")
                    if isinstance(first_val, str) and first_val:
                        preview = first_val[:80] + ("..." if len(first_val) > 80 else "")
                        console.print(f"[dim]{preview}[/dim]", end="")
                in_tool_call = True

            elif kind == "on_tool_end":
                if in_tool_call:
                    console.print(f" [dim]✓[/dim]")
                    in_tool_call = False
                    tool_name = ""

            elif kind == "on_chain_end" and not in_tool_call:
                if current_text:
                    console.print()
                    current_text = ""

    except KeyboardInterrupt:
        if current_text:
            console.print()
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        logger.debug("Agent stream error", exc_info=True)
        if current_text:
            console.print()
        console.print(f"[red]Error: {e}[/red]")

    if current_text and not current_text.endswith("\n"):
        console.print()



async def _build_and_run_agent(
    prompt: str,
    *,
    model: str,
    agent_name: str,
    api_key: str = "",
    base_url: str = "",
    target: str = "",
    rate_limit_profile: str = "",
    auto_approve: bool,
    no_mcp: bool,
    disable_rtk: bool = False,
    interactive: bool,
    thread_id: str,
    cwd: Path | None = None,
    preloaded_mcp_tools: list | None = None,
    preloaded_mcp_session: Optional[object] = None,
    preloaded_mcp_server_info: list | None = None,
) -> None:
    """Build agent (with MCP tools) and run a single prompt.

    When preloaded_mcp_tools is provided the caller owns the session lifetime
    and this function skips both loading and cleanup — used by the persistent
    REPL so one set of MCP connections serves the whole session.
    """
    from rai.engine.factory import create_rai_agent
    from rai.mcp.loader import resolve_and_load_mcp_tools
    from rai.sessions.store import build_stream_config, get_checkpointer

    effective_cwd = cwd or Path.cwd()

    # Use caller-supplied MCP tools (persistent session) or load fresh ones.
    if preloaded_mcp_tools is not None:
        mcp_tools = preloaded_mcp_tools
        mcp_session = preloaded_mcp_session
        mcp_server_info = preloaded_mcp_server_info or []
        _owns_session = False
    else:
        mcp_tools = []
        mcp_session = None
        mcp_server_info = []
        _owns_session = True
        if not no_mcp:
            try:
                mcp_tools, mcp_session, mcp_server_info = await resolve_and_load_mcp_tools(
                    cwd=effective_cwd, agent_name=agent_name,
                )
                if mcp_server_info:
                    console.print(f"[dim]MCP: loaded {len(mcp_tools)} tool(s) from {len(mcp_server_info)} server(s)[/dim]")
            except RuntimeError as e:
                console.print(f"[yellow]MCP warning: {e}[/yellow]")

    stream_config = build_stream_config(thread_id, agent_name, str(effective_cwd))

    try:
        async with get_checkpointer() as checkpointer:
            agent, _ = create_rai_agent(
                model=_resolve_model(model),
                agent_name=agent_name,
                api_key=api_key,
                base_url=base_url,
                target=target,
                rate_limit_profile=rate_limit_profile,
                extra_tools=mcp_tools,
                interactive=interactive,
                auto_approve=auto_approve,
                disable_rtk=disable_rtk,
                checkpointer=checkpointer,
                mcp_server_info=mcp_server_info,
                cwd=effective_cwd,
            )

            await _run_agent_streaming(agent, prompt, stream_config, interactive=interactive)
    finally:
        if _owns_session and mcp_session:
            await mcp_session.cleanup()


# ---------------------------------------------------------------------------
# rai chat
# ---------------------------------------------------------------------------


@app.command()
def chat(
    agent: AgentOpt = "rai",
    model: ModelOpt = _DEFAULT_MODEL,
    api_key: ApiKeyOpt = None,
    base_url: BaseUrlOpt = None,
    remote_url: Annotated[Optional[str], typer.Option("--remote-url", help="Connect to an existing remote RAI HTTP server; skips local server startup")] = None,
    port: Annotated[int, typer.Option("--port", "-p", help="HTTP server port")] = 8000,
    host: Annotated[str, typer.Option("--host", help="HTTP server host")] = "127.0.0.1",
    hitl: Annotated[bool, typer.Option("--hitl", help="Enable human-in-the-loop tool approval")] = True,
    server_key: Annotated[str, typer.Option("--server-key", envvar="RAI_SERVER_KEY", help="HTTP server API key for local or remote chat")] = "",
    verbose: VerboseOpt = False,
    resume: Annotated[Optional[str], typer.Option("--resume", "-r", help="Resume a session by thread ID")] = None,
    cont: Annotated[bool, typer.Option("--continue", "-c", help="Continue the most recent session for this agent")] = False,
) -> None:
    """Start an interactive chat — local by default, or remote with --remote-url.

    Use ``--remote-url`` to connect the TUI to an existing RAI HTTP server
    instead of starting a local one. Pair it with ``--server-key`` when the
    remote server requires X-API-Key authentication.
    """
    _bootstrap(verbose)

    from rai.sessions.store import generate_thread_id, get_most_recent_sync

    async def _get_remote_most_recent_thread() -> str:
        if not remote_url:
            return ""
        from rai.client import RAIClient

        async with RAIClient(base_url=remote_url, api_key=server_key) as client:
            threads = await client.threads.list(agent=agent, limit=1)
            return threads[0].thread_id if threads else ""

    # Resolve thread ID: explicit > --continue most-recent > fresh/new
    thread_id: Optional[str] = None
    if resume:
        thread_id = resume
        console.print(f"[dim]Resuming thread {thread_id[:8]}...[/dim]")
    elif cont:
        if remote_url:
            thread_id = asyncio.run(_get_remote_most_recent_thread()) or None
        else:
            existing = get_most_recent_sync(agent)
            thread_id = existing if existing else generate_thread_id()
    else:
        if not remote_url:
            thread_id = generate_thread_id()

    if remote_url:
        from rai.cli.http_server import launch_tui

        launch_tui(
            base_url=remote_url.rstrip("/"),
            agent=agent,
            api_key=server_key,
            thread_id=thread_id,
        )
        return

    from rai.cli.http_server import http_server
    http_server(
        agents=[agent],
        port=port,
        host=host,
        model=model,
        api_key=api_key or "",
        server_key=server_key,
        base_url=base_url or "",
        hitl=hitl,
        verbose=verbose,
        tui=True,
        thread_id=thread_id or "",
    )


# ---------------------------------------------------------------------------
# rai run
# ---------------------------------------------------------------------------


@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="Task to execute")],
    model: ModelOpt = _DEFAULT_MODEL,
    agent: AgentOpt = "rai",
    target: TargetOpt = None,
    api_key: ApiKeyOpt = None,
    base_url: BaseUrlOpt = None,
    auto_approve: AutoApproveOpt = True,
    rate_limit: RateLimitOpt = None,
    no_mcp: NoMCPOpt = False,
    no_rtk: NoRTKOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Run a single task non-interactively (headless mode)."""
    _bootstrap(verbose)
    from rai.sessions.store import generate_thread_id
    thread_id = generate_thread_id()

    asyncio.run(_build_and_run_agent(
        prompt,
        model=_resolve_model(model),
        agent_name=agent,
        api_key=api_key or "",
        base_url=base_url or "",
        target=target or "",
        rate_limit_profile=rate_limit or "",
        auto_approve=auto_approve,
        no_mcp=no_mcp,
        disable_rtk=no_rtk,
        interactive=False,
        thread_id=thread_id,
        cwd=Path.cwd(),
    ))


# ---------------------------------------------------------------------------
# rai explore
# ---------------------------------------------------------------------------


@explore_app.command("agents")
def explore_agents() -> None:
    """List all RAI agents."""
    from rai.cli.explorer import list_agents
    list_agents()


@explore_app.command("skills")
def explore_skills(
    agent: AgentOpt = "rai",
) -> None:
    """List available skills."""
    from rai.cli.explorer import list_skills
    list_skills(agent_name=agent)


@explore_app.command("mcp")
def explore_mcp(
    agent: AgentOpt = "rai",
) -> None:
    """List configured MCP servers for an agent."""
    from rai.cli.explorer import list_mcp_servers
    list_mcp_servers(agent_name=agent)


@explore_app.command("memory")
def explore_memory(
    agent: AgentOpt = "rai",
) -> None:
    """Show memory files for an agent."""
    from rai.cli.explorer import show_memory
    show_memory(agent)


@explore_app.command("mcp-add")
def explore_mcp_add(
    name: Annotated[str, typer.Argument(help="Server name")],
    command: Annotated[str, typer.Argument(help="Command to run (stdio) or URL (sse/http)")],
    args: Annotated[Optional[list[str]], typer.Option("--arg", help="Command arguments")] = None,
    transport: Annotated[str, typer.Option("--transport", help="Transport: stdio, sse, http")] = "stdio",
) -> None:
    """Add an MCP server to ~/.rai/.mcp.json."""
    from rai.cli.explorer import add_mcp_server
    if transport in {"sse", "http"}:
        add_mcp_server(name, command="", url=command, transport=transport)
    else:
        add_mcp_server(name, command=command, args=args or [])


@explore_app.command("mcp-remove")
def explore_mcp_remove(
    name: Annotated[str, typer.Argument(help="Server name to remove")],
) -> None:
    """Remove an MCP server from ~/.rai/.mcp.json."""
    from rai.cli.explorer import remove_mcp_server
    remove_mcp_server(name)


@explore_app.command("skills-create")
def explore_skills_create(
    name: Annotated[str, typer.Argument(help="Skill name")],
    agent: AgentOpt = "",
) -> None:
    """Create a new skill .md file scaffold."""
    from rai.cli.explorer import create_skill
    create_skill(name, agent_name=agent or None)


# ---------------------------------------------------------------------------
# rai agents
# ---------------------------------------------------------------------------


@agents_app.command("list")
def agents_list() -> None:
    """List all agents."""
    from rai.cli.explorer import list_agents
    list_agents()


@agents_app.command("show")
def agents_show(name: Annotated[str, typer.Argument(help="Agent name")] = "agent") -> None:
    """Show agent details."""
    from rai.cli.explorer import show_agent
    show_agent(name)


@agents_app.command("reset")
def agents_reset(
    name: Annotated[str, typer.Argument(help="Agent name")] = "agent",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without making changes")] = False,
) -> None:
    """Reset agent memory files."""
    from rai.cli.explorer import reset_agent
    reset_agent(name, dry_run=dry_run)


@agents_app.command("memory-clear")
def agents_memory_clear(
    name: Annotated[str, typer.Argument(help="Agent name")] = "agent",
    memory_type: Annotated[str, typer.Option("--type", help="short_term, long_term, episodic, or all")] = "all",
) -> None:
    """Clear agent memory files."""
    from rai.cli.explorer import clear_memory
    clear_memory(name, memory_type)


@agents_app.command("config")
def agents_config_show(
    name: Annotated[str, typer.Argument(help="Agent name")] = "rai",
) -> None:
    """Show per-agent config (~/.rai/agents/<name>/config.toml)."""
    from rai.config.agent import _config_path, load_agent_config
    cfg = load_agent_config(name)
    path = _config_path(name)
    console.print(Panel(
        f"[dim]Config file:[/dim] {path}\n"
        f"[dim]model:[/dim]      {cfg.model or '(not set — using CLI / RAI_MODEL env)'}\n"
        f"[dim]api_key:[/dim]    {'***' if cfg.api_key else '(not set)'}\n"
        f"[dim]base_url:[/dim]   {cfg.base_url or '(not set)'}\n"
        f"[dim]temperature:[/dim] {cfg.temperature}\n"
        f"[dim]max_tokens:[/dim]  {cfg.max_tokens}",
        title=f"Agent config: {name}",
        expand=False,
    ))


@agents_app.command("config-set")
def agents_config_set(
    name: Annotated[str, typer.Argument(help="Agent name")] = "rai",
    model: Annotated[Optional[str], typer.Option("--model", "-m", help="Model string (LiteLLM or LangChain format)")] = None,
    api_key: Annotated[Optional[str], typer.Option("--api-key", help="API key override for this agent")] = None,
    base_url: Annotated[Optional[str], typer.Option("--base-url", help="Custom API base URL (OpenAI-compatible endpoint)")] = None,
    temperature: Annotated[Optional[float], typer.Option("--temperature", help="Sampling temperature")] = None,
    max_tokens: Annotated[Optional[int], typer.Option("--max-tokens", help="Max output tokens")] = None,
) -> None:
    """Set per-agent config values."""
    from rai.config.agent import load_agent_config, save_agent_config
    cfg = load_agent_config(name)
    if model is not None:
        cfg.model = model
    if api_key is not None:
        cfg.api_key = api_key
    if base_url is not None:
        cfg.base_url = base_url
    if temperature is not None:
        cfg.temperature = temperature
    if max_tokens is not None:
        cfg.max_tokens = max_tokens
    path = save_agent_config(name, cfg)
    console.print(f"[green]✓[/green] Saved config for agent '{name}' → {path}")


@agents_app.command("config-init")
def agents_config_init(
    name: Annotated[str, typer.Argument(help="Agent name")] = "rai",
) -> None:
    """Scaffold a config.toml template for an agent (skips if one exists)."""
    from rai.config.agent import scaffold_agent_config
    path = scaffold_agent_config(name)
    console.print(f"[green]✓[/green] Config template at: {path}")
    console.print(f"[dim]Edit the file to set model, api_key, base_url, etc.[/dim]")


# ---------------------------------------------------------------------------
# rai config
# ---------------------------------------------------------------------------


@config_app.command("show")
def config_show() -> None:
    """Show current RAI configuration."""
    from rai.sessions.store import get_db_path
    console.print(Panel(
        f"[bold]RAI v{__version__} Configuration[/bold]\n\n"
        f"[dim]Config dir:[/dim] {settings.user_rai_dir}\n"
        f"[dim]Agents dir:[/dim] {settings.agents_dir}\n"
        f"[dim]Skills dir:[/dim] {settings.user_skills_dir}\n"
        f"[dim]Sessions DB:[/dim] {get_db_path()}\n"
        f"[dim]MCP config:[/dim] {settings.user_mcp_config_path}\n"
        f"[dim]Audit log:[/dim] {settings.audit_log_path}\n"
        f"[dim]Model:[/dim] {settings.model_name or '(not set — use --model or RAI_MODEL env var)'}\n"
        f"[dim]Shell allow-list:[/dim] {settings.shell_allow_list!r}",
        title="RAI Config",
        expand=False,
    ))


@config_app.command("init")
def config_init(
    agent: AgentOpt = "rai",
) -> None:
    """Initialize RAI directories for an agent."""
    settings.user_rai_dir.mkdir(parents=True, exist_ok=True)
    settings.ensure_global_user_profile()
    settings.ensure_agent_dir(agent)
    settings.ensure_memory_files(agent)
    settings.ensure_skills_dirs(agent)
    console.print(f"[green]✓[/green] Initialized RAI for agent '{agent}'")
    console.print(f"  Memory: {settings.memories_dir(agent)}")
    console.print(f"  Skills: {settings.user_skills_dir}")
    console.print(f"  MCP:    {settings.user_mcp_config_path}")


# ---------------------------------------------------------------------------
# rai skills
# ---------------------------------------------------------------------------


@skills_app.command("list")
def skills_list(
    agent: AgentOpt = "rai",
    project: Annotated[bool, typer.Option("--project", help="Show only project-level skills")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """List all available skills."""
    _bootstrap(verbose)
    from rai.skills.commands import cmd_list
    cwd = Path.cwd() if project else None
    cmd_list(agent, cwd=cwd)


@skills_app.command("create")
def skills_create(
    name: Annotated[str, typer.Argument(help="Skill name (lowercase-alphanumeric-with-hyphens)")],
    agent: AgentOpt = "rai",
    project: Annotated[bool, typer.Option("--project", help="Create in project .rai/skills/")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Create a new skill with a SKILL.md template."""
    _bootstrap(verbose)
    from rai.skills.commands import cmd_create
    cmd_create(name, agent, project=project, cwd=Path.cwd() if project else None)


@skills_app.command("info")
def skills_info(
    name: Annotated[str, typer.Argument(help="Skill name")],
    agent: AgentOpt = "rai",
    verbose: VerboseOpt = False,
) -> None:
    """Show detailed information about a skill."""
    _bootstrap(verbose)
    from rai.skills.commands import cmd_info
    cmd_info(name, agent, cwd=Path.cwd())


@skills_app.command("delete")
def skills_delete(
    name: Annotated[str, typer.Argument(help="Skill name, git repo URL, or local directory path")],
    agent: AgentOpt = "rai",
    skill: Annotated[Optional[str], typer.Option("--skill", help="Delete only this skill (for URL/path sources)")] = None,
    project: Annotated[bool, typer.Option("--project", help="Search only project skills")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without deleting")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation for bulk deletes")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Delete skill(s) by name, or bulk-delete by git repo URL / local path."""
    _bootstrap(verbose)
    from rai.skills.commands import _is_git_url, cmd_delete, cmd_delete_bulk

    is_bulk = _is_git_url(name) or Path(name).expanduser().is_dir()
    if is_bulk:
        cmd_delete_bulk(
            name,
            agent,
            skill_filter=skill or "",
            force=force,
            dry_run=dry_run,
            project=project,
            cwd=Path.cwd() if project else None,
            yes=yes,
        )
    else:
        cmd_delete(name, agent, force=force, dry_run=dry_run, cwd=Path.cwd())


@skills_app.command("add")
def skills_add(
    source: Annotated[str, typer.Argument(help="Git repo URL or local directory path containing SKILL.md")],
    agent: AgentOpt = "rai",
    skill: Annotated[Optional[str], typer.Option("--skill", help="Install only this skill (for multi-skill repos)")] = None,
    name: Annotated[Optional[str], typer.Option("--name", help="Override installed skill name (single-skill only)")] = None,
    branch: Annotated[Optional[str], typer.Option("--branch", "-b", help="Git branch or tag to clone")] = None,
    project: Annotated[bool, typer.Option("--project", help="Install into project .rai/skills/ instead of user skills dir")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation for multi-skill installs")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Install skill(s) from a git repo URL or local directory path."""
    _bootstrap(verbose)
    from rai.skills.commands import cmd_add_git
    cmd_add_git(
        source,
        agent,
        skill_filter=skill or "",
        name_override=name or "",
        branch=branch or "",
        project=project,
        cwd=Path.cwd() if project else None,
        yes=yes,
    )


# ---------------------------------------------------------------------------
# rai threads
# ---------------------------------------------------------------------------


@threads_app.command("list")
def threads_list(
    agent: Annotated[Optional[str], typer.Option("--agent", "-a", help="Filter by agent name")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Maximum threads to show")] = 20,
    sort: Annotated[str, typer.Option("--sort", help="Sort by 'updated' or 'created'")] = "updated",
    verbose: VerboseOpt = False,
) -> None:
    """List recent conversation threads."""
    _bootstrap(verbose)
    from rai.sessions.store import cmd_threads_list
    cmd_threads_list(agent_name=agent, limit=limit, sort_by=sort, verbose=verbose)


@threads_app.command("delete")
def threads_delete(
    thread_id: Annotated[str, typer.Argument(help="Thread ID to delete")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without deleting")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Delete a conversation thread and all its checkpoints."""
    _bootstrap(verbose)
    from rai.sessions.store import cmd_threads_delete
    cmd_threads_delete(thread_id, dry_run=dry_run, force=force)


# ---------------------------------------------------------------------------
# rai mcp
# ---------------------------------------------------------------------------

_MCP_AGENT_HELP = "Agent name (default: rai — writes to ~/.rai/agents/<name>/mcp.json)"
_MCP_SCOPE_HELP = "Target config: 'agent' (default) or 'global' (~/.rai/.mcp.json)"


@mcp_app.command("add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="MCP server name")],
    command_or_url: Annotated[str, typer.Argument(help="Command to run (stdio) or URL (sse/http)")],
    transport: Annotated[str, typer.Option("--transport", "-t", help="Transport: stdio, sse, http")] = "stdio",
    args: Annotated[Optional[list[str]], typer.Option("--arg", help="Command argument (repeatable, stdio only)")] = None,
    env_vars: Annotated[Optional[list[str]], typer.Option("--env", "-e", help="Env var KEY=VAL (repeatable, stdio only)")] = None,
    headers: Annotated[Optional[list[str]], typer.Option("--header", help="Header KEY:VAL (repeatable, sse/http only)")] = None,
    agent: Annotated[str, typer.Option("--agent", "-a", help=_MCP_AGENT_HELP)] = "rai",
    scope: Annotated[str, typer.Option("--scope", help=_MCP_SCOPE_HELP)] = "agent",
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite if server name already exists")] = False,
) -> None:
    """Add an MCP server to an agent's mcp.json (or global config)."""
    path = _get_mcp_path(agent, scope)
    data = _read_mcp_json(path)
    servers: dict = data.setdefault("mcpServers", {})

    if name in servers and not force:
        console.print(f"[red]Error:[/red] server '{name}' already exists in {path}")
        console.print(f"[dim]Use --force to overwrite.[/dim]")
        raise typer.Exit(1)

    if transport in {"sse", "http"}:
        entry: dict = {"url": command_or_url, "transport": transport}
        parsed_headers = _parse_kv_list(headers, ":")
        if not parsed_headers:
            parsed_headers = _parse_kv_list(headers, "=")
        if parsed_headers:
            entry["headers"] = parsed_headers
    else:
        entry = {"command": command_or_url, "transport": "stdio"}
        if args:
            entry["args"] = list(args)
        parsed_env = _parse_kv_list(env_vars, "=")
        if parsed_env:
            entry["env"] = parsed_env

    servers[name] = entry
    _write_mcp_json(path, data)
    console.print(f"[green]✓[/green] Added MCP server [bold]{name}[/bold] ({transport}) → {path}")


@mcp_app.command("remove")
def mcp_remove(
    name: Annotated[str, typer.Argument(help="MCP server name to remove")],
    agent: Annotated[str, typer.Option("--agent", "-a", help=_MCP_AGENT_HELP)] = "rai",
    scope: Annotated[str, typer.Option("--scope", help=_MCP_SCOPE_HELP)] = "agent",
    force: Annotated[bool, typer.Option("--force", "-f", help="Suppress error if not found")] = False,
) -> None:
    """Remove an MCP server from an agent's mcp.json (or global config)."""
    path = _get_mcp_path(agent, scope)
    data = _read_mcp_json(path)
    servers: dict = data.get("mcpServers", {})

    if name not in servers:
        if force:
            return
        console.print(f"[red]Error:[/red] server '{name}' not found in {path}")
        raise typer.Exit(1)

    del servers[name]
    _write_mcp_json(path, data)
    console.print(f"[green]✓[/green] Removed MCP server [bold]{name}[/bold] from {path}")


@mcp_app.command("list")
def mcp_list(
    agent: Annotated[str, typer.Option("--agent", "-a", help=_MCP_AGENT_HELP)] = "rai",
    scope: Annotated[str, typer.Option("--scope", help=_MCP_SCOPE_HELP)] = "agent",
) -> None:
    """List MCP servers configured for an agent (or global)."""
    from rich.table import Table

    path = _get_mcp_path(agent, scope)
    data = _read_mcp_json(path)
    servers: dict = data.get("mcpServers", {})

    if not servers:
        console.print(f"[dim]No MCP servers configured in {path}[/dim]")
        return

    table = Table(title=str(path), show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Transport")
    table.add_column("Command / URL")

    for sname, scfg in servers.items():
        transport = scfg.get("transport") or scfg.get("type") or "stdio"
        cmd_or_url = scfg.get("url") or scfg.get("command") or ""
        if scfg.get("args"):
            cmd_or_url += " " + " ".join(str(a) for a in scfg["args"])
        table.add_row(sname, transport, cmd_or_url)

    console.print(table)


@mcp_app.command("get")
def mcp_get(
    name: Annotated[str, typer.Argument(help="MCP server name")],
    agent: Annotated[str, typer.Option("--agent", "-a", help=_MCP_AGENT_HELP)] = "rai",
    scope: Annotated[str, typer.Option("--scope", help=_MCP_SCOPE_HELP)] = "agent",
) -> None:
    """Show full configuration for a single MCP server."""
    path = _get_mcp_path(agent, scope)
    data = _read_mcp_json(path)
    servers: dict = data.get("mcpServers", {})

    if name not in servers:
        console.print(f"[red]Error:[/red] server '{name}' not found in {path}")
        raise typer.Exit(1)

    console.print(Panel(
        json.dumps(servers[name], indent=2),
        title=f"MCP server: {name}",
        subtitle=str(path),
        expand=False,
    ))


# ---------------------------------------------------------------------------
# rai version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print RAI version."""
    console.print(f"rai {__version__}")
    console.print("[dim]The open-source AI security operator — threat modeling, SAST, pentesting, red team, bug bounty, VAPT, and SOC operations.[/dim]")


# ---------------------------------------------------------------------------
# rai update
# ---------------------------------------------------------------------------


@app.command()
def update(
    yes:   Annotated[bool, typer.Option("--yes", "-y", help="Auto-confirm upgrade without prompting")] = False,
    check: Annotated[bool, typer.Option("--check",    help="Only check for an update; do not upgrade")] = False,
    force: Annotated[bool, typer.Option("--force",    help="Bypass PyPI cache and re-fetch latest version")] = False,
) -> None:
    """Check for a newer version of RAI and optionally upgrade."""
    from rai.update import is_update_available, upgrade_command, perform_upgrade

    console.print(f"[dim]Current:[/dim] rai {__version__}")
    console.print("[dim]Checking PyPI…[/dim]")

    available, latest = is_update_available(bypass_cache=force)

    if not available:
        console.print("[green]✓ Already up to date.[/green]")
        return

    cmd = upgrade_command()
    console.print(f"[yellow]Update available:[/yellow] v{latest}")
    console.print(f"[dim]Command:[/dim] {cmd}")

    if check:
        return

    if not yes:
        confirm = typer.confirm(f"Upgrade to v{latest}?", default=True)
        if not confirm:
            console.print("[dim]Skipped.[/dim]")
            return

    console.print(f"[dim]Running:[/dim] {cmd}")
    ok, output = asyncio.run(perform_upgrade())
    if ok:
        console.print(f"[green]✓ Upgraded to v{latest}[/green]")
    else:
        console.print(f"[red]Upgrade failed:[/red]\n{output}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# rai serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    agent:      Annotated[str,  typer.Option("--agent",    "-a", help="Agent name to serve")] = "rai",
    model:      Annotated[str,  typer.Option("--model",    "-m", help="Model override")] = "",
    port:       Annotated[int,  typer.Option("--port",     "-p", help="Port to bind")] = 2024,
    host:       Annotated[str,  typer.Option("--host",          help="Host to bind")] = "127.0.0.1",
    dev:        Annotated[bool, typer.Option("--dev/--no-dev",  help="Hot reload on (--dev, default) or off (--no-dev)")] = True,
    browser:    Annotated[bool, typer.Option("--browser/--no-browser", help="Open LangSmith Studio in browser")] = True,
    workers:    Annotated[int,  typer.Option("--workers",       help="Max concurrent jobs per worker")] = 10,
    api_key:    Annotated[str,  typer.Option("--api-key",       help="API key override", envvar="ANTHROPIC_API_KEY")] = "",
    base_url:   Annotated[str,  typer.Option("--base-url",      help="Base URL / proxy override")] = "",
    target:     Annotated[str,  typer.Option("--target",        help="Per-target memory scope (IP, hostname, project)")] = "",
    rate_limit: Annotated[str,  typer.Option("--rate-limit",    help="Rate limit profile: aggressive | normal | stealth")] = "",
    log_level:  Annotated[str,  typer.Option("--log-level",     help="Server log level")] = "warning",
    env_file:   Annotated[Optional[str], typer.Option("--env-file", help=".env file for server process")] = None,
) -> None:
    """Serve a RAI agent via the LangGraph API (no langgraph.json required).

    \b
    Loads identically to the TUI: main-agent + all subagent MCP tools
    loaded in parallel, target memory scoping and rate limit respected.

    \b
    Examples
    --------
        rai serve                               # dev + Studio, port 2024
        rai serve --no-dev --port 8080          # plain server, no hot reload
        rai serve --agent pentest               # serve a specific agent
        rai serve --host 0.0.0.0 --workers 4    # bind all interfaces
        rai serve --target 10.0.0.1 --rate-limit aggressive

    \b
    API endpoints (once running)
    ----------------------------
        GET  http://HOST:PORT/ok              health check
        POST http://HOST:PORT/runs/stream     stream a run
        POST http://HOST:PORT/threads         create thread
        GET  http://HOST:PORT/docs            OpenAPI docs
    """
    from rai.cli.serve import serve as _serve
    _serve(
        agent=agent, model=model, port=port, host=host,
        dev=dev, browser=browser, workers=workers,
        api_key=api_key, base_url=base_url,
        target=target, rate_limit=rate_limit,
        log_level=log_level, env_file=env_file,
    )


# ---------------------------------------------------------------------------
# rai http  (subcommand group: serve, run, agents, threads, tasks,
#            subagents, status, logs, approve, cancel)
# ---------------------------------------------------------------------------

from rai.cli.http_server import http_app  # noqa: E402
app.add_typer(http_app, name="http")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cli_main() -> None:
    app()


if __name__ == "__main__":
    cli_main()
