"""rai refs — manage offline reference repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from rai.data.references.cache import REFERENCE_REPOS, cache_path, is_cached

refs_app = typer.Typer(name="refs", help="Manage offline reference repositories (~/.rai/references/)")


@refs_app.command("install")
def install(
    force: bool = typer.Option(False, "--force", "-f", help="Re-clone already-cached repos."),
    slug: str = typer.Argument(default="", help="Single repo slug to install. Default: all repos."),
) -> None:
    """Clone reference repos to ~/.rai/references/ for offline use."""
    repos = {slug: REFERENCE_REPOS[slug]} if slug else REFERENCE_REPOS
    if slug and slug not in REFERENCE_REPOS:
        typer.echo(f"Unknown slug '{slug}'. Available: {', '.join(REFERENCE_REPOS)}", err=True)
        raise typer.Exit(1)

    for s, url in repos.items():
        path = cache_path(s)
        if is_cached(s) and not force:
            typer.echo(f"  [skip]  {s} already cached at {path}")
            continue
        if path.exists() and force:
            typer.echo(f"  [clean] removing {path}")
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        typer.echo(f"  [clone] {url}")
        typer.echo(f"       → {path}")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            typer.echo(f"  [done]  {s}")
        else:
            typer.echo(f"  [fail]  {s}: {result.stderr.strip()}", err=True)


@refs_app.command("update")
def update(
    slug: str = typer.Argument(default="", help="Single repo slug to update. Default: all cached repos."),
) -> None:
    """Run git pull on cached reference repos."""
    repos = {slug: REFERENCE_REPOS[slug]} if slug else REFERENCE_REPOS
    if slug and slug not in REFERENCE_REPOS:
        typer.echo(f"Unknown slug '{slug}'. Available: {', '.join(REFERENCE_REPOS)}", err=True)
        raise typer.Exit(1)

    for s in repos:
        path = cache_path(s)
        if not is_cached(s):
            typer.echo(f"  [skip]  {s} not cached — run 'rai refs install {s}' first")
            continue
        typer.echo(f"  [pull]  {s}")
        result = subprocess.run(
            ["git", "-C", str(path), "pull", "--ff-only"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            typer.echo(f"  [done]  {s}: {result.stdout.strip()}")
        else:
            typer.echo(f"  [fail]  {s}: {result.stderr.strip()}", err=True)


@refs_app.command("status")
def status() -> None:
    """Show which reference repos are cached and their disk usage."""
    import shutil

    typer.echo("Reference repository status:\n")
    rows: list[tuple[str, str, str]] = []
    for s, url in REFERENCE_REPOS.items():
        path = cache_path(s)
        if is_cached(s):
            size = _dir_size_mb(path)
            state = f"cached ({size:.1f} MB)"
        elif path.exists():
            state = "partial (missing .git)"
        else:
            state = "not cached"
        rows.append((s, state, url))

    col_w = max(len(r[0]) for r in rows) + 2
    for s, state, url in rows:
        typer.echo(f"  {s:<{col_w}} {state}")

    typer.echo(f"\nCache root: {cache_path('').parent}")
    typer.echo("Run 'rai refs install' to clone all repos.")


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)
