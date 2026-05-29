"""RAI defaults package — default agents and skills."""

from rai.defaults.agents import ensure_default_agents, _load_prompt
from rai.defaults.skills import ensure_default_skills

__all__ = [
    "ensure_default_agents",
    "_load_prompt",
    "ensure_default_skills",
]
