from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClaudeServeConfig:
    base_url: str = "http://127.0.0.1:8001"
    api_key: str = ""
    timeout: float = 30.0
    connect_timeout: float = 10.0
    ssl_verify: bool = False
    max_retries: int = 3
    retry_backoff: float = 0.5
    retry_on: tuple[int, ...] = field(default_factory=lambda: (429, 500, 502, 503, 504))
