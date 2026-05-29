"""RAI tools package — all agent tools, organized by domain."""

from rai.tools.core.bash import BashTool, get_builtin_tools
from rai.tools.core.findings import (
    _get_findings,
    _add_finding,
    _clear_findings,
    FindingsAddTool,
    FindingsListTool,
    FindingsExportTool,
    init_findings_store,
)
from rai.tools.core.memory import (
    MemoryReadTool,
    MemoryWriteTool,
    MemoryUpdateTool,
    MemoryFilesListTool,
    MemoryPathTool,
    get_memory_tools,
    _GLOBAL_FILES,
    _AGENT_FILES,
    _TARGET_FILES,
)
from rai.tools.core.opplan import (
    OPPLANInitTool,
    OPPLANAddObjectiveTool,
    OPPLANGetObjectiveTool,
    OPPLANListObjectivesTool,
    OPPLANUpdateObjectiveTool,
    OPPLANExpandObjectiveTool,
    OPPLANCollapseObjectiveTool,
    OPPLANSaveTool,
    OPPLANLoadTool,
    get_opplan_tools,
)
from rai.tools.core.references import get_reference_tools
from rai.tools.security.security import (
    HttpRequestTool,
    NucleiScanTool,
    NmapScanTool,
    WebSearchTool,
    WebFetchTool,
    CreateSubagentTool,
    get_security_tools,
)
from rai.tools.web.web import get_web_tools
from rai.tools.cloud.cloud import get_cloud_tools
from rai.tools.active_directory.ad import get_ad_tools
from rai.tools.reversing.reversing import get_reversing_tools
from rai.tools.android.android import get_android_tools
from rai.tools.container.container import get_container_tools

__all__ = [
    "BashTool",
    "get_builtin_tools",
    "_get_findings",
    "_add_finding",
    "_clear_findings",
    "FindingsAddTool",
    "FindingsListTool",
    "FindingsExportTool",
    "init_findings_store",
    "HttpRequestTool",
    "NucleiScanTool",
    "NmapScanTool",
    "WebSearchTool",
    "WebFetchTool",
    "CreateSubagentTool",
    "get_security_tools",
    "MemoryReadTool",
    "MemoryWriteTool",
    "MemoryUpdateTool",
    "MemoryFilesListTool",
    "MemoryPathTool",
    "get_memory_tools",
    "_GLOBAL_FILES",
    "_AGENT_FILES",
    "_TARGET_FILES",
    "OPPLANInitTool",
    "OPPLANAddObjectiveTool",
    "OPPLANGetObjectiveTool",
    "OPPLANListObjectivesTool",
    "OPPLANUpdateObjectiveTool",
    "OPPLANExpandObjectiveTool",
    "OPPLANCollapseObjectiveTool",
    "OPPLANSaveTool",
    "OPPLANLoadTool",
    "get_opplan_tools",
    "get_reference_tools",
    "get_web_tools",
    "get_cloud_tools",
    "get_ad_tools",
    "get_reversing_tools",
    "get_android_tools",
    "get_container_tools",
]
