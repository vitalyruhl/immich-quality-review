"""Injectable transport contracts for the Immich API adapter."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """A bounded request for an injected HTTP transport."""

    method: str
    path: str
    headers: Mapping[str, str]
    timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A response returned by an injected HTTP transport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class TransportTimeoutError(TimeoutError):
    """Raised by a concrete transport when its bounded request times out."""


class ImmichTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Send one bounded request."""
        ...
