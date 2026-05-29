"""Skill discovery — list and find skills across all configured directories."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills as _sdk_list_skills  # noqa: PLC2701

from rai.config.settings import settings

logger = logging.getLogger(__name__)

_MAX_NAME_LEN = 64

# Bundled skills shipped inside the RAI package (lowest precedence — always overridable)
_BUNDLED_SKILLS_DIR = Path(__file__).parent.parent / "data" / "skills"


# ---------------------------------------------------------------------------
# Name validation (Agent Skills spec)
# ---------------------------------------------------------------------------


def _validate_skill_name(name: str) -> tuple[bool, str]:
    """Validate a skill name per Agent Skills spec."""
    if not name or not name.strip():
        return False, "cannot be empty"
    if len(name) > _MAX_NAME_LEN:
        return False, f"cannot exceed {_MAX_NAME_LEN} characters"
    if ".." in name or "/" in name or "\\" in name:
        return False, "cannot contain path components"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, "must be lowercase alphanumeric with single hyphens only"
    for c in name:
        if c == "-":
            continue
        if (c.isalpha() and c.islower()) or c.isdigit():
            continue
        return False, "must be lowercase alphanumeric with single hyphens only"
    return True, ""


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------


def _skill_source_dirs(
    agent_name: str,
    cwd: Path | None = None,
) -> list[tuple[Path | None, str]]:
    """Return (path, source-label) pairs in ascending precedence order."""
    dirs: list[tuple[Path | None, str]] = [
        (_BUNDLED_SKILLS_DIR, "bundled"),          # lowest precedence — always overridable
        (settings.user_skills_dir, "user"),
        (settings.agent_skills_dir(agent_name), "user"),
        (settings.get_project_skills_dir(cwd=cwd), "project"),
    ]
    claude_user = settings.get_user_claude_skills_dir()
    if claude_user.exists():
        dirs.append((claude_user, "claude (experimental)"))
    claude_project = settings.get_project_claude_skills_dir(cwd=cwd)
    if claude_project:
        dirs.append((claude_project, "claude (experimental)"))
    return dirs


def list_skills(
    agent_name: str,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    """Discover all skills for *agent_name*, merged by precedence.

    Returns a list of dicts with at minimum: name, description, path, source.
    """
    merged: dict[str, dict[str, Any]] = {}
    for skill_dir, source_label in _skill_source_dirs(agent_name, cwd=cwd):
        if not skill_dir or not skill_dir.exists():
            continue
        try:
            backend = FilesystemBackend(root_dir=str(skill_dir))
            raw_skills = _sdk_list_skills(backend=backend, source_path=".")
            for skill in raw_skills:
                merged[skill["name"]] = {**skill, "source": source_label}
        except Exception:
            logger.warning("Could not load skills from %s", skill_dir, exc_info=True)
    return list(merged.values())


def find_skill(
    name: str,
    agent_name: str,
    cwd: Path | None = None,
) -> dict[str, Any] | None:
    """Return skill metadata for *name*, or None if not found."""
    return next((s for s in list_skills(agent_name, cwd=cwd) if s["name"] == name), None)
