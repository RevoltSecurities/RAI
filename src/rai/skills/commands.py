"""Skills CLI commands — list, create, info, delete, add."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from rai.config.settings import settings
from rai.skills.discovery import _validate_skill_name, find_skill, list_skills

console = Console()


# ---------------------------------------------------------------------------
# SKILL.md template
# ---------------------------------------------------------------------------


def _generate_skill_template(name: str) -> str:
    title = name.title().replace("-", " ")
    description = (
        "Describe what this skill does and when to invoke it. "
        "Include specific triggers — scenarios, file types, or phrases that should activate it."
    )
    return f"""---
name: {name}
description: "{description}"
# Optional fields (Agent Skills spec):
# license: MIT
# compatibility: RAI
# metadata:
#   author: your-handle
#   version: "1.0"
---

# {title}

## Overview

[TODO: 1-2 sentences explaining what this skill enables]

## Instructions

### Step 1: [First Action]
[Explain what to do first]

### Step 2: [Second Action]
[Explain what to do next]

### Step 3: [Final Action]
[Explain how to complete the task]

## Best Practices

- [Best practice 1]
- [Best practice 2]

## Examples

### Example: [Scenario Name]

**User Request:** "/{name} [describe a typical request]"

**Approach:**
1. [Step-by-step breakdown]
2. [Expected outcome]
"""


# ---------------------------------------------------------------------------
# CLI operations (Rich output)
# ---------------------------------------------------------------------------


def cmd_list(agent_name: str, cwd: Path | None = None) -> None:
    """Print a Rich table of all available skills."""
    skills = list_skills(agent_name, cwd=cwd)

    if not skills:
        console.print("[yellow]No skills found.[/yellow]")
        console.print(
            f"[dim]Create a skill: rai skills create my-skill --agent {agent_name}[/dim]"
        )
        return

    table = Table(title="RAI Skills", box=box.ROUNDED)
    table.add_column("Name", style="bold cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Description")
    table.add_column("Path", style="dim")

    source_order = {"user": 0, "project": 1, "claude (experimental)": 2}
    for skill in sorted(skills, key=lambda s: (source_order.get(s["source"], 99), s["name"])):
        table.add_row(
            skill["name"],
            skill["source"],
            (skill.get("description") or "")[:80],
            str(Path(skill["path"]).parent),
        )

    console.print(table)
    console.print("[dim]Invoke with: /skill-name [args][/dim]")


def cmd_create(
    name: str,
    agent_name: str,
    *,
    project: bool = False,
    cwd: Path | None = None,
) -> None:
    """Scaffold a new SKILL.md in the appropriate skills directory."""
    is_valid, err = _validate_skill_name(name)
    if not is_valid:
        console.print(f"[red]Invalid skill name:[/red] {err}")
        console.print("[dim]Names must be lowercase alphanumeric with hyphens (e.g. web-recon)[/dim]")
        return

    if project:
        effective_cwd = cwd or Path.cwd()
        skills_dir = effective_cwd / ".rai" / "skills"
    else:
        skills_dir = settings.agent_skills_dir(agent_name)

    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = skills_dir / name

    # Resolve both to detect symlink traversal
    try:
        if not skill_dir.resolve().is_relative_to(skills_dir.resolve()):
            console.print("[red]Error:[/red] Skill path resolves outside skills directory.")
            return
    except OSError as e:
        console.print(f"[red]Error:[/red] Invalid path: {e}")
        return

    if skill_dir.exists():
        console.print(f"[yellow]Skill '{name}' already exists at {skill_dir}[/yellow]")
        return

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_generate_skill_template(name), encoding="utf-8")

    console.print(f"[green]✓[/green] Created skill '{name}' at {skill_dir}")
    console.print(f"[dim]Edit SKILL.md to add your instructions: nano {skill_md}[/dim]")


def cmd_info(name: str, agent_name: str, cwd: Path | None = None) -> None:
    """Show detailed info for a skill."""
    skill = find_skill(name, agent_name, cwd=cwd)
    if not skill:
        all_skills = list_skills(agent_name, cwd=cwd)
        console.print(f"[red]Skill '{name}' not found.[/red]")
        if all_skills:
            console.print("[dim]Available:[/dim]")
            for s in all_skills:
                console.print(f"  [dim]- {s['name']} ({s['source']})[/dim]")
        return

    skill_path = Path(skill["path"])
    console.print(f"\n[bold cyan]{skill['name']}[/bold cyan]  [dim]({skill['source']})[/dim]")
    console.print(f"[dim]Location:[/dim] {skill_path.parent}/")
    console.print(f"[dim]Description:[/dim] {skill.get('description', '')}")

    extras = {}
    for k in ("license", "compatibility", "allowed_tools", "metadata"):
        v = skill.get(k)
        if v:
            extras[k] = v
    for k, v in extras.items():
        console.print(f"[dim]{k.title()}:[/dim] {v}")

    supporting = [f.name for f in skill_path.parent.iterdir() if f.name != "SKILL.md"]
    if supporting:
        console.print(f"[dim]Supporting files:[/dim] {', '.join(supporting)}")

    content = skill_path.read_text(encoding="utf-8")
    console.print(f"\n[bold]SKILL.md content:[/bold]\n{content}")


def cmd_delete(
    name: str,
    agent_name: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    cwd: Path | None = None,
) -> None:
    """Delete a skill directory after validation and confirmation."""
    is_valid, err = _validate_skill_name(name)
    if not is_valid:
        console.print(f"[red]Invalid skill name:[/red] {err}")
        return

    skill = find_skill(name, agent_name, cwd=cwd)
    if not skill:
        console.print(f"[red]Skill '{name}' not found.[/red]")
        return

    skill_dir = Path(skill["path"]).parent

    # Determine allowed base dir for safety check
    if skill["source"] == "project":
        effective_cwd = cwd or Path.cwd()
        base_dir = effective_cwd / ".rai" / "skills"
    else:
        base_dir = settings.agent_skills_dir(agent_name)
        if not skill_dir.resolve().is_relative_to(base_dir.resolve()):
            base_dir = settings.user_skills_dir

    try:
        if not skill_dir.resolve().is_relative_to(base_dir.resolve()):
            console.print("[red]Error:[/red] Refusing to delete outside allowed skill dir.")
            return
    except OSError as e:
        console.print(f"[red]Error:[/red] Path validation failed: {e}")
        return

    if skill_dir.is_symlink():
        console.print("[red]Error:[/red] Refusing to delete a symlink.")
        return

    if dry_run:
        file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
        console.print(f"[dim]Would delete:[/dim] {skill_dir}/ ({file_count} files)")
        return

    if not force:
        console.print(
            f"[yellow]Delete skill '{name}' at {skill_dir}? (y/N)[/yellow] ", end=""
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return
        if answer not in {"y", "yes"}:
            console.print("[dim]Cancelled.[/dim]")
            return

    try:
        shutil.rmtree(skill_dir)
        console.print(f"[green]✓[/green] Deleted skill '{name}'")
    except OSError as e:
        console.print(f"[red]Failed to delete:[/red] {e}")


# ---------------------------------------------------------------------------
# cmd_delete_bulk — bulk-delete skills discovered from a repo or local path
# ---------------------------------------------------------------------------


def _skill_name_from_md(skill_md: Path, src_root: Path, source: str) -> str:
    """Derive the installed skill name from a discovered SKILL.md path."""
    skill_src_dir = skill_md.parent
    if skill_src_dir == src_root:
        return _repo_name_from_url(source) if _is_git_url(source) else src_root.name
    return skill_src_dir.name


def _delete_one(skill_dir: Path, skill_name: str) -> bool:
    """Remove *skill_dir* after safety checks. Returns True on success."""
    if skill_dir.is_symlink():
        console.print(f"[red]Skipping '{skill_name}':[/red] refusing to delete a symlink.")
        return False
    try:
        shutil.rmtree(skill_dir)
        return True
    except OSError as e:
        console.print(f"[red]Failed to delete '{skill_name}':[/red] {e}")
        return False


def cmd_delete_bulk(
    source: str,
    agent_name: str,
    *,
    skill_filter: str = "",
    force: bool = False,
    dry_run: bool = False,
    project: bool = False,
    cwd: Path | None = None,
    yes: bool = False,
) -> None:
    """Bulk-delete skills whose names match those found in *source* (URL or local path)."""
    tmp_dir: Path | None = None

    try:
        # ── 1. Resolve source ────────────────────────────────────────────────
        if _is_git_url(source):
            if shutil.which("git") is None:
                console.print("[red]Error:[/red] git is not installed or not on PATH.")
                return
            source, _url_warn = _normalize_git_url(source)
            if _url_warn:
                console.print(f"[yellow]⚠ {_url_warn}[/yellow]")
            tmp_dir = Path(tempfile.mkdtemp(prefix="rai_skill_"))
            cmd = ["git", "clone", "--depth=1", "--quiet", source, str(tmp_dir)]
            console.print(f"[dim]Cloning {source} …[/dim]")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                err = result.stderr.strip()
                hint = ""
                if "Connection reset" in err or "unable to access" in err:
                    hint = "\n[dim]Hint: check your network connection and verify the URL is correct.[/dim]"
                elif "Repository not found" in err or "not found" in err.lower():
                    hint = "\n[dim]Hint: the repository may be private or the URL may be wrong.[/dim]"
                console.print(f"[red]git clone failed:[/red] {err}{hint}")
                return
            src_root = tmp_dir
        else:
            src_path = Path(source).expanduser().resolve()
            if not src_path.exists() or not src_path.is_dir():
                console.print(f"[red]Path not found or not a directory:[/red] {source}")
                return
            src_root = src_path

        # ── 2. Discover skill names from source ──────────────────────────────
        skill_mds = _find_skills_in_dir(src_root)
        if not skill_mds:
            console.print("[yellow]No SKILL.md found in source.[/yellow]")
            return

        if skill_filter:
            skill_mds = [p for p in skill_mds if p.parent.name == skill_filter]
            if not skill_mds:
                console.print(f"[red]Skill '{skill_filter}' not found in source.[/red]")
                return

        candidate_names = [_skill_name_from_md(md, src_root, source) for md in skill_mds]

        # ── 3. Resolve destination base ──────────────────────────────────────
        if project:
            effective_cwd = cwd or Path.cwd()
            dest_base = effective_cwd / ".rai" / "skills"
        else:
            dest_base = settings.agent_skills_dir(agent_name)

        # ── 4. Find which candidates are actually installed ──────────────────
        to_delete: list[tuple[str, Path]] = []
        not_found: list[str] = []

        for skill_name in candidate_names:
            is_valid, _ = _validate_skill_name(skill_name)
            if not is_valid:
                not_found.append(skill_name)
                continue
            skill_dir = dest_base / skill_name
            try:
                if skill_dir.exists() and skill_dir.resolve().is_relative_to(dest_base.resolve()):
                    to_delete.append((skill_name, skill_dir))
                else:
                    not_found.append(skill_name)
            except OSError:
                not_found.append(skill_name)

        if not to_delete:
            console.print("[yellow]None of the source skills are installed.[/yellow]")
            if not_found:
                console.print(f"[dim]Checked: {', '.join(not_found[:10])}{'…' if len(not_found) > 10 else ''}[/dim]")
            return

        # ── 5. Preview / confirm ─────────────────────────────────────────────
        if dry_run:
            console.print(f"[dim]Would delete {len(to_delete)} skill(s):[/dim]")
            for name, skill_dir in to_delete:
                file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
                console.print(f"  [cyan]{name}[/cyan] ({file_count} files) — {skill_dir}")
            if not_found:
                console.print(f"[dim]Not installed (skipped): {', '.join(not_found[:10])}{'…' if len(not_found) > 10 else ''}[/dim]")
            return

        if not force and not yes:
            console.print(f"[bold yellow]Delete {len(to_delete)} skill(s)?[/bold yellow]")
            for name, _ in to_delete[:20]:
                console.print(f"  [cyan]• {name}[/cyan]")
            if len(to_delete) > 20:
                console.print(f"  [dim]… and {len(to_delete) - 20} more[/dim]")
            console.print("[yellow](y/N)[/yellow] ", end="")
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Cancelled.[/dim]")
                return
            if answer not in {"y", "yes"}:
                console.print("[dim]Cancelled.[/dim]")
                return

        # ── 6. Delete ────────────────────────────────────────────────────────
        deleted: list[str] = []
        failed: list[str] = []
        for skill_name, skill_dir in to_delete:
            if _delete_one(skill_dir, skill_name):
                deleted.append(skill_name)
            else:
                failed.append(skill_name)

        if deleted:
            console.print(f"[green]✓[/green] Deleted {len(deleted)} skill(s)")
        if failed:
            console.print(f"[yellow]Failed:[/yellow] {', '.join(failed)}")
        if not_found:
            console.print(f"[dim]Not installed (skipped): {len(not_found)} skill(s)[/dim]")

    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# cmd_add_git — install skills from a git repo or local path
# ---------------------------------------------------------------------------


def _is_git_url(src: str) -> bool:
    return src.startswith(("https://", "http://", "git@", "git://", "ssh://"))


_GITHUB_TYPOS = {
    "gihub.com", "gitub.com", "gthub.com", "githb.com", "githbu.com",
    "gihtub.com", "gituhb.com", "gtihub.com", "gihhub.com", "gitnub.com",
    "githup.com", "githug.com", "githun.com",
}


def _normalize_git_url(url: str) -> tuple[str, str | None]:
    """Return (corrected_url, warning) — warning is None when no change was made."""
    import re
    m = re.match(r'^(https?://)([^/]+)(/.*)$', url)
    if not m:
        return url, None
    scheme, host, path = m.groups()
    if host.lower() in _GITHUB_TYPOS:
        fixed = f"{scheme}github.com{path}"
        return fixed, f"Typo in URL: '{host}' → 'github.com'. Using {fixed}"
    return url, None


def _repo_name_from_url(url: str) -> str:
    """Extract a human-readable repo name from a URL."""
    name = url.rstrip("/").split("/")[-1]
    return name.removesuffix(".git") or "skill"


def _find_skills_in_dir(root: Path, max_depth: int = 6) -> list[Path]:
    """Return paths to every SKILL.md found recursively under *root*.

    If a SKILL.md exists at the root itself the whole repo is treated as a
    single skill and only that file is returned.  Otherwise every SKILL.md
    found up to *max_depth* directory levels is returned (sorted).

    Hidden directories (names starting with '.') are skipped at every level.
    """
    # Depth 0 — root is itself the skill
    if (root / "SKILL.md").exists():
        return [root / "SKILL.md"]

    results: list[Path] = []

    def _walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                md = entry / "SKILL.md"
                if md.exists():
                    results.append(md)
                else:
                    _walk(entry, depth + 1)

    _walk(root, 1)
    return results


def cmd_add_git(
    source: str,
    agent_name: str,
    *,
    skill_filter: str = "",
    name_override: str = "",
    branch: str = "",
    project: bool = False,
    cwd: Path | None = None,
    yes: bool = False,
) -> None:
    """Install skill(s) from a git repo URL or local directory path."""
    tmp_dir: Path | None = None

    try:
        # ── 1. Acquire source into a temp directory ──────────────────────
        if _is_git_url(source):
            if shutil.which("git") is None:
                console.print("[red]Error:[/red] git is not installed or not on PATH.")
                return

            source, _url_warn = _normalize_git_url(source)
            if _url_warn:
                console.print(f"[yellow]⚠ {_url_warn}[/yellow]")

            tmp_dir = Path(tempfile.mkdtemp(prefix="rai_skill_"))
            cmd = ["git", "clone", "--depth=1", "--quiet"]
            if branch:
                cmd += ["--branch", branch]
            cmd += [source, str(tmp_dir)]

            console.print(f"[dim]Cloning {source} …[/dim]")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                err = result.stderr.strip()
                hint = ""
                if "Connection reset" in err or "unable to access" in err:
                    hint = "\n[dim]Hint: check your network connection and verify the URL is correct.[/dim]"
                elif "Repository not found" in err or "not found" in err.lower():
                    hint = "\n[dim]Hint: the repository may be private or the URL may be wrong.[/dim]"
                console.print(f"[red]git clone failed:[/red] {err}{hint}")
                return
            src_root = tmp_dir
        else:
            src_path = Path(source).expanduser().resolve()
            if not src_path.exists():
                console.print(f"[red]Path not found:[/red] {source}")
                return
            if not src_path.is_dir():
                console.print(f"[red]Not a directory:[/red] {source}")
                return
            src_root = src_path

        # ── 2. Discover skills ────────────────────────────────────────────
        skill_mds = _find_skills_in_dir(src_root)
        if not skill_mds:
            console.print(
                "[yellow]No SKILL.md found.[/yellow] "
                "The repo must have a SKILL.md at its root or inside subdirectories."
            )
            return

        # ── 3. Filter by --skill if requested ────────────────────────────
        if skill_filter:
            skill_mds = [p for p in skill_mds if p.parent.name == skill_filter]
            if not skill_mds:
                all_names = [p.parent.name if p.parent != src_root else _repo_name_from_url(source) for p in _find_skills_in_dir(src_root)]
                console.print(f"[red]Skill '{skill_filter}' not found.[/red] Available: {', '.join(all_names)}")
                return

        # ── 4. Confirm for multi-skill installs ──────────────────────────
        if len(skill_mds) > 1 and not yes:
            console.print(f"[bold]Found {len(skill_mds)} skills:[/bold]")
            for md in skill_mds:
                console.print(f"  [cyan]• {md.parent.name}[/cyan]")
            console.print("Install all? (y/N) ", end="")
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Cancelled.[/dim]")
                return
            if answer not in {"y", "yes"}:
                console.print("[dim]Cancelled. Use --skill <name> to install just one.[/dim]")
                return

        # ── 5. Resolve destination directory ─────────────────────────────
        if project:
            effective_cwd = cwd or Path.cwd()
            dest_base = effective_cwd / ".rai" / "skills"
        else:
            dest_base = settings.agent_skills_dir(agent_name)
        dest_base.mkdir(parents=True, exist_ok=True)

        # ── 6. Install each skill ─────────────────────────────────────────
        installed: list[str] = []
        skipped: list[str] = []

        for skill_md in skill_mds:
            skill_src_dir = skill_md.parent

            # Derive skill name
            if name_override and len(skill_mds) == 1:
                skill_name = name_override
            elif skill_src_dir == src_root:
                skill_name = _repo_name_from_url(source) if _is_git_url(source) else src_root.name
            else:
                skill_name = skill_src_dir.name

            # Validate name
            is_valid, err = _validate_skill_name(skill_name)
            if not is_valid:
                console.print(f"[yellow]Skipping '{skill_name}':[/yellow] invalid name — {err}")
                skipped.append(skill_name)
                continue

            dest_dir = dest_base / skill_name

            # Resolve paths to prevent traversal
            try:
                if not dest_dir.resolve().is_relative_to(dest_base.resolve()):
                    console.print(f"[red]Refusing '{skill_name}':[/red] path escapes skills directory.")
                    skipped.append(skill_name)
                    continue
            except OSError as e:
                console.print(f"[red]Path error for '{skill_name}':[/red] {e}")
                skipped.append(skill_name)
                continue

            if dest_dir.exists():
                console.print(f"[yellow]'{skill_name}' already exists at {dest_dir} — skipping.[/yellow]")
                skipped.append(skill_name)
                continue

            try:
                shutil.copytree(str(skill_src_dir), str(dest_dir))
                installed.append(skill_name)
            except OSError as e:
                console.print(f"[red]Failed to copy '{skill_name}':[/red] {e}")
                skipped.append(skill_name)

        # ── 7. Summary ────────────────────────────────────────────────────
        if installed:
            console.print(f"[green]✓[/green] Installed {len(installed)} skill(s) to {dest_base}:")
            for name in installed:
                console.print(f"  [cyan]/{name}[/cyan]")
        if skipped:
            console.print(f"[yellow]Skipped:[/yellow] {', '.join(skipped)}")
        if not installed and not skipped:
            console.print("[dim]Nothing to install.[/dim]")

    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
