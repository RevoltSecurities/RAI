"""rai.sdk.tui — RAI Terminal UI (HTTP-connected Textual app).

    from rai.sdk.tui import RaiHttpTUI, BrandingConfig
    RaiHttpTUI(base_url="http://127.0.0.1:8000", agent="rai").run()

Custom branding::

    from rai.sdk.tui import RaiHttpTUI, BrandingConfig
    brand = BrandingConfig(
        icon="[bold magenta]🛡  ACME Security[/bold magenta]",
        title="ACME Scanner",
        subtitle="Powered by RAI",
        tips="[dim]/scan[/dim] start  ·  [dim]/report[/dim] export",
        info_rows=[("target", "example.com")],
    )
    RaiHttpTUI(agent="scanner", branding=brand).run()
"""

from rai.tui import RaiHttpTUI, RaiHttpTUIApp
from rai.tui.branding import BrandingConfig, RAI_BRANDING

__all__ = ["RaiHttpTUI", "RaiHttpTUIApp", "BrandingConfig", "RAI_BRANDING"]
