"""RAI — the open-source AI security operator for the full cybersecurity spectrum: threat modeling, SAST, pentesting, red team, bug bounty, VAPT, and SOC operations."""
__version__ = "2.0.0"

# langchain_core's surface_langchain_deprecation_warnings() inserts a "default"
# filter at position 0 when it is first imported, pushing any earlier "ignore"
# filter down so it loses when langgraph fires the allowed_objects warning.
# Fix: replace warnings.filterwarnings with a wrapper that re-inserts our
# "ignore" at position 0 after every third-party insertion, then restore.
import warnings as _warnings

_orig_fw = _warnings.filterwarnings  # capture real function before we replace it


def _sticky_fw(action, message="", category=Warning, module="", lineno=0, append=False):
    _orig_fw(action, message, category, module, lineno, append)
    # After every insertion by any package, keep our filter at the front.
    _orig_fw("ignore", ".*allowed_objects.*", PendingDeprecationWarning)


_warnings.filterwarnings = _sticky_fw

from deepagents import AsyncSubAgent, CompiledSubAgent
from deepagents.middleware.subagents import SubAgent

_warnings.filterwarnings = _orig_fw  # restore

# Final re-apply with the exact subclass now that it is importable.
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning as _LCPDW
    _warnings.filterwarnings("ignore", message=".*allowed_objects.*", category=_LCPDW)
except ImportError:
    pass

from rai.engine.factory import create_rai_agent, get_system_prompt
from rai.engine.model import DEFAULT_AGENT_NAME, DEFAULT_MODEL, build_model, ModelConfig, list_providers
from rai.engine.runner import run_agent
from rai.mcp.loader import resolve_and_load_mcp_tools as load_mcp_tools
from rai.mcp.loader import load_subagents_mcp_tools_map
from rai.sessions.store import (
    build_stream_config,
    generate_thread_id,
    get_checkpointer,
)

try:
    from rai.middleware.prompt_cache import RAIPromptCachingMiddleware
except ImportError:
    pass

__all__ = [
    # ---- Agent factory ----
    "create_rai_agent",
    "get_system_prompt",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    # ---- Runner (handles subagent quiescence) ----
    "run_agent",
    # ---- Session helpers ----
    "generate_thread_id",
    "get_checkpointer",
    "build_stream_config",
    # ---- Subagent spec types ----
    "SubAgent",
    "AsyncSubAgent",
    "CompiledSubAgent",
    # ---- MCP helpers ----
    "load_mcp_tools",
    "load_subagents_mcp_tools_map",
    # ---- Model SDK ----
    "build_model",
    "ModelConfig",
    "list_providers",
    # ---- Middleware ----
    "RAIPromptCachingMiddleware",
]
