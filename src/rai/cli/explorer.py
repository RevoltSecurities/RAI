"""RAI Explorer — browse and manage agents, skills, MCP servers, and memory.

Provides rich-formatted table views for:
  - Agents    (rai explore agents)
  - Skills    (rai explore skills)
  - MCP       (rai explore mcp)
  - Memory    (rai explore memory <agent-name>)

Also provides management commands:
  - rai explore agents list/reset/delete
  - rai explore skills create/delete
  - rai explore mcp add/remove
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from rai.config.settings import settings

console = Console()


# ---------------------------------------------------------------------------
# Agents explorer
# ---------------------------------------------------------------------------


def list_agents() -> None:
    """Print a table of all available RAI agents."""
    agents_dir = settings.agents_dir
    if not agents_dir.exists() or not any(agents_dir.iterdir()):
        console.print("[yellow]No agents found.[/yellow]")
        console.print("[dim]Agents are created automatically when you first run 'rai chat'.[/dim]")
        return

    table = Table(title="RAI Agents", box=box.ROUNDED, show_header=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Memory Files", justify="center")
    table.add_column("Skills")
    table.add_column("Subagents", justify="center")
    table.add_column("Path", style="dim")

    for agent_path in sorted(agents_dir.iterdir()):
        if not agent_path.is_dir() or agent_path.is_symlink():
            continue
        name = agent_path.name
        memories_dir = agent_path / "memories"
        memory_count = len(list(memories_dir.glob("*.md"))) if memories_dir.exists() else 0
        skills_dir = agent_path / "skills"
        skill_count = len(list(skills_dir.glob("*.md"))) if skills_dir.exists() else 0
        subagents_dir = agent_path / "subagents"
        subagent_count = len(list(subagents_dir.glob("*.toml"))) if subagents_dir.exists() else 0

        table.add_row(
            name,
            str(memory_count),
            str(skill_count),
            str(subagent_count),
            str(agent_path),
        )

    console.print(table)


def reset_agent(agent_name: str, *, dry_run: bool = False) -> None:
    """Reset an agent's memory files to empty defaults."""
    agent_dir = settings.agent_dir(agent_name)
    if not agent_dir.exists():
        console.print(f"[red]Agent '{agent_name}' not found.[/red]")
        return

    memories_dir = agent_dir / "memories"
    if dry_run:
        console.print(f"Would reset memory files in {memories_dir}")
        return

    if memories_dir.exists():
        shutil.rmtree(memories_dir)
    settings.ensure_memory_files(agent_name)
    console.print(f"[green]✓[/green] Memory reset for agent '{agent_name}'")


def show_agent(agent_name: str) -> None:
    """Show details for a specific agent."""
    agent_dir = settings.agent_dir(agent_name)
    if not agent_dir.exists():
        console.print(f"[red]Agent '{agent_name}' not found.[/red]")
        return

    console.print(Panel(f"[bold cyan]{agent_name}[/bold cyan]", title="Agent Details"))
    console.print(f"[dim]Path:[/dim] {agent_dir}")

    # Memory
    for mem_path in settings.all_memory_paths(agent_name):
        if mem_path.exists():
            size = mem_path.stat().st_size
            console.print(f"  [green]●[/green] {mem_path.name} ({size} bytes)")
        else:
            console.print(f"  [dim]○ {mem_path.name} (not created)[/dim]")

    # Findings
    findings_path = settings.findings_path(agent_name)
    if findings_path.exists():
        try:
            findings = json.loads(findings_path.read_text())
            console.print(f"  [yellow]⚠[/yellow] findings.json — {len(findings)} finding(s)")
        except Exception:
            console.print(f"  [red]✗[/red] findings.json (parse error)")


# ---------------------------------------------------------------------------
# Skills explorer
# ---------------------------------------------------------------------------


def list_skills(agent_name: str | None = None) -> None:
    """List all skills available to an agent (or global user skills)."""
    sources: list[tuple[str, Path]] = [
        ("user", settings.user_skills_dir),
    ]
    if agent_name:
        sources.append((f"agent:{agent_name}", settings.agent_skills_dir(agent_name)))

    project_skills = settings.get_project_skills_dir()
    if project_skills:
        sources.append(("project", project_skills))

    table = Table(title="RAI Skills", box=box.ROUNDED)
    table.add_column("Name", style="bold cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Description")
    table.add_column("Path", style="dim")

    seen: set[str] = set()
    for source_label, skills_dir in sources:
        if not skills_dir or not skills_dir.exists():
            continue
        for skill_file in sorted(skills_dir.glob("**/*.md")):
            name = skill_file.stem
            if name in seen:
                continue
            seen.add(name)
            desc = _read_skill_description(skill_file)
            table.add_row(name, source_label, desc, str(skill_file))

    if not seen:
        console.print("[yellow]No skills found.[/yellow]")
        console.print(f"[dim]Add .md skill files to {settings.user_skills_dir}[/dim]")
        return
    console.print(table)


def _read_skill_description(path: Path) -> str:
    """Read the first non-frontmatter line as the description."""
    try:
        content = path.read_text(encoding="utf-8")
        in_frontmatter = False
        for line in content.splitlines():
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            line = line.strip().lstrip("#").strip()
            if line:
                return line[:100] + ("..." if len(line) > 100 else "")
    except OSError:
        pass
    return ""


def create_skill(name: str, agent_name: str | None = None) -> None:
    """Scaffold a new skill .md file."""
    if agent_name:
        skills_dir = settings.agent_skills_dir(agent_name)
        skills_dir.mkdir(parents=True, exist_ok=True)
    else:
        skills_dir = settings.user_skills_dir
        skills_dir.mkdir(parents=True, exist_ok=True)

    skill_file = skills_dir / f"{name}.md"
    if skill_file.exists():
        console.print(f"[yellow]Skill '{name}' already exists at {skill_file}[/yellow]")
        return

    skill_file.write_text(
        f"---\nname: {name}\ndescription: Describe what this skill does\n---\n\n"
        f"# {name}\n\n## Description\n\nDescribe your skill here.\n\n"
        f"## Usage\n\nExplain how to invoke this skill.\n\n"
        f"## Steps\n\n1. Step one\n2. Step two\n",
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] Created skill '{name}' at {skill_file}")
    console.print(f"[dim]Edit the file to add your skill content.[/dim]")


# ---------------------------------------------------------------------------
# MCP explorer
# ---------------------------------------------------------------------------


def list_mcp_servers(cwd: Path | None = None, agent_name: str = "rai") -> None:
    """List MCP servers from all discovered config files."""
    from rai.mcp.config import discover_mcp_configs, load_mcp_config

    config_paths = discover_mcp_configs(cwd=cwd, agent_name=agent_name)
    if not config_paths:
        console.print("[yellow]No MCP config files found.[/yellow]")
        console.print(f"[dim]Add servers to {settings.user_mcp_config_path}[/dim]")
        return

    table = Table(title="MCP Servers", box=box.ROUNDED)
    table.add_column("Server", style="bold cyan")
    table.add_column("Transport", style="yellow")
    table.add_column("Command / URL")
    table.add_column("Source", style="dim")

    for config_path in config_paths:
        try:
            cfg = load_mcp_config(config_path)
        except Exception as e:
            console.print(f"[red]Error loading {config_path}: {e}[/red]")
            continue

        for server_name, server_cfg in cfg.get("mcpServers", {}).items():
            transport = server_cfg.get("type") or server_cfg.get("transport", "stdio")
            if transport in {"sse", "http"}:
                endpoint = server_cfg.get("url", "")
            else:
                cmd = server_cfg.get("command", "")
                args = " ".join(server_cfg.get("args", []))
                endpoint = f"{cmd} {args}".strip()
            table.add_row(server_name, transport, endpoint, str(config_path))

    console.print(table)


def add_mcp_server(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    url: str = "",
    transport: str = "stdio",
) -> None:
    """Add a new MCP server to ~/.rai/.mcp.json."""
    config_path = settings.user_mcp_config_path
    settings.user_rai_dir.mkdir(parents=True, exist_ok=True)

    # Load existing config
    existing: dict[str, Any] = {"mcpServers": {}}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except Exception:
            pass

    servers = existing.setdefault("mcpServers", {})
    if name in servers:
        console.print(f"[yellow]Server '{name}' already exists in {config_path}[/yellow]")
        return

    if transport in {"sse", "http"}:
        servers[name] = {"type": transport, "url": url}
    else:
        entry: dict[str, Any] = {"command": command}
        if args:
            entry["args"] = args
        if env:
            entry["env"] = env
        servers[name] = entry

    config_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    console.print(f"[green]✓[/green] Added MCP server '{name}' to {config_path}")


def remove_mcp_server(name: str) -> None:
    """Remove a server from ~/.rai/.mcp.json."""
    config_path = settings.user_mcp_config_path
    if not config_path.exists():
        console.print(f"[yellow]No MCP config at {config_path}[/yellow]")
        return

    try:
        existing = json.loads(config_path.read_text())
    except Exception as e:
        console.print(f"[red]Failed to parse {config_path}: {e}[/red]")
        return

    servers = existing.get("mcpServers", {})
    if name not in servers:
        console.print(f"[yellow]Server '{name}' not found in {config_path}[/yellow]")
        return

    del servers[name]
    config_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    console.print(f"[green]✓[/green] Removed MCP server '{name}' from {config_path}")


# ---------------------------------------------------------------------------
# Memory explorer
# ---------------------------------------------------------------------------


def show_memory(agent_name: str) -> None:
    """Print all memory files for an agent."""
    from rich.markdown import Markdown

    for path in settings.all_memory_paths(agent_name):
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            console.print(Panel(f"[dim](empty)[/dim]", title=f"[bold]{path.name}[/bold]"))
        else:
            console.print(Panel(Markdown(content), title=f"[bold]{path.name}[/bold]"))


def clear_memory(agent_name: str, memory_type: str = "all") -> None:
    """Clear one or all memory files for an agent."""
    headers = {
        "short_term": "# Short-Term Memory\n\nRecent context for this session.\n",
        "long_term": "# Long-Term Memory\n\nPersistent facts learned across sessions.\n",
        "episodic": "# Episodic Memory\n\nPast engagement summaries and findings.\n",
    }
    if memory_type == "all":
        for key, header in headers.items():
            p = getattr(settings, f"{key}_memory_path")(agent_name)
            if p.exists():
                p.write_text(header, encoding="utf-8")
                console.print(f"[green]✓[/green] Cleared {p.name}")
    elif memory_type in headers:
        p = getattr(settings, f"{memory_type}_memory_path")(agent_name)
        if p.exists():
            p.write_text(headers[memory_type], encoding="utf-8")
            console.print(f"[green]✓[/green] Cleared {p.name}")
    else:
        console.print(f"[red]Unknown memory type '{memory_type}'. Use: short_term, long_term, episodic, all[/red]")
