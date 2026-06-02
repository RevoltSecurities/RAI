"""RequestInspectorMiddleware — debug middleware for inspecting LLM requests live.

FOR TESTING ONLY — revert before production release.

Two modes:
  1. Log mode (default): logs full request details to ~/.rai/debug/requests.jsonl
     Shows: model, messages, token counts, tool names, cache_control presence
  2. Proxy mode: routes all LLM requests through a local MITM proxy (e.g. mitmproxy)
     Set RAI_INSPECT_PROXY=http://127.0.0.1:8080 to capture in mitmproxy/Burp/Charles

Activation:
  RAI_INSPECT=1                    — enable request logging
  RAI_INSPECT_PROXY=http://...     — also route through MITM proxy
  RAI_INSPECT_LOG_FILE=<path>      — override log path

Usage with mitmproxy:
  # Terminal 1: start mitmproxy
  mitmproxy --listen-port 8080 --ssl-insecure

  # Terminal 2: start RAI with proxy
  RAI_INSPECT=1 RAI_INSPECT_PROXY=http://127.0.0.1:8080 rai chat

  # mitmproxy will show every request to your LiteLLM proxy / Anthropic API
  # including full message payloads, headers, response times
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
    """Compact message representation for logging."""
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
    entry = {
        "type": mtype,
        "chars": len(str(content)),
        "preview": str(content)[:120],
        "cached": cache,
    }
    if tool_calls:
        entry["tool_calls"] = tool_calls
    return entry


def _inject_proxy(model: Any) -> Any:
    """Patch the LLM client to route through RAI_INSPECT_PROXY (e.g. Burp/mitmproxy).

    Burp Suite acts as a MITM and presents its own certificate — SSL verification
    must be disabled or Python will refuse the connection.
    """
    if not _PROXY:
        return model
    try:
        import httpx

        # verify=False: accept Burp/mitmproxy self-signed cert
        proxy_transport = httpx.HTTPTransport(proxy=_PROXY, verify=False)
        async_proxy_transport = httpx.AsyncHTTPTransport(proxy=_PROXY, verify=False)
        proxy_client = httpx.Client(transport=proxy_transport, verify=False)
        async_proxy_client = httpx.AsyncClient(transport=async_proxy_transport, verify=False)

        # ChatAnthropic: patch its internal httpx clients directly
        _anthropic_client = getattr(model, "client", None)
        if _anthropic_client is not None:
            if hasattr(_anthropic_client, "_client"):
                _anthropic_client._client = proxy_client
            if hasattr(model, "async_client") and hasattr(model.async_client, "_client"):
                model.async_client._client = async_proxy_client
            logger.warning("RequestInspectorMiddleware: patched ChatAnthropic → %s", _PROXY)

        # ChatLiteLLM: set env vars — LiteLLM reads HTTPS_PROXY + SSL_VERIFY on each call
        os.environ["HTTPS_PROXY"] = _PROXY
        os.environ["HTTP_PROXY"] = _PROXY
        # Disable SSL verification for Burp/mitmproxy MITM certificate
        os.environ["LITELLM_SSL_VERIFY"] = "false"
        os.environ["SSL_VERIFY"] = "false"
        os.environ["CURL_CA_BUNDLE"] = ""        # disables curl SSL verify
        os.environ["REQUESTS_CA_BUNDLE"] = ""    # disables requests SSL verify
        logger.warning(
            "RequestInspectorMiddleware: HTTPS_PROXY=%s SSL_VERIFY=false", _PROXY
        )
    except Exception as e:
        logger.warning("RequestInspectorMiddleware: proxy inject failed: %s", e)
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

    FOR TESTING ONLY. Remove from middleware stack before production release.
    Activate with: RAI_INSPECT=1 [RAI_INSPECT_PROXY=http://127.0.0.1:8080]
    """

    def __init__(self) -> None:
        self._enabled = _ENABLED
        self._proxy_injected = False
        if self._enabled and _PROXY:
            logger.warning(
                "RequestInspectorMiddleware: MITM proxy active → %s", _PROXY
            )
        elif self._enabled:
            logger.warning(
                "RequestInspectorMiddleware: request logging active → %s", _LOG_FILE
            )

    def _build_entry(self, request: ModelRequest, elapsed_ms: float) -> dict:
        msgs = request.messages or []
        model_name = ""
        try:
            model_name = str(getattr(request.model, "model", "") or type(request.model).__name__)
        except Exception:
            pass

        # Cache control presence
        has_cache = any(
            isinstance(getattr(m, "content", None), list) and
            any(isinstance(b, dict) and b.get("cache_control") for b in m.content)
            for m in msgs
        )

        # Count by type
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

    def _ensure_proxy(self, request: ModelRequest) -> None:
        """Inject proxy into model client once."""
        if _PROXY and not self._proxy_injected:
            _inject_proxy(request.model)
            self._proxy_injected = True

    def wrap_model_call(self, request: ModelRequest, handler: Callable) -> ModelResponse:
        if not self._enabled:
            return handler(request)
        self._ensure_proxy(request)
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
        self._ensure_proxy(request)
        t0 = time.monotonic()
        result = await handler(request)
        elapsed = (time.monotonic() - t0) * 1000
        _write(self._build_entry(request, elapsed))
        return result
