"""rai.tui — standalone HTTP-connected TUI for the RAI harness.

Launch via CLI:
    rai http serve --agent rai --port 8001 --hitl --tui

Or standalone against a running server:
    from rai.tui import RaiHttpTUI
    RaiHttpTUI(base_url="http://127.0.0.1:8000", agent="rai").run()
"""

from __future__ import annotations

from rai.tui.app import RaiHttpTUIApp


class RaiHttpTUI:
    """Convenience wrapper to launch the TUI.

    Parameters
    ----------
    base_url:       HTTP server base URL (default: http://127.0.0.1:8000)
    agent:          Agent name to connect to
    api_key:        Server API key (X-API-Key header)
    thread_id:      Resume an existing thread
    banner:         Controls the welcome banner shown at startup:

                    * ``None`` (default) — show the built-in RAI ASCII art banner.
                    * ``""`` (empty string) — suppress the banner entirely.
                    * Any non-empty string — render that Rich markup as the banner
                      branding block.  Recent-threads and keyboard-tips sections
                      are still shown below the custom markup. Example::

                          RaiHttpTUI(
                              base_url="http://127.0.0.1:8000",
                              agent="mybot",
                              banner=(
                                  "[bold magenta]🛡  ACME Security Scanner[/bold magenta]\\n"
                                  "[dim]Powered by RAI[/dim]"
                              ),
                          ).run()

    default_theme:  Textual theme name (default: ``"github-dark"``)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        agent: str = "rai",
        api_key: str = "",
        thread_id: str | None = None,
        banner: str | None = None,
        default_theme: str = "github-dark",
    ) -> None:
        self._app = RaiHttpTUIApp(
            base_url=base_url,
            agent=agent,
            api_key=api_key,
            thread_id=thread_id,
            banner=banner,
            default_theme=default_theme,
        )

    def run(self) -> None:
        self._app.run()


__all__ = ["RaiHttpTUI", "RaiHttpTUIApp"]
