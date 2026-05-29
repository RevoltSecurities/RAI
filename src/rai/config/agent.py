"""Per-agent configuration — merged from AGENTS.md YAML block and config.toml.

Config is resolved in this priority order (highest wins):

  1. ~/.rai/agents/<name>/AGENTS.md  — YAML block: model, api_key, base_url
  2. ~/.rai/agents/<name>/config.toml — all fields; temperature/max_tokens only here

Example AGENTS.md YAML block:

    ---
    name: my-agent
    description: My specialised agent
    model: openai/gpt-4o
    api_key: sk-...
    base_url: https://my-proxy.example.com/v1/
    ---

Example config.toml (temperature / max_tokens only):

    temperature = 0.7
    max_tokens = 8192

The model field accepts:
  - LiteLLM format:  "openai/gpt-4o",  "anthropic/claude-3-5-sonnet-20241022"
  - LiteLLM prefix:  "litellm:openai/gpt-4o"
  - Langchain format: "anthropic:claude-sonnet-4-6"  (existing RAI default)
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Per-agent model and API configuration."""

    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    rate_limit_profile: str = ""

    def is_empty(self) -> bool:
        """True when no meaningful overrides are set."""
        return not (self.model or self.api_key or self.base_url)

    def to_toml_str(self) -> str:
        """Serialize to a minimal TOML string."""
        lines: list[str] = []
        if self.model:
            lines.append(f'model = "{self.model}"')
        if self.api_key:
            lines.append(f'api_key = "{self.api_key}"')
        if self.base_url:
            lines.append(f'base_url = "{self.base_url}"')
        lines.append(f"temperature = {self.temperature}")
        lines.append(f"max_tokens = {self.max_tokens}")
        if self.rate_limit_profile:
            lines.append(f'rate_limit_profile = "{self.rate_limit_profile}"')
        return "\n".join(lines) + "\n"


def _config_path(agent_name: str) -> Path:
    from rai.config.settings import settings
    return settings.agent_dir(agent_name) / "config.toml"


def load_agent_config(agent_name: str) -> AgentConfig:
    """Load per-agent config merged from AGENTS.md (model/api_key/base_url)
    and config.toml (temperature/max_tokens).

    Priority: AGENTS.md > config.toml > defaults.
    """
    # 1. config.toml — temperature/max_tokens + fallback model/api_key/base_url
    toml_model = toml_api_key = toml_base_url = ""
    temperature = 0.7
    max_tokens = 8192
    rate_limit_profile = ""

    path = _config_path(agent_name)
    if path.exists():
        try:
            with path.open("rb") as f:
                data: dict[str, Any] = tomllib.load(f)
            toml_model = str(data.get("model", ""))
            toml_api_key = str(data.get("api_key", ""))
            toml_base_url = str(data.get("base_url", ""))
            temperature = float(data.get("temperature", 0.7))
            max_tokens = int(data.get("max_tokens", 8192))
            rate_limit_profile = str(data.get("rate_limit_profile", ""))
        except Exception as exc:
            logger.warning("Could not read agent config %s: %s", path, exc)

    # 2. AGENTS.md YAML block — overrides config.toml for model/api_key/base_url
    md_model = md_api_key = md_base_url = ""
    try:
        from rai.agents.parser import parse_agents_md
        from rai.config.settings import settings

        entries = parse_agents_md(settings.agent_md_path(agent_name))
        if entries:
            entry = next((e for e in entries if e.name == agent_name), entries[0])
            _INHERIT = {"inherit", ""}
            if entry.model not in _INHERIT:
                md_model = entry.model
            if entry.api_key not in _INHERIT:
                md_api_key = entry.api_key
            if entry.base_url not in _INHERIT:
                md_base_url = entry.base_url
    except Exception as exc:
        logger.debug("Could not read AGENTS.md config for '%s': %s", agent_name, exc)

    return AgentConfig(
        model=md_model or toml_model,
        api_key=md_api_key or toml_api_key,
        base_url=md_base_url or toml_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        rate_limit_profile=rate_limit_profile,
    )


def save_agent_config(agent_name: str, config: AgentConfig) -> Path:
    """Write config to ~/.rai/agents/<name>/config.toml and return its path."""
    from rai.config.settings import settings
    settings.ensure_agent_dir(agent_name)
    path = _config_path(agent_name)
    path.write_text(config.to_toml_str(), encoding="utf-8")
    return path


def scaffold_agent_config(agent_name: str) -> Path:
    """Write a commented config template (if no config exists) and return the path."""
    path = _config_path(agent_name)
    if path.exists():
        return path

    from rai.config.settings import settings
    settings.ensure_agent_dir(agent_name)

    template = (
        "# RAI per-agent configuration\n"
        "# Uncomment and edit the fields you want to override.\n"
        "\n"
        "# model = \"openai/gpt-4o\"          # LiteLLM format\n"
        "# model = \"anthropic/claude-3-5-sonnet-20241022\"\n"
        "# model = \"anthropic:claude-sonnet-4-6\"  # LangChain provider format\n"
        "\n"
        "# api_key = \"\"     # Leave empty to use env var (OPENAI_API_KEY etc.)\n"
        "# base_url = \"\"    # Custom OpenAI-compatible endpoint\n"
        "\n"
        "temperature = 0.7\n"
        "max_tokens = 8192\n"
    )
    path.write_text(template, encoding="utf-8")
    return path
