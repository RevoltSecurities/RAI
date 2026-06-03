"""RequestInspectorMiddleware — debug middleware for inspecting LLM requests live.

Two modes:
  1. Log mode (default): logs full request details to ~/.rai/debug/requests.jsonl
  2. Proxy mode: routes all LLM requests through a local MITM proxy (Burp/mitmproxy)
     Set RAI_INSPECT_PROXY=http://127.0.0.1:8080

Activation:
  RAI_INSPECT=1                    — enable request logging
  RAI_INSPECT_PROXY=http://...     — also route through MITM proxy
  RAI_INSPECT_LOG_FILE=<path>      — override log path

Usage with mitmproxy:
  # Terminal 1: start mitmproxy
  mitmproxy --listen-port 8080 --ssl-insecure

  # Terminal 2: start RAI with proxy
  RAI_INSPECT=1 RAI_INSPECT_PROXY=http://127.0.0.1:8080 rai chat
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

_ENABLED = os.environ.get("RAI_INSPECT", "0") == "1"
_PROXY = os.environ.get("RAI_INSPECT_PROXY", "")
_LOG_FILE = os.environ.get(
    "RAI_INSPECT_LOG_FILE",
    str(Path.home() / ".rai" / "debug" / "requests.jsonl"),
)


def _msg_preview(msg: Any) -> dict:
    mtype = getattr(msg, "type", type(msg).__name__)
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else "" for b in content
        )
    tool_calls = [
        {"name": tc.get("name", ""), "args_len": len(json.dumps(tc.get("args", {})))}
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]
    cache = any(
        (b.get("cache_control") if isinstance(b, dict) else False)
        for b in (content if isinstance(getattr(msg, "content", None), list) else [])
    )
    entry: dict[str, Any] = {
        "type": mtype,
        "chars": len(str(content)),
        "preview": str(content)[:120],
        "cached": cache,
    }
    if tool_calls:
        entry["tool_calls"] = tool_calls
    return entry


def _inject_proxy(model: Any) -> Any:
    """Patch the LLM client to route through RAI_INSPECT_PROXY.

    ChatAnthropic: temporarily clears macOS system SOCKS proxy env vars
    (WARP/VPN sets socks5 via get_environment_proxies) then patches ._client /
    ._async_client with httpx clients that use the MITM proxy + verify=False.

    ChatLiteLLM: sets HTTPS_PROXY env var — LiteLLM reads it per call.
    """
    if not _PROXY:
        return model
    try:
        import httpx
        from langchain_anthropic import ChatAnthropic

        if isinstance(model, ChatAnthropic):
            from langchain_anthropic._client_utils import (
                _get_default_httpx_client, _get_default_async_httpx_client,
            )
            # Clear system proxy env vars temporarily — macOS WARP/VPN sets socks5
            # via get_environment_proxies() which httpx picks up, failing without socksio.
            _PROXY_KEYS = [
                "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy",
            ]
            saved = {k: os.environ.pop(k) for k in _PROXY_KEYS if k in os.environ}
            _get_default_httpx_client.cache_clear()
            _get_default_async_httpx_client.cache_clear()

            transport       = httpx.HTTPTransport(proxy=_PROXY, verify=False)
            async_transport = httpx.AsyncHTTPTransport(proxy=_PROXY, verify=False)
            sync_client  = httpx.Client(transport=transport, verify=False, trust_env=False)
            async_client = httpx.AsyncClient(transport=async_transport, verify=False, trust_env=False)

            anth_sync  = model._client
            anth_async = model._async_client
            if hasattr(anth_sync, "_client"):
                anth_sync._client = sync_client
            if hasattr(anth_async, "_client"):
                anth_async._client = async_client

            os.environ.update(saved)
            logger.warning("RequestInspectorMiddleware: patched ChatAnthropic → %s", _PROXY)
            return model

    except Exception as e:
        logger.warning("RequestInspectorMiddleware: ChatAnthropic patch failed: %s", e)
        if "saved" in dir():
            os.environ.update(saved)  # type: ignore[possibly-undefined]

    # ChatLiteLLM fallback — env var approach
    os.environ["HTTPS_PROXY"] = _PROXY
    os.environ["HTTP_PROXY"] = _PROXY
    os.environ["LITELLM_SSL_VERIFY"] = "false"
    os.environ["SSL_VERIFY"] = "false"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    logger.warning("RequestInspectorMiddleware: HTTPS_PROXY=%s SSL_VERIFY=false", _PROXY)
    return model


def _write(entry: dict) -> None:
    try:
        Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


class RequestInspectorMiddleware(AgentMiddleware):
    """Debug middleware — logs full LLM request details + optional MITM proxy routing.

    Activate with: RAI_INSPECT=1 [RAI_INSPECT_PROXY=http://127.0.0.1:8080]
    """

    def __init__(self) -> None:
        self._enabled = _ENABLED
        self._proxy_injected = False
        if self._enabled and _PROXY:
            logger.warning("RequestInspectorMiddleware: MITM proxy active → %s", _PROXY)
        elif self._enabled:
            logger.warning("RequestInspectorMiddleware: request logging active → %s", _LOG_FILE)

    def _build_entry(self, request: ModelRequest, elapsed_ms: float) -> dict:
        msgs = request.messages or []
        model_name = ""
        try:
            model_name = str(getattr(request.model, "model", "") or type(request.model).__name__)
        except Exception:
            pass

        has_cache = any(
            isinstance(getattr(m, "content", None), list) and
            any(isinstance(b, dict) and b.get("cache_control") for b in m.content)
            for m in msgs
        )
        by_type: dict[str, int] = {}
        for m in msgs:
            t = getattr(m, "type", "?")
            by_type[t] = by_type.get(t, 0) + 1

        total_chars = sum(
            len(str(getattr(m, "content", "") or "")) +
            sum(len(json.dumps(tc.get("args", {})))
                for tc in (getattr(m, "tool_calls", None) or []))
            for m in msgs
        )
        return {
            "ts": datetime.now().isoformat(),
            "model": model_name,
            "elapsed_ms": round(elapsed_ms, 1),
            "msg_count": len(msgs),
            "total_chars": total_chars,
            "estimated_tokens": int(total_chars / 2.5),
            "has_cache_control": has_cache,
            "by_type": by_type,
            "proxy": _PROXY or None,
            "messages": [_msg_preview(m) for m in msgs],
        }

    def _ensure_proxy(self, request: ModelRequest) -> ModelRequest:
        if _PROXY and not self._proxy_injected:
            patched = _inject_proxy(request.model)
            self._proxy_injected = True
            if patched is not request.model:
                return request.override(model=patched)
        return request

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> ModelResponse:
        if not self._enabled:
            return handler(request)
        request = self._ensure_proxy(request)
        t0 = time.monotonic()
        result = handler(request)
        elapsed = (time.monotonic() - t0) * 1000
        _write(self._build_entry(request, elapsed))
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not self._enabled:
            return await handler(request)
        request = self._ensure_proxy(request)
        t0 = time.monotonic()
        result = await handler(request)
        elapsed = (time.monotonic() - t0) * 1000
        _write(self._build_entry(request, elapsed))
        return result
