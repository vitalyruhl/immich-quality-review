"""Version-aware, redacted Immich API capability client."""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from immich_quality_review.application.integration import IntegrationCapabilities

from .transport import HttpRequest, HttpResponse, ImmichTransport, TransportTimeoutError

MAX_IMMICH_PAGE = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class ServerVersion:
    """Strictly parsed Immich server version fields."""

    major: int
    minor: int
    patch: int
    prerelease: int | None


class ImmichClientError(Exception):
    """Base class for redacted client failures."""


class ImmichAuthenticationError(ImmichClientError):
    """The configured API key was rejected."""

    def __init__(self) -> None:
        super().__init__("Immich authentication failed.")


class ImmichPermissionError(ImmichClientError):
    """The API key lacks the required permission."""

    def __init__(self) -> None:
        super().__init__("Immich permission was denied.")


class ImmichTimeoutError(ImmichClientError):
    """The bounded transport request timed out."""

    def __init__(self) -> None:
        super().__init__("Immich request timed out.")


class ImmichProtocolError(ImmichClientError):
    """The response did not satisfy the expected bounded API contract."""

    def __init__(self) -> None:
        super().__init__("Immich response was invalid.")


class ImmichClient:
    """Probe the stable public Immich API through an injected transport."""

    SUPPORTED_MAJOR = 3

    def __init__(
        self,
        *,
        transport: ImmichTransport,
        api_key: str,
        timeout_seconds: float,
        max_response_bytes: int,
        workflow_event_intake_configured: bool,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must not be empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("max_response_bytes must be positive")
        if not isinstance(workflow_event_intake_configured, bool):
            raise ValueError("workflow_event_intake_configured must be bool")
        self._transport = transport
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._workflow_event_intake_configured = workflow_event_intake_configured

    def get_server_version(self) -> ServerVersion:
        response = self._send(path="/server/version", authenticated=False)
        payload = self._parse_json_object(response)
        if set(payload) != {"major", "minor", "patch", "prerelease"}:
            raise ImmichProtocolError
        major = self._strict_non_negative_int(payload["major"])
        minor = self._strict_non_negative_int(payload["minor"])
        patch = self._strict_non_negative_int(payload["patch"])
        prerelease_value = payload["prerelease"]
        if prerelease_value is not None:
            prerelease = self._strict_non_negative_int(prerelease_value)
        else:
            prerelease = None
        return ServerVersion(major=major, minor=minor, patch=patch, prerelease=prerelease)

    def search_assets(
        self,
        *,
        page: int,
        size: int,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
    ) -> dict[str, Any]:
        """Search the bounded timeline-image metadata contract."""
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_IMMICH_PAGE:
            raise ValueError("page must be a positive integer")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 1000:
            raise ValueError("size must be between 1 and 1000")
        if updated_after is not None:
            self._validate_aware_datetime(updated_after, "updated_after")
        if updated_before is not None:
            self._validate_aware_datetime(updated_before, "updated_before")
        if (
            updated_after is not None
            and updated_before is not None
            and updated_after >= updated_before
        ):
            raise ValueError("updated_after must be before updated_before")

        search_body: dict[str, object] = {
            "order": "asc",
            "page": page,
            "size": size,
            "type": "IMAGE",
            "visibility": "timeline",
            "withDeleted": False,
            "withExif": False,
            "withPeople": False,
            "withStacked": False,
        }
        if updated_after is not None:
            search_body["updatedAfter"] = self._format_query_timestamp(updated_after)
        if updated_before is not None:
            search_body["updatedBefore"] = self._format_query_timestamp(updated_before)
        response = self._send(
            method="POST",
            path="/search/metadata",
            authenticated=True,
            headers={"content-type": "application/json"},
            body=json.dumps(search_body, separators=(",", ":")).encode("utf-8"),
        )
        return self._parse_json_object(response)

    def probe_capabilities(self) -> IntegrationCapabilities:
        version = self.get_server_version()
        if version.major != self.SUPPORTED_MAJOR:
            return IntegrationCapabilities(
                provider_version=self._format_version(version),
                api_compatible=False,
                authenticated=False,
                workflow_event_intake=False,
                polling_fallback=False,
                reason_code="unsupported_major",
            )

        about_response = self._send(path="/server/about", authenticated=True)
        about = self._parse_json_object(about_response)
        if (
            not isinstance(about.get("licensed"), bool)
            or not isinstance(about.get("version"), str)
            or not isinstance(about.get("versionUrl"), str)
        ):
            raise ImmichProtocolError
        return IntegrationCapabilities(
            provider_version=self._format_version(version),
            api_compatible=True,
            authenticated=True,
            workflow_event_intake=self._workflow_event_intake_configured,
            polling_fallback=True,
        )

    def _send(
        self,
        *,
        method: str = "GET",
        path: str,
        authenticated: bool,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        request_headers = {"x-api-key": self._api_key} if authenticated else {}
        if headers is not None:
            request_headers.update(headers)
        request = HttpRequest(
            method=method,
            path=path,
            headers=request_headers,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            body=body,
        )
        try:
            response = self._transport.send(request)
        except TransportTimeoutError:
            raise ImmichTimeoutError from None
        except Exception:
            raise ImmichProtocolError from None
        if (
            not isinstance(response, HttpResponse)
            or isinstance(response.status_code, bool)
            or not isinstance(response.status_code, int)
            or not isinstance(response.headers, Mapping)
            or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in response.headers.items()
            )
            or not isinstance(response.body, bytes)
        ):
            raise ImmichProtocolError
        if len(response.body) > self._max_response_bytes:
            raise ImmichProtocolError
        if response.status_code == 401:
            raise ImmichAuthenticationError
        if response.status_code == 403:
            raise ImmichPermissionError
        if not 200 <= response.status_code < 300:
            raise ImmichProtocolError
        return response

    @staticmethod
    def _parse_json_object(response: HttpResponse) -> dict[str, Any]:
        content_type = next(
            (value for key, value in response.headers.items() if key.lower() == "content-type"),
            None,
        )
        if content_type is not None:
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise ImmichProtocolError
        try:
            payload = json.loads(response.body)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ImmichProtocolError from None
        if not isinstance(payload, dict):
            raise ImmichProtocolError
        return payload

    @staticmethod
    def _strict_non_negative_int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ImmichProtocolError
        return value

    @staticmethod
    def _validate_aware_datetime(value: object, name: str) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")

    @classmethod
    def _format_query_timestamp(cls, value: datetime) -> str:
        cls._validate_aware_datetime(value, "timestamp")
        return value.astimezone(UTC).isoformat(timespec="auto").replace("+00:00", "Z")

    @staticmethod
    def _format_version(version: ServerVersion) -> str:
        suffix = "" if version.prerelease is None else f"-{version.prerelease}"
        return f"{version.major}.{version.minor}.{version.patch}{suffix}"
