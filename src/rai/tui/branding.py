"""BrandingConfig — white-label configuration for RAI TUIs."""

from __future__ import annotations

from dataclasses import dataclass, field


_RAI_ICON = (
    "[bold $accent]    ▲   ▲[/bold $accent]\n"
    "[bold $accent]  ╭───────╮[/bold $accent]\n"
    "[bold $accent]▝▜│ ██ ██ │▛▘  RAI * Your CyberSecurity AI Assistant[/bold $accent]\n"
    "[bold $accent]  ╰───────╯[/bold $accent]\n"
    "[bold $accent]    ▘▘ ▝▝  [/bold $accent]\n"
)


@dataclass(frozen=True)
class BrandingConfig:
    """White-label branding for RAI TUIs.

    Pass an instance to ``RaiHttpTUI(branding=...)`` or ``ClaudeTUIApp(branding=...)``
    to customize the look without touching RAI internals.

    Examples
    --------
    Default RAI branding (no changes needed)::

        from rai.sdk.tui import RaiHttpTUI
        RaiHttpTUI(agent="rai").run()

    Custom branding (all fields)::

        from rai.sdk.tui import RaiHttpTUI, BrandingConfig

        brand = BrandingConfig(
            icon="[bold magenta]🛡  ACME Security[/bold magenta]",
            title="ACME Scanner",
            subtitle="Powered by RAI v2",
            tips="[dim]/scan[/dim] start  ·  [dim]/report[/dim] export",
            info_rows=[("target", "example.com"), ("scope", "*.example.com")],
        )
        RaiHttpTUI(agent="scanner", branding=brand).run()

    Minimal — just override title and tips::

        brand = BrandingConfig(title="My Tool", tips="[dim]/help[/dim] for commands")
        RaiHttpTUI(agent="rai", branding=brand).run()

    Multi-line ASCII icon::

        brand = BrandingConfig(
            icon=(
                "[bold cyan]╔═══════════════════╗[/bold cyan]\\n"
                "[bold cyan]║  ⚔  War Room  ⚔  ║[/bold cyan]\\n"
                "[bold cyan]╚═══════════════════╝[/bold cyan]"
            ),
            title="War Room",
        )

    Suppress banner entirely::

        brand = BrandingConfig(show_banner=False)
        RaiHttpTUI(agent="rai", branding=brand).run()

    With ClaudeTUIApp::

        from rai.tui.claude_tui import ClaudeTUIApp
        from rai.tui.branding import BrandingConfig

        brand = BrandingConfig(
            icon="[bold red]🔴  RedTeam Console[/bold red]",
            title="RedTeam",
            tips="[dim]/agents[/dim] switch  ·  [dim]/history[/dim] browse",
        )
        app = ClaudeTUIApp(base_url="http://localhost:8001", branding=brand)
        app.run()
    """

    icon: str = _RAI_ICON
    """Rich markup for the banner icon/logo block. Multi-line supported."""

    title: str = "Revolt AI"
    """Title shown in the info rows (e.g. company/product name)."""

    subtitle: str = ""
    """Optional subtitle below the version line (e.g. 'Powered by RAI')."""

    tips: str = (
        "[dim]/new[/dim] new thread  "
        "[dim]·[/dim]  [dim]/threads[/dim] browse  "
        "[dim]·[/dim]  [dim]ctrl+b[/dim] bg runs  "
        "[dim]·[/dim]  [dim]ctrl+t[/dim] theme"
    )
    """Rich markup for the keyboard tips footer line."""

    show_banner: bool = True
    """Set to False to suppress the welcome banner entirely."""

    info_rows: list[tuple[str, str]] = field(default_factory=list)
    """Extra info rows as (label, value) tuples shown below the standard rows.

    Example: ``[("target", "example.com"), ("scope", "*.example.com")]``
    """


# Default singleton — used when no custom branding is provided.
RAI_BRANDING = BrandingConfig()
