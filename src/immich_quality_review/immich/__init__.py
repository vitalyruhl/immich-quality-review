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
from .discovery import (
    DiscoveryCursor,
    DiscoveryCursorCodec,
    DiscoveryCursorError,
    DiscoveryProtocolError,
    ImmichDiscovery,
)
from .events import (
    AdmissionLimiter,
    ConcurrencyAdmissionLimiter,
    EventAdmissionError,
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
    TransportError,
    TransportTimeoutError,
)

__all__ = [
    "EventAuthenticator",
    "EventAuthenticationError",
    "EventAdmissionError",
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
    "AdmissionLimiter",
    "ConcurrencyAdmissionLimiter",
    "DiscoveryCursor",
    "DiscoveryCursorCodec",
    "DiscoveryCursorError",
    "DiscoveryProtocolError",
    "ImmichDiscovery",
    "ReplayGuard",
    "ServerVersion",
    "StaticSecretAuthenticator",
    "TransportTimeoutError",
    "TransportError",
    "WorkflowEventIntake",
]
