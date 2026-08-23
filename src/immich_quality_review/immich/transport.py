"""Injectable transport contracts for the Immich API adapter."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """A bounded request for an injected HTTP transport."""

    method: str
    path: str
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int
    body: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A response returned by an injected HTTP transport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class TransportError(Exception):
    """Raised by a concrete transport for a bounded request failure."""


class TransportTimeoutError(TransportError, TimeoutError):
    """Raised by a concrete transport when its bounded request times out."""


class ImmichTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Send one bounded request."""
        ...
