import json

import pytest

from immich_quality_review.immich.client import (
    ImmichAuthenticationError,
    ImmichClient,
    ImmichPermissionError,
    ImmichProtocolError,
    ImmichTimeoutError,
)
from immich_quality_review.immich.transport import (
    HttpRequest,
    HttpResponse,
    TransportTimeoutError,
)


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(payload: object, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


def make_client(
    transport: FakeTransport,
    *,
    workflow_event_intake_configured: bool = False,
) -> ImmichClient:
    return ImmichClient(
        transport=transport,
        api_key="test-api-key",
        timeout_seconds=2.5,
        max_response_bytes=2048,
        workflow_event_intake_configured=workflow_event_intake_configured,
    )


def test_about_probe_uses_exact_authenticated_request_contract() -> None:
    transport = FakeTransport(
        [
            response({"major": 3, "minor": 0, "patch": 0, "prerelease": None}),
            response({"version": "3.0.0"}),
        ]
    )
    client = make_client(transport)

    client.probe_capabilities()

    assert transport.requests[1:] == [
        HttpRequest(
            method="GET",
            path="/server/about",
            headers={"x-api-key": "test-api-key"},
            timeout_seconds=2.5,
            max_response_bytes=2048,
        )
    ]


def test_version_probe_does_not_send_api_key() -> None:
    transport = FakeTransport([response({"major": 3, "minor": 1, "patch": 0, "prerelease": None})])

    ImmichClient(
        transport=transport,
        api_key="test-api-key",
        timeout_seconds=2.5,
        max_response_bytes=2048,
        workflow_event_intake_configured=False,
    ).get_server_version()

    assert transport.requests[0].path == "/server/version"
    assert transport.requests[0].headers == {}


def test_v3_version_is_parsed_strictly() -> None:
    transport = FakeTransport([response({"major": 3, "minor": 2, "patch": 1, "prerelease": 4})])

    version = make_client(transport).get_server_version()

    assert (version.major, version.minor, version.patch, version.prerelease) == (3, 2, 1, 4)


def test_unknown_major_fails_safe_before_authenticated_follow_up() -> None:
    transport = FakeTransport([response({"major": 4, "minor": 0, "patch": 0, "prerelease": None})])

    capabilities = make_client(transport).probe_capabilities()

    assert capabilities.api_compatible is False
    assert capabilities.authenticated is False
    assert capabilities.workflow_event_intake is False
    assert capabilities.polling_fallback is False
    assert capabilities.reason_code == "unsupported_major"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"minor": 0, "patch": 0, "prerelease": None},
        {"major": "3", "minor": 0, "patch": 0, "prerelease": None},
        {"major": 3, "minor": False, "patch": 0, "prerelease": None},
        {"major": 3, "minor": 0, "patch": 0, "prerelease": "rc1"},
    ],
)
def test_version_probe_rejects_missing_or_wrongly_typed_fields(payload: object) -> None:
    transport = FakeTransport([response(payload)])

    with pytest.raises(ImmichProtocolError):
        make_client(transport).get_server_version()


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, ImmichAuthenticationError), (403, ImmichPermissionError)],
)
def test_authenticated_probe_maps_authentication_and_permission_failures(
    status_code: int, error_type: type[Exception]
) -> None:
    transport = FakeTransport([response({"secret": "response-body"}, status_code=status_code)])

    with pytest.raises(error_type):
        make_client(transport).probe_capabilities()


def test_transport_timeout_is_redacted() -> None:
    secret = "test-api-key"
    transport = FakeTransport([TransportTimeoutError("private endpoint and " + secret)])

    with pytest.raises(ImmichTimeoutError) as exc_info:
        make_client(transport).get_server_version()

    assert str(exc_info.value) == "Immich request timed out."
    assert secret not in str(exc_info.value)
    assert "private endpoint" not in str(exc_info.value)


def test_oversized_response_is_rejected_without_body_in_exception() -> None:
    confidential_body = b"confidential-response-body"
    transport = FakeTransport([HttpResponse(status_code=200, headers={}, body=confidential_body)])
    client = ImmichClient(
        transport=transport,
        api_key="test-api-key",
        timeout_seconds=2.5,
        max_response_bytes=len(confidential_body) - 1,
        workflow_event_intake_configured=False,
    )

    with pytest.raises(ImmichProtocolError) as exc_info:
        client.get_server_version()

    assert str(exc_info.value) == "Immich response was invalid."
    assert "confidential-response-body" not in str(exc_info.value)
    assert "test-api-key" not in str(exc_info.value)


def test_invalid_authenticated_json_is_rejected() -> None:
    transport = FakeTransport(
        [
            response({"major": 3, "minor": 0, "patch": 0, "prerelease": None}),
            HttpResponse(status_code=200, headers={}, body=b"not-json"),
        ]
    )

    with pytest.raises(ImmichProtocolError):
        make_client(transport).probe_capabilities()


def test_event_intake_disabled_keeps_api_and_polling_available() -> None:
    transport = FakeTransport(
        [
            response({"major": 3, "minor": 0, "patch": 0, "prerelease": None}),
            response({"version": "3.0.0"}),
        ]
    )

    capabilities = make_client(transport).probe_capabilities()

    assert capabilities.api_compatible is True
    assert capabilities.authenticated is True
    assert capabilities.workflow_event_intake is False
    assert capabilities.polling_fallback is True


def test_version_alone_never_enables_event_intake() -> None:
    transport = FakeTransport(
        [
            response({"major": 3, "minor": 0, "patch": 0, "prerelease": None}),
            response({"version": "3.0.0"}),
        ]
    )

    capabilities = make_client(transport).probe_capabilities()

    assert capabilities.workflow_event_intake is False


def test_event_intake_requires_separate_local_configuration() -> None:
    transport = FakeTransport(
        [
            response({"major": 3, "minor": 0, "patch": 0, "prerelease": None}),
            response({"version": "3.0.0"}),
        ]
    )

    capabilities = make_client(
        transport, workflow_event_intake_configured=True
    ).probe_capabilities()

    assert capabilities.api_compatible is True
    assert capabilities.polling_fallback is True
    assert capabilities.workflow_event_intake is True
