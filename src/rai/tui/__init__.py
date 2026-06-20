"""rai.tui — standalone HTTP-connected TUI for the RAI harness.

Launch via CLI:
    rai http serve --agent rai --port 8001 --hitl --tui

Or standalone against a running server:
    from rai.sdk.tui import RaiHttpTUI
    RaiHttpTUI(base_url="http://127.0.0.1:8000", agent="rai").run()

Custom branding — white-label with your own icon, title, tips, and info rows:
    from rai.sdk.tui import RaiHttpTUI, BrandingConfig

    brand = BrandingConfig(
        icon="[bold magenta]🛡  ACME[/bold magenta]",
        title="ACME Security",
        subtitle="Powered by RAI",
        tips="[dim]/scan[/dim] start  ·  [dim]/report[/dim] export",
        info_rows=[("target", "example.com"), ("scope", "*.example.com")],
    )
    RaiHttpTUI(agent="scanner", branding=brand).run()

Suppress banner entirely:
    brand = BrandingConfig(show_banner=False)
    RaiHttpTUI(agent="rai", branding=brand).run()
"""

from __future__ import annotations

from rai.tui.app import RaiHttpTUIApp
from rai.tui.branding import BrandingConfig, RAI_BRANDING


class RaiHttpTUI:
    """Convenience wrapper to launch the TUI.

    Parameters
    ----------
    base_url:       HTTP server base URL (default: http://127.0.0.1:8000)
    agent:          Agent name to connect to
    api_key:        Server API key (X-API-Key header)
    thread_id:      Resume an existing thread
    branding:       BrandingConfig instance for white-labeling. Overrides banner=.
    banner:         Legacy — controls the welcome banner (deprecated, use branding=):
                    * ``None`` (default) — show the built-in RAI banner.
                    * ``""`` (empty string) — suppress the banner entirely.
                    * Any non-empty string — render that Rich markup as banner.
    default_theme:  Textual theme name (default: ``"github-dark"``)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        agent: str = "rai",
        api_key: str = "",
        thread_id: str | None = None,
        branding: BrandingConfig | None = None,
        banner: str | None = None,
        default_theme: str = "github-dark",
    ) -> None:
        # branding= takes precedence over legacy banner=
        if branding is not None:
            if not branding.show_banner:
                banner = ""
            else:
                banner = None  # WelcomeBanner will use branding directly
        self._branding = branding
        self._app = RaiHttpTUIApp(
            base_url=base_url,
            agent=agent,
            api_key=api_key,
            thread_id=thread_id,
            banner=banner,
            default_theme=default_theme,
            branding=branding,
        )

    def run(self) -> None:
        self._app.run()


__all__ = ["RaiHttpTUI", "RaiHttpTUIApp", "BrandingConfig", "RAI_BRANDING"]
