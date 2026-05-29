"""RAI HTTP Server — expose RAI agents via a streaming HTTP API.

Quick-start
-----------
    from rai.sdk import RAIAgent
    from rai.harness import RAIHTTPServer, HTTPConfig

    server = RAIHTTPServer(HTTPConfig(port=8000))
    server.register(RAIAgent.builder().agent_name("rai").without_hitl())
    server.run()   # blocking; use run_async() in async context

Or via the builder:

    RAIAgent.builder().agent_name("rai").serve_http(port=8000)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from rai.sdk import RAIAgentBuilder

logger = logging.getLogger(__name__)


@dataclass
class HTTPConfig:
    """Configuration for the RAI HTTP server."""

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    workers: int = 1
    log_level: str = "info"
    cors_origins: list[str] = field(default_factory=list)
    api_key: str = ""
    max_run_timeout: float = 3600.0

    def __post_init__(self) -> None:
        if self.workers > 1:
            logger.warning(
                "RAI HTTP server: workers=%d requested, but all task state is process-local. "
                "Forcing workers=1 to avoid split state.",
                self.workers,
            )
            self.workers = 1


class RAIHTTPServer:
    """Blocking HTTP server that exposes registered RAI agents."""

    def __init__(self, config: HTTPConfig | None = None) -> None:
        self._config = config or HTTPConfig()
        self._builders: list[RAIAgentBuilder] = []

    def register(self, builder: RAIAgentBuilder) -> RAIHTTPServer:
        """Register an agent builder. Returns self for chaining."""
        self._builders.append(builder)
        return self

    def _build_app(self):
        from rai.harness.app import AgentPool, create_app

        pool = AgentPool()
        for builder in self._builders:
            pool.register(builder)
        return create_app(pool, self._config)

    def run(self) -> NoReturn:
        """Start the server (blocking). Call from a non-async context."""
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "RAI HTTP server requires 'uvicorn'. Install or upgrade RAI with:\n"
                "  pip install revolt-rai"
            ) from exc

        app = self._build_app()
        uvicorn.run(
            app,
            host=self._config.host,
            port=self._config.port,
            reload=self._config.reload,
            workers=self._config.workers,
            log_level=self._config.log_level,
        )

    async def run_async(self) -> None:
        """Start the server from an async context."""
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "RAI HTTP server requires 'uvicorn'. Install or upgrade RAI with:\n"
                "  pip install revolt-rai"
            ) from exc

        app = self._build_app()
        cfg = uvicorn.Config(
            app,
            host=self._config.host,
            port=self._config.port,
            reload=self._config.reload,
            workers=self._config.workers,
            log_level=self._config.log_level,
        )
        server = uvicorn.Server(cfg)
        await server.serve()


from rai.client import RAIClient, SSEEvent  # noqa: E402

__all__ = ["RAIHTTPServer", "HTTPConfig", "RAIClient", "SSEEvent"]
