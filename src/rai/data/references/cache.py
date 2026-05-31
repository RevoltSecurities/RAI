"""Reference repo cache helpers.

Provides the canonical path root for offline reference git clones:
    ~/.rai/references/<slug>

The ``rai refs install`` command clones repos into these paths.
"""

from __future__ import annotations

from pathlib import Path

_REFS_DIR = Path.home() / ".rai" / "references"

REFERENCE_REPOS: dict[str, str] = {
    "all-about-bug-bounty": "https://github.com/daffainfo/AllAboutBugBounty.git",
    "redteam-tools": "https://github.com/A-poc/RedTeam-Tools.git",
    "book-of-secret-knowledge": "https://github.com/trimstray/the-book-of-secret-knowledge.git",
    "hackerone-reports": "https://github.com/reddelexc/hackerone-reports.git",
    "trickest-cve": "https://github.com/trickest/cve.git",
    "penetration-testing-poc": "https://github.com/Mr-xn/Penetration_Testing_POC.git",
}


def cache_path(slug: str, *, root: Path | None = None) -> Path:
    """Return the local cache dir for a reference repo slug."""
    base = root if root is not None else _REFS_DIR
    return base / slug


def is_cached(slug: str, *, root: Path | None = None) -> bool:
    p = cache_path(slug, root=root)
    return (p / ".git").exists()
