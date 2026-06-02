"""RAI middleware package."""

from rai.middleware.sanitizer import EmptyContentSanitizerMiddleware
from rai.middleware.audit import AuditLogMiddleware
from rai.middleware.findings import FindingsEnrichmentMiddleware
from rai.middleware.execute import ExecuteInterceptorMiddleware
from rai.middleware.hooks import HooksMiddleware
from rai.middleware.ratelimit import RateLimitMiddleware
from rai.middleware.opplan import OPPLANMiddleware
from rai.middleware.model_override import ModelOverrideMiddleware
try:
    from rai.middleware.prompt_cache import RAIPromptCachingMiddleware
except ImportError:
    RAIPromptCachingMiddleware = None  # type: ignore[assignment,misc]
from rai.middleware.model_logger import ModelCallLoggerMiddleware
from rai.middleware.rtk import RTKToolMiddleware
from rai.middleware.tool_compaction import ToolResultCompressionMiddleware
from rai.middleware.loop_detection import LoopDetectionMiddleware

__all__ = [
    "EmptyContentSanitizerMiddleware",
    "AuditLogMiddleware",
    "FindingsEnrichmentMiddleware",
    "ExecuteInterceptorMiddleware",
    "HooksMiddleware",
    "RateLimitMiddleware",
    "OPPLANMiddleware",
    "ModelOverrideMiddleware",
    "RAIPromptCachingMiddleware",
    "ModelCallLoggerMiddleware",
    "RTKToolMiddleware",
    "ToolResultCompressionMiddleware",
    "LoopDetectionMiddleware",
]
