"""rai.sdk.tui — RAI Terminal UI (HTTP-connected Textual app).

    from rai.sdk.tui import RaiHttpTUI
    RaiHttpTUI(base_url="http://127.0.0.1:8000", agent="rai").run()
"""

from rai.tui import RaiHttpTUI, RaiHttpTUIApp

__all__ = ["RaiHttpTUI", "RaiHttpTUIApp"]
