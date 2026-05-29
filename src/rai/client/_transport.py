"""AsyncTransport — httpx client with retry loop, SSL=False, api_key header."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from rai.client._config import ClientConfig


class AsyncTransport:
    def __init__(self, cfg: ClientConfig) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            verify=cfg.ssl_verify,
            timeout=httpx.Timeout(cfg.timeout, connect=cfg.connect_timeout),
            headers=self._build_headers(),
        )

    def _build_headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            h["X-API-Key"] = self._cfg.api_key
        return h

    def _sse_headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._cfg.api_key:
            h["X-API-Key"] = self._cfg.api_key
        return h

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            try:
                r = await self._client.request(method, path, **kw)
                if r.status_code in self._cfg.retry_on and attempt < self._cfg.max_retries:
                    await asyncio.sleep(self._cfg.retry_backoff * (2 ** attempt))
                    continue
                r.raise_for_status()
                return r
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt < self._cfg.max_retries:
                    await asyncio.sleep(self._cfg.retry_backoff * (2 ** attempt))
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = await self._request("GET", path, params=params or {})
        return r.json()

    async def post(self, path: str, body: Any = None) -> Any:
        kw: dict[str, Any] = {}
        if body is not None:
            if hasattr(body, "model_dump"):
                kw["json"] = body.model_dump(exclude_none=True)
            elif isinstance(body, dict):
                kw["json"] = body
            else:
                kw["json"] = body
        r = await self._request("POST", path, **kw)
        return r.json()

    async def delete(self, path: str) -> Any:
        r = await self._request("DELETE", path)
        return r.json()

    def make_sse_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._cfg.base_url.rstrip("/"),
            verify=self._cfg.ssl_verify,
            timeout=None,
            headers=self._sse_headers(),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
