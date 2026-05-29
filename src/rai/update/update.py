"""RAI update checking — checks PyPI for rai, not deepagents-cli."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from typing import Literal

from packaging.version import InvalidVersion, Version

from rai import __version__

logger = logging.getLogger(__name__)

RAI_PYPI_URL = "https://pypi.org/pypi/rai/json"
RAI_GITHUB_URL = "https://github.com/RevoltSecurities/RAI"
_UPGRADE_TIMEOUT = 120

InstallMethod = Literal["uv", "pip", "brew", "unknown"]

_UPGRADE_COMMANDS: dict[InstallMethod, str] = {
    "uv":   "uv tool upgrade rai",
    "brew": "brew upgrade rai",
    "pip":  "pip install --upgrade rai",
}


def detect_install_method() -> InstallMethod:
    """Detect how rai was installed — uv tool, brew, pip, or editable."""
    prefix = sys.prefix
    if "/uv/tools/" in prefix or "\\uv\\tools\\" in prefix:
        return "uv"
    if any(prefix.startswith(p) for p in ("/opt/homebrew", "/usr/local/Cellar", "/home/linuxbrew")):
        return "brew"
    try:
        from deepagents_cli.config import _is_editable_install
        if _is_editable_install():
            return "unknown"
    except Exception:
        pass
    return "pip"


def get_latest_version(*, bypass_cache: bool = False) -> str | None:
    """Fetch latest rai version from PyPI (24-hour cache)."""
    from pathlib import Path
    from deepagents_cli.model_config import DEFAULT_CONFIG_DIR

    cache_file = DEFAULT_CONFIG_DIR / "rai_latest_version.json"

    if not bypass_cache and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - data.get("checked_at", 0) < 86_400 and "version" in data:
                return data["version"]
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    try:
        import requests
        resp = requests.get(RAI_PYPI_URL, headers={"User-Agent": f"rai/{__version__} update-check"}, timeout=3)
        resp.raise_for_status()
        latest: str = resp.json()["info"]["version"]
    except Exception:
        logger.debug("Failed to fetch rai version from PyPI", exc_info=True)
        return None

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"version": latest, "checked_at": time.time()}), encoding="utf-8")
    except OSError:
        pass

    return latest


def is_update_available(*, bypass_cache: bool = False) -> tuple[bool, str | None]:
    """Return (available, latest_version) for rai."""
    try:
        installed = Version(__version__)
    except InvalidVersion:
        return False, None

    latest = get_latest_version(bypass_cache=bypass_cache)
    if latest is None:
        return False, None

    try:
        return Version(latest) > installed, latest
    except InvalidVersion:
        return False, None


def upgrade_command() -> str:
    """Return the shell command to upgrade rai."""
    method = detect_install_method()
    return _UPGRADE_COMMANDS.get(method, _UPGRADE_COMMANDS["pip"])


async def perform_upgrade() -> tuple[bool, str]:
    """Upgrade rai using the detected install method."""
    method = detect_install_method()
    if method == "unknown":
        return False, "Editable install — skipping auto-update."

    cmd = _UPGRADE_COMMANDS.get(method)
    if cmd is None:
        return False, f"No upgrade command for method: {method}"

    if method == "brew" and not shutil.which("brew"):
        return False, "brew not found on PATH."

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_UPGRADE_TIMEOUT)
        output = (stdout or b"").decode() + (stderr or b"").decode()
        if proc.returncode == 0:
            return True, output.strip()
        return False, output.strip()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"Upgrade timed out after {_UPGRADE_TIMEOUT}s"
    except OSError:
        return False, f"Failed to execute: {cmd}"
