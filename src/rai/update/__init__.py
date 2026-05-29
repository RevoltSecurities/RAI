"""rai.update — PyPI version checking and self-upgrade logic."""

from rai.update.update import (
    detect_install_method,
    get_latest_version,
    is_update_available,
    perform_upgrade,
    upgrade_command,
    InstallMethod,
    RAI_PYPI_URL,
    RAI_GITHUB_URL,
)

__all__ = [
    "detect_install_method",
    "get_latest_version",
    "is_update_available",
    "perform_upgrade",
    "upgrade_command",
    "InstallMethod",
    "RAI_PYPI_URL",
    "RAI_GITHUB_URL",
]
