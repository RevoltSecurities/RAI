"""RAI config package — settings and per-agent configuration."""

from rai.config.settings import (
    RAISettings,
    get_settings,
    settings,
    SHELL_ALLOW_ALL,
    SHELL_TOOL_NAMES,
    is_shell_command_allowed,
)
from rai.config.agent import (
    AgentConfig,
    load_agent_config,
    save_agent_config,
    scaffold_agent_config,
)

__all__ = [
    "RAISettings",
    "get_settings",
    "settings",
    "SHELL_ALLOW_ALL",
    "SHELL_TOOL_NAMES",
    "is_shell_command_allowed",
    "AgentConfig",
    "load_agent_config",
    "save_agent_config",
    "scaffold_agent_config",
]
