"""rai.sdk.middleware — all RAI and deepagents middleware classes.

    from rai.sdk.middleware import AuditLogMiddleware, RateLimitMiddleware
"""

# RAI middleware
from rai.middleware.audit import AuditLogMiddleware
from rai.middleware.execute import ExecuteInterceptorMiddleware
from rai.middleware.findings import FindingsEnrichmentMiddleware
from rai.middleware.hooks import HooksMiddleware
from rai.middleware.model_logger import ModelCallLoggerMiddleware
from rai.middleware.rtk import RTKToolMiddleware
from rai.middleware.model_override import ModelOverrideMiddleware
from rai.middleware.opplan import OPPLANMiddleware
from rai.middleware.ratelimit import RateLimitMiddleware
from rai.middleware.sanitizer import EmptyContentSanitizerMiddleware
from rai.middleware.compression import MessageCompressionMiddleware
from rai.middleware.tool_compaction import ToolResultCompressionMiddleware
from rai.middleware.write_truncate import WriteTruncateMiddleware
from rai.middleware.loop_detection import LoopDetectionMiddleware
from rai.middleware.read_dedup import ReadFileDedupMiddleware
from rai.middleware.subagent_restrict import SubagentRestrictMiddleware
from rai.middleware.allowed_tools import AllowedToolsMiddleware
from rai.middleware.cost_tracking import CostTrackingMiddleware
from rai.middleware.retry import RetryMiddleware

try:
    from rai.middleware.prompt_cache import RAIPromptCachingMiddleware
except ImportError:
    RAIPromptCachingMiddleware = None  # type: ignore[assignment,misc]

# deepagents middleware
from deepagents.middleware import MemoryMiddleware, SkillsMiddleware

# Background agent middleware
from rai.agents.background import LocalAsyncAgentMiddleware
from rai.harness.subagents_v2 import AsyncRaiSubagentsMiddleware

__all__ = [
    "AuditLogMiddleware",
    "ExecuteInterceptorMiddleware",
    "FindingsEnrichmentMiddleware",
    "HooksMiddleware",
    "ModelCallLoggerMiddleware",
    "ModelOverrideMiddleware",
    "OPPLANMiddleware",
    "RateLimitMiddleware",
    "EmptyContentSanitizerMiddleware",
    "RAIPromptCachingMiddleware",
    "MemoryMiddleware",
    "SkillsMiddleware",
    "LocalAsyncAgentMiddleware",
    "RTKToolMiddleware",
    "MessageCompressionMiddleware",
    "ToolResultCompressionMiddleware",
    "WriteTruncateMiddleware",
    "LoopDetectionMiddleware",
    "ReadFileDedupMiddleware",
    "SubagentRestrictMiddleware",
    "AllowedToolsMiddleware",
    "CostTrackingMiddleware",
    "RetryMiddleware",
    "AsyncRaiSubagentsMiddleware",
]
