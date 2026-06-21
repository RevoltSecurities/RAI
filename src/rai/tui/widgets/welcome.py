"""WelcomeBanner — Metasploit-style startup banner with configurable branding."""

from __future__ import annotations

import os
import time
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from rai.tui.branding import BrandingConfig, RAI_BRANDING


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")

def _version() -> str:
    try:
        from importlib.metadata import version
        return version("revolt-rai")
    except Exception:
        return "0.1.0"


def _username() -> str:
    for key in ("USER", "USERNAME", "LOGNAME"):
        v = os.getenv(key, "")
        if v:
            return v.capitalize()
    return "there"


def _short_path(p: str, max_len: int = 42) -> str:
    home = os.path.expanduser("~")
    if p.startswith(home):
        p = "~" + p[len(home):]
    if len(p) > max_len:
        p = "…" + p[-(max_len - 1):]
    return p


def _age_label(ts: float | None) -> str:
    if ts is None:
        return "recent"
    diff = time.time() - ts
    if diff < 120:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


class WelcomeBanner(Widget):
    """Configurable welcome banner with branding support.

    Parameters
    ----------
    agent:    Agent name shown in info rows.
    server:   Server URL shown in info rows.
    branding: BrandingConfig instance for white-labeling. None = RAI default.
    custom_markup: Legacy — Rich markup that replaces the branding block.
                   Deprecated in favor of BrandingConfig. Still supported for
                   backward compatibility.
    """

    DEFAULT_CSS = """
    WelcomeBanner {
        height: auto;
        margin: 1 2 0 2;
        border: round $accent;
        padding: 1 2;
    }
    """

    def __init__(
        self,
        agent: str,
        server: str,
        branding: BrandingConfig | None = None,
        custom_markup: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._server = server
        self._branding = branding or RAI_BRANDING
        self._custom_markup = custom_markup
        self._ver = _version()
        self._user = _username()
        self._recent: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="wb-content")

    def on_mount(self) -> None:
        self._update_content()

    def _update_content(self) -> None:
        brand = self._branding
        ver = _escape(self._ver)
        user = _escape(self._user)
        agent = _escape(self._agent)
        server = _escape(self._server)
        cwd = _escape(_short_path(os.getcwd()))

        lines: list[str] = []

        if self._custom_markup:
            # Legacy: render user-supplied Rich markup verbatim.
            lines.append(self._custom_markup)
            lines.append("")
        else:
            # Branding block
            lines.append(brand.icon)
            subtitle = f"  {_escape(brand.subtitle)}" if brand.subtitle else ""
            lines.append(f"[dim]v{ver}[/dim]{subtitle}")
            lines.append("")

            # Info rows
            title = _escape(brand.title)
            lines.append(f"[dim]+ -- --=[/dim]  [bold $accent]{title}[/bold $accent]  [dim]── ──+[/dim]")
            lines.append(f"[dim]+ -- --=[/dim]  [dim]agent  :[/dim]  [cyan]{agent}[/cyan]")
            lines.append(f"[dim]+ -- --=[/dim]  [dim]server :[/dim]  [dim]{server}[/dim]")
            lines.append(f"[dim]+ -- --=[/dim]  [dim]cwd    :[/dim]  [dim]{cwd}[/dim]")

            # Custom extra rows
            for label, value in brand.info_rows:
                lbl = _escape(label)
                val = _escape(value)
                lines.append(f"[dim]+ -- --=[/dim]  [dim]{lbl:<7}:[/dim]  [dim]{val}[/dim]")

            lines.append(f"[dim]+ -- --=[/dim]  [bold]Welcome back {user}![/bold]")

        # Recent activity
        lines.append("")
        if self._recent:
            lines.append("[bold $warning]Recent threads[/bold $warning]")
            for t in self._recent[:5]:
                prompt = t.get("initial_prompt") or t.get("thread_id", "")[:14] or "…"
                if len(prompt) > 50:
                    prompt = prompt[:50] + "…"
                age = _age_label(t.get("_ts"))
                lines.append(f"  [dim]{age:>8}[/dim]  {_escape(prompt)}")
            lines.append("  [dim]/threads for more[/dim]")
        else:
            lines.append("[dim]No recent activity — start a conversation![/dim]")

        # Tips footer
        lines.append("")
        lines.append(brand.tips)

        try:
            self.query_one("#wb-content", Static).update("\n".join(lines))
        except Exception:
            pass

    def set_recent_threads(self, threads: list[dict]) -> None:
        self._recent = threads
        self._update_content()
