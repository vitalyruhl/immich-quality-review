"""Public contracts for the bounded Immich integration boundary."""

from .client import (
    ImmichAuthenticationError,
    ImmichClient,
    ImmichClientError,
    ImmichPermissionError,
    ImmichProtocolError,
    ImmichTimeoutError,
    ServerVersion,
)
from .events import (
    EventAuthenticationError,
    EventAuthenticator,
    EventFreshnessError,
    EventPayloadError,
    EventReplayError,
    ReplayGuard,
    StaticSecretAuthenticator,
    WorkflowEventIntake,
)
from .transport import (
    HttpRequest,
    HttpResponse,
    ImmichTransport,
    TransportTimeoutError,
)

__all__ = [
    "EventAuthenticator",
    "EventAuthenticationError",
    "EventFreshnessError",
    "EventPayloadError",
    "EventReplayError",
    "HttpRequest",
    "HttpResponse",
    "ImmichAuthenticationError",
    "ImmichClient",
    "ImmichClientError",
    "ImmichPermissionError",
    "ImmichProtocolError",
    "ImmichTimeoutError",
    "ImmichTransport",
    "ReplayGuard",
    "ServerVersion",
    "StaticSecretAuthenticator",
    "TransportTimeoutError",
    "WorkflowEventIntake",
]
