"""rai.sdk.harness — RAI HTTP server and configuration.

    from rai.sdk.harness import RAIHTTPServer, HTTPConfig

    server = RAIHTTPServer(HTTPConfig(port=8000))
    server.register(RAIAgent.builder().agent_name("rai").without_hitl())
    server.run()
"""

from rai.harness import HTTPConfig, RAIHTTPServer, RAIClient, SSEEvent

__all__ = ["RAIHTTPServer", "HTTPConfig", "RAIClient", "SSEEvent"]
