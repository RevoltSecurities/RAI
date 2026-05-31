"""rai.sdk.engine — RAI agent factory, runner, and model SDK.

    from rai.sdk.engine import create_rai_agent, build_model, ModelConfig
"""

from rai.engine.factory import create_rai_agent, get_system_prompt
from rai.engine.model import (
    DEFAULT_AGENT_NAME,
    DEFAULT_MODEL,
    build_model,
    ModelConfig,
    list_providers,
)
from rai.engine.runner import run_agent

__all__ = [
    "create_rai_agent",
    "get_system_prompt",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    "build_model",
    "ModelConfig",
    "list_providers",
    "run_agent",
]
