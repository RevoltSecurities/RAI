"""ClientConfig — all tunables for the RAI HTTP client in one dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClientConfig:
    base_url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    timeout: float = 30.0
    connect_timeout: float = 10.0
    ssl_verify: bool = False
    max_retries: int = 3
    retry_backoff: float = 0.5
    retry_on: tuple[int, ...] = (429, 500, 502, 503, 504)
    allowed_tools: list[str] | None = None
    default_model: str | None = None
    default_agent: str = "rai"
    sse_reconnect: bool = True
    sse_max_reconnects: int = 5
