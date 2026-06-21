"""Docker Router Middleware — routes bash tool calls to a Kali Linux Docker container.

When RAI_DOCKER_ENV=1, this middleware intercepts every `bash` and `execute` tool call
and runs the command inside a persistent Docker container via `docker exec`. The container
is shared across all agent threads and managed by the HTTP harness lifespan.

Container lifecycle:
    - ensure_container_global() — called by harness lifespan on startup
    - stop_container_global() — called by harness lifespan on shutdown

Volume mapping uses platformdirs for cross-platform cache directory:
    - Linux:   ~/.cache/rai/docker/workspace → /workspace
    - macOS:   ~/Library/Caches/rai/docker/workspace → /workspace
    - Windows: %LOCALAPPDATA%/rai/Cache/docker/workspace → /workspace
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from platformdirs import user_cache_dir

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_IMAGE = ""
_DEFAULT_CONTAINER = "rai-kali"
_DEFAULT_MEMORY = "4g"
_DEFAULT_PIDS_LIMIT = "4096"
_MAX_TIMEOUT = 3600
_TIMEOUT_EXIT_CODE = 124
_MAX_INLINE_CHARS = 5_000
_SPILL_PREVIEW_CHARS = 500

_CAPABILITIES = [
    "NET_RAW",
    "NET_ADMIN",
    "NET_BIND_SERVICE",
    "SYS_PTRACE",
    "SETUID",
    "SETGID",
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "KILL",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Docker sudo detection — on Linux (Ubuntu, Debian, etc.) Docker often requires
# sudo unless the user is in the `docker` group. We auto-detect on first call
# and cache the result. Override with RAI_DOCKER_SUDO=1 to force sudo.
_docker_prefix: list[str] | None = None  # cached after first probe


def _probe_docker_prefix() -> list[str]:
    """Detect whether docker commands need sudo. Cached after first call."""
    global _docker_prefix
    if _docker_prefix is not None:
        return _docker_prefix

    # Explicit override
    if os.environ.get("RAI_DOCKER_SUDO", "0") == "1":
        _docker_prefix = ["sudo", "docker"]
        return _docker_prefix

    # Try without sudo first
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _docker_prefix = ["docker"]
            return _docker_prefix
        # Permission denied pattern
        if "permission denied" in result.stderr.lower() or "connect:" in result.stderr.lower():
            logger.info("Docker requires sudo — auto-enabling (set RAI_DOCKER_SUDO=0 to override)")
            _docker_prefix = ["sudo", "docker"]
            return _docker_prefix
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Default to plain docker (will fail with clear error if not accessible)
    _docker_prefix = ["docker"]
    return _docker_prefix


def _docker_cmd(*args: str) -> list[str]:
    """Build a docker command with the correct prefix (docker or sudo docker)."""
    return [*_probe_docker_prefix(), *args]


def _docker_cache_dir() -> Path:
    """Cross-platform cache directory for Docker workspace/output."""
    return Path(user_cache_dir("rai")) / "docker"


def _workspace_dir() -> Path:
    d = _docker_cache_dir() / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _output_dir() -> Path:
    d = _docker_cache_dir() / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_container_running(name: str) -> bool:
    """Check if a container with the given name is running."""
    try:
        result = subprocess.run(
            _docker_cmd("inspect", "-f", "{{.State.Running}}", name),
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, OSError):
        return False


def _container_exists(name: str) -> bool:
    """Check if a container with the given name exists (running or stopped)."""
    try:
        result = subprocess.run(
            _docker_cmd("inspect", name),
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# DockerRouterMiddleware
# ---------------------------------------------------------------------------


class DockerRouterMiddleware(AgentMiddleware):
    """Route bash/execute tool calls to a persistent Kali Linux Docker container.

    The container is started once (idempotent) and shared across all threads.
    Commands are executed via `docker exec` with credential-stripped env passthrough.

    Fallback: if the container fails to start or Docker is unavailable, all tool
    calls pass through to the next handler (normal host BashTool). This ensures
    the agent never gets stuck — it degrades gracefully to local execution.
    """

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _container_started: ClassVar[bool] = False
    _image: ClassVar[str] = _DEFAULT_IMAGE
    _container_name: ClassVar[str] = _DEFAULT_CONTAINER

    def __init__(
        self,
        image: str | None = None,
        container_name: str | None = None,
    ) -> None:
        cls = type(self)
        if image:
            cls._image = image
        if container_name:
            cls._container_name = container_name

    # ------------------------------------------------------------------
    # Container lifecycle (class-level — shared across all instances)
    # ------------------------------------------------------------------

    @classmethod
    def ensure_container_global(cls) -> None:
        """Start the Docker container if not already running. Idempotent."""
        image = os.environ.get("RAI_DOCKER_IMAGE", cls._image)
        if not image:
            logger.info("No RAI_DOCKER_IMAGE configured — skipping Docker routing")
            return

        if not shutil.which("docker"):
            logger.warning(
                "docker binary not found in PATH — falling back to host bash execution"
            )
            return

        with cls._lock:
            if cls._container_started:
                return

            name = os.environ.get("RAI_DOCKER_CONTAINER", cls._container_name)
            memory = os.environ.get("RAI_DOCKER_MEMORY", _DEFAULT_MEMORY)
            network = os.environ.get("RAI_DOCKER_NETWORK", "")

            if _is_container_running(name):
                logger.info("Docker container '%s' already running", name)
                cls._container_name = name
                cls._container_started = True
                return

            # Remove stale stopped container
            if _container_exists(name):
                subprocess.run(
                    _docker_cmd("rm", "-f", name),
                    capture_output=True, timeout=15,
                )

            workspace = _workspace_dir()
            output = _output_dir()

            cmd = [
                *_docker_cmd("run", "-d"),
                "--name", name,
                "--init",
                "--cap-drop", "ALL",
            ]
            for cap in _CAPABILITIES:
                cmd.extend(["--cap-add", cap])
            cmd.extend([
                "--security-opt", "no-new-privileges:true",
                "--memory", memory,
                "--pids-limit", _DEFAULT_PIDS_LIMIT,
                "-v", f"{workspace}:/workspace",
                "-v", f"{output}:/tmp/rai_output",
                "-w", "/workspace",
            ])
            if network:
                cmd.extend(["--network", network])
            cmd.extend([image, "tail", "-f", "/dev/null"])

            logger.info("Starting Docker container: %s (image=%s)", name, image)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.warning(
                    "Failed to start Docker container '%s': %s — falling back to host bash",
                    name, result.stderr.strip(),
                )
                return

            cls._container_name = name
            cls._container_started = True
            logger.info("Docker container '%s' started successfully", name)

            # Run optional setup command (e.g., apt install tools on base image)
            setup_cmd = os.environ.get("RAI_DOCKER_SETUP_CMD", "")
            if setup_cmd:
                logger.info("Running Docker setup command: %s", setup_cmd[:80])
                setup_result = subprocess.run(
                    [*_docker_cmd("exec", name), "sh", "-c", setup_cmd],
                    capture_output=True, text=True, timeout=300,
                )
                if setup_result.returncode != 0:
                    logger.warning(
                        "Docker setup command failed (exit %d): %s",
                        setup_result.returncode, setup_result.stderr[:200],
                    )

    @classmethod
    def stop_container_global(cls) -> None:
        """Stop and remove the Docker container. Called on harness shutdown."""
        with cls._lock:
            if not cls._container_started:
                return

            name = cls._container_name
            logger.info("Stopping Docker container '%s'", name)
            try:
                subprocess.run(
                    _docker_cmd("rm", "-f", name),
                    capture_output=True, timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.warning("Error stopping container '%s': %s", name, e)
            finally:
                cls._container_started = False

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _exec_in_container(
        self,
        command: str,
        timeout: int = 600,
        working_dir: str = "",
    ) -> str:
        """Execute a command in the Docker container via docker exec.

        Returns None if the container isn't available (caller should fallback).
        """
        if not command or not command.strip():
            return "[exit 1]\nError: command must be a non-empty string."

        if not self._container_started:
            return ""  # empty signals caller to fallback

        name = self._container_name
        effective_timeout = min(max(1, timeout), _MAX_TIMEOUT)
        cwd = working_dir.strip() or "/workspace"

        cmd = [*_docker_cmd("exec", "-w", cwd, name), "sh", "-c", command]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return (
                f"[exit {_TIMEOUT_EXIT_CODE}]\n"
                f"Command timed out after {effective_timeout}s. "
                "Increase the timeout parameter for long-running scans."
            )
        except Exception as exc:
            return f"[exit 1]\nError executing in Docker container: {exc}"

        parts: list[str] = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            stderr_lines = proc.stderr.rstrip("\n").split("\n")
            parts.extend(f"[stderr] {line}" for line in stderr_lines)

        output = "\n".join(parts) if parts else "<no output>"

        # Spill large output to shared /tmp/rai_output
        if len(output) > _MAX_INLINE_CHARS:
            output_bytes = output.encode("utf-8", errors="replace")
            h = hashlib.md5(output_bytes, usedforsecurity=False).hexdigest()[:8]
            spill_path = _output_dir() / f"rai_out_{h}.txt"
            try:
                spill_path.write_bytes(output_bytes)
                preview = output[:_SPILL_PREVIEW_CHARS]
                exit_info = f"\n\nExit code: {proc.returncode}" if proc.returncode != 0 else ""
                return (
                    f"Result too large ({len(output):,} chars). "
                    f"Read from: /tmp/rai_output/{spill_path.name}\n\n"
                    f"Preview (first {_SPILL_PREVIEW_CHARS} chars):\n{preview}{exit_info}"
                )
            except OSError:
                output = output[:_MAX_INLINE_CHARS] + f"\n... (truncated at {_MAX_INLINE_CHARS:,} chars)"

        if proc.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {proc.returncode}"

        return output

    async def _aexec_in_container(
        self,
        command: str,
        timeout: int = 600,
        working_dir: str = "",
    ) -> str:
        return await asyncio.to_thread(self._exec_in_container, command, timeout, working_dir)

    # ------------------------------------------------------------------
    # Middleware interface
    # ------------------------------------------------------------------

    def wrap_tool_call(
        self,
        request: "ToolCallRequest",
        handler: "Callable[[ToolCallRequest], ToolMessage | Command[Any]]",
    ) -> "ToolMessage | Command[Any]":
        tool_name = request.tool_call.get("name", "")
        if tool_name not in ("bash", "execute"):
            return handler(request)

        # Fallback: if container isn't running, pass through to host BashTool
        if not self._container_started:
            logger.debug("Docker container unavailable — falling back to host bash")
            return handler(request)

        args = request.tool_call.get("args") or {}
        command = args.get("command", "")
        timeout = int(args.get("timeout") or 600)
        working_dir = args.get("working_dir", "")

        result = self._exec_in_container(command, timeout, working_dir)
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])

    async def awrap_tool_call(
        self,
        request: "ToolCallRequest",
        handler: "Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]",
    ) -> "ToolMessage | Command[Any]":
        tool_name = request.tool_call.get("name", "")
        if tool_name not in ("bash", "execute"):
            return await handler(request)

        # Fallback: if container isn't running, pass through to host BashTool
        if not self._container_started:
            logger.debug("Docker container unavailable — falling back to host bash")
            return await handler(request)

        args = request.tool_call.get("args") or {}
        command = args.get("command", "")
        timeout = int(args.get("timeout") or 600)
        working_dir = args.get("working_dir", "")

        result = await self._aexec_in_container(command, timeout, working_dir)
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])
