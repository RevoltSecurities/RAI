"""Skill invocation — slash command resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rai.skills.discovery import _validate_skill_name, find_skill


# ---------------------------------------------------------------------------
# Slash command dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillInvocationResult:
    """Result of resolving a slash command."""

    prompt: str
    skill_name: str
    allowed_tools: list[str] | None = None


def resolve_slash_command(
    raw_input: str,
    agent_name: str,
    cwd: Path | None = None,
) -> SkillInvocationResult | None:
    """Parse a /slash-command and return the skill invocation prompt, or None.

    Returns None when the input is not a slash command or the skill isn't found
    (in which case the caller should report an error — the raw_input was a slash
    command but we couldn't resolve it).

    Raises ValueError when *raw_input* starts with "/" but the skill name part
    is structurally invalid, so callers can show a targeted error message.
    """
    if not raw_input.startswith("/"):
        return None

    # Strip leading "/" and split into name + args
    body = raw_input[1:].strip()
    parts = body.split(None, 1)
    if not parts:
        return None

    skill_name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # Special: /skills or /skill → list, handled by caller (we return None)
    if skill_name in {"skills", "skill"}:
        return None

    is_valid, err = _validate_skill_name(skill_name)
    if not is_valid:
        raise ValueError(f"'{skill_name}': {err}")

    skill = find_skill(skill_name, agent_name, cwd=cwd)
    if not skill:
        return None

    # Read SKILL.md content
    skill_path = Path(skill["path"])
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Could not read skill '{skill_name}': {e}") from e

    prompt = (
        f"I'm invoking the skill `{skill['name']}`. "
        "Below are the full instructions from the skill's SKILL.md file. "
        "Follow these instructions to complete the task.\n\n"
        f"---\n{content}\n---"
    )
    if args:
        prompt += f"\n\n**User request:** {args}"

    # allowed_tools: non-empty list from SKILL.md frontmatter, or None (no restriction)
    raw_tools: list[str] = skill.get("allowed_tools") or []
    allowed_tools: list[str] | None = raw_tools if raw_tools else None

    return SkillInvocationResult(prompt=prompt, skill_name=skill_name, allowed_tools=allowed_tools)
