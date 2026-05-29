"""RAI skills package — skill discovery, invocation, and CLI commands."""

from rai.skills.discovery import (
    list_skills,
    find_skill,
    _validate_skill_name,
)
from rai.skills.invocation import (
    SkillInvocationResult,
    resolve_slash_command,
)
from rai.skills.commands import (
    cmd_list,
    cmd_create,
    cmd_info,
    cmd_delete,
)

__all__ = [
    "list_skills",
    "find_skill",
    "_validate_skill_name",
    "SkillInvocationResult",
    "resolve_slash_command",
    "cmd_list",
    "cmd_create",
    "cmd_info",
    "cmd_delete",
]
