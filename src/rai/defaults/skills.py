"""Default skills shipped with every RAI installation.

Skills are stored as full directories under src/rai/data/skills/ and copied
to ~/.rai/skills/ on first run.  Each skill directory may contain:

  SKILL.md        — main skill instructions (required)
  scripts/        — Python/bash helpers the agent can invoke
  references/     — markdown docs, templates, payload lists, examples

Seeding is idempotent — existing SKILL.md files are never overwritten,
preserving user customisations.  The rest of the directory tree is also
skipped if the skill's SKILL.md already exists.

Bundled skills
--------------
  skill-creator   — Creates RAI skills, agents, scripts, and references.
                    Model-agnostic, cybersecurity-focused.  Includes cross-
                    platform path resolver (rai_paths.py), scaffolding scripts
                    (create_skill.py, create_agent.py), and reference templates.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Package-internal data directory: src/rai/data/skills/
_DATA_SKILLS_DIR = Path(__file__).parent.parent / "data" / "skills"


def ensure_default_skills() -> bool:
    """Copy bundled skills from package data to ``~/.rai/skills/``.

    Each skill directory is copied in full (SKILL.md + scripts/ + references/).
    A skill is skipped if its SKILL.md already exists at the destination,
    preserving any user customisations.

    Returns:
        True if at least one skill was installed, False otherwise.
    """
    from rai.config.settings import settings

    if not _DATA_SKILLS_DIR.exists():
        logger.debug("No bundled skills found at %s; skipping", _DATA_SKILLS_DIR)
        return False

    skills_root = settings.user_skills_dir
    created = False

    for skill_src in sorted(_DATA_SKILLS_DIR.iterdir()):
        if not skill_src.is_dir():
            continue

        skill_name = skill_src.name
        skill_dest = skills_root / skill_name
        skill_md_dest = skill_dest / "SKILL.md"

        if skill_md_dest.exists():
            logger.debug("Default skill '%s' already exists; skipping", skill_name)
            continue

        try:
            shutil.copytree(skill_src, skill_dest, dirs_exist_ok=False)
            logger.info("Installed default skill '%s' to %s", skill_name, skill_dest)
            created = True
        except FileExistsError:
            # Race condition: another process created it between our check and copy
            logger.debug("Skill '%s' appeared concurrently; skipping", skill_name)
        except OSError as exc:
            logger.warning(
                "Could not install default skill '%s' to %s: %s",
                skill_name, skill_dest, exc,
            )

    return created
