"""ClaudeAgentRegistry — register multiple named agents.

Each entry maps a name → ClaudeAgentBuilder spec.  Calling .build(name)
produces a fresh RunnableClaudeAgent for that spec.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rai.engine.claude_sdk.agent import RunnableClaudeAgent
    from rai.engine.claude_sdk.builder import ClaudeAgentBuilder

logger = logging.getLogger(__name__)


class ClaudeAgentRegistry:
    """Thread-safe registry of named ClaudeAgentBuilder specs.

    Example::

        registry = ClaudeAgentRegistry()
        registry.register("pentest", ClaudeAgent.builder().agent_name("pentest"))
        registry.register("recon", ClaudeAgent.builder().agent_name("recon"))

        async with registry.build("pentest") as agent:
            result = await agent.run("scan example.com for open ports")
    """

    def __init__(self) -> None:
        self._builders: dict[str, "ClaudeAgentBuilder"] = {}
        self._lock = threading.Lock()

    def register(self, name: str, builder: "ClaudeAgentBuilder") -> "ClaudeAgentRegistry":
        """Register a builder under name.  Returns self for chaining."""
        with self._lock:
            self._builders[name] = builder
        return self

    def get(self, name: str) -> "ClaudeAgentBuilder":
        """Return the builder for name.  Raises KeyError if not found."""
        with self._lock:
            return self._builders[name]

    def build(self, name: str) -> "RunnableClaudeAgent":
        """Build a fresh RunnableClaudeAgent for the named spec."""
        return self.get(name).build()

    def list(self) -> list[str]:
        """Return sorted list of registered agent names."""
        with self._lock:
            return sorted(self._builders)

    @classmethod
    def default(cls) -> "ClaudeAgentRegistry":
        """Return a registry pre-populated from ~/.rai/agents/ directory scan.

        Each subdirectory with an AGENTS.md is registered as a ClaudeAgentBuilder
        with that directory name as the agent_name.
        """
        from rai.config.settings import settings
        from rai.engine.claude_sdk.builder import ClaudeAgentBuilder

        registry = cls()
        agents_root = settings.agents_dir
        if not agents_root.exists():
            return registry

        for subdir in sorted(agents_root.iterdir()):
            if not subdir.is_dir():
                continue
            agents_md = subdir / "AGENTS.md"
            if not agents_md.exists():
                continue
            name = subdir.name
            builder = ClaudeAgentBuilder().agent_name(name)
            registry.register(name, builder)
            logger.debug("Registered claude agent '%s' from %s", name, subdir)

        return registry
