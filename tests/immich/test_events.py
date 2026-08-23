import json
from datetime import UTC, datetime, timedelta

import pytest

from immich_quality_review.application.integration import WorkRequest, WorkSink, WorkSource
from immich_quality_review.immich.events import (
    EventAuthenticationError,
    EventFreshnessError,
    EventPayloadError,
    EventReplayError,
    ReplayGuard,
    StaticSecretAuthenticator,
    WorkflowEventIntake,
)


class FakeSink:
    def __init__(self) -> None:
        self.requests: list[WorkRequest] = []

    def submit(self, request: WorkRequest) -> None:
        self.requests.append(request)


class FakeReplayGuard:
    def __init__(self, *, claim_result: bool = True) -> None:
        self.claim_result = claim_result
        self.claims: list[tuple[str, datetime]] = []

    def claim(self, event_id: str, expires_at: datetime) -> bool:
        self.claims.append((event_id, expires_at))
        return self.claim_result


def event_payload(
    *,
    event_id: str = "event-1",
    asset_id: str = "asset-1",
    occurred_at: str = "2026-08-23T12:00:00+00:00",
    **extra: object,
) -> bytes:
    payload: dict[str, object] = {
        "eventId": event_id,
        "assetId": asset_id,
        "occurredAt": occurred_at,
    }
    payload.update(extra)
    return json.dumps(payload).encode()


def make_intake(
    sink: WorkSink | FakeSink,
    guard: ReplayGuard | FakeReplayGuard,
    *,
    now: datetime = datetime(2026, 8, 23, 12, 2, tzinfo=UTC),
    secret: str = "bridge-secret-with-enough-length",
    max_age: timedelta = timedelta(minutes=5),
    max_future_skew: timedelta = timedelta(seconds=30),
    max_payload_bytes: int = 4096,
) -> WorkflowEventIntake:
    return WorkflowEventIntake(
        authenticator=StaticSecretAuthenticator(secret),
        replay_guard=guard,
        sink=sink,
        now=now,
        max_age=max_age,
        max_future_skew=max_future_skew,
        max_payload_bytes=max_payload_bytes,
    )


def test_valid_event_is_normalized_once_into_the_shared_sink() -> None:
    sink = FakeSink()
    guard = FakeReplayGuard()

    result = make_intake(sink, guard).accept(
        event_payload(), credential="bridge-secret-with-enough-length"
    )

    assert result == sink.requests[0]
    assert result == WorkRequest(
        asset_id="asset-1",
        source=WorkSource.WORKFLOW_EVENT,
        event_id="event-1",
        occurred_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    assert len(sink.requests) == 1
    assert guard.claims == [("event-1", datetime(2026, 8, 23, 12, 5, tzinfo=UTC))]


def test_wrong_credential_is_rejected_without_sink_or_guard_access() -> None:
    sink = FakeSink()
    guard = FakeReplayGuard()

    with pytest.raises(EventAuthenticationError):
        make_intake(sink, guard).accept(event_payload(), credential="wrong-secret")

    assert sink.requests == []
    assert guard.claims == []


@pytest.mark.parametrize("secret", ["", "short"])
def test_runtime_secret_must_be_non_empty_and_sufficiently_long(secret: str) -> None:
    with pytest.raises(ValueError):
        StaticSecretAuthenticator(secret)


def test_oversized_payload_is_rejected_before_json_processing() -> None:
    sink = FakeSink()
    guard = FakeReplayGuard()
    intake = make_intake(sink, guard, max_payload_bytes=10)

    with pytest.raises(EventPayloadError):
        intake.accept(b"{" + b"x" * 20, credential="bridge-secret-with-enough-length")

    assert sink.requests == []
    assert guard.claims == []


@pytest.mark.parametrize("payload", [b"not utf8: \xff", b"not-json"])
def test_invalid_utf8_or_json_is_rejected(payload: bytes) -> None:
    with pytest.raises(EventPayloadError):
        make_intake(FakeSink(), FakeReplayGuard()).accept(
            payload, credential="bridge-secret-with-enough-length"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"eventId": "event-1", "assetId": "asset-1"},
        {
            "eventId": "event-1",
            "assetId": "asset-1",
            "occurredAt": "2026-08-23T12:00:00+00:00",
            "token": "internal-token",
        },
    ],
)
def test_missing_or_additional_fields_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(EventPayloadError):
        make_intake(FakeSink(), FakeReplayGuard()).accept(
            json.dumps(payload).encode(), credential="bridge-secret-with-enough-length"
        )


@pytest.mark.parametrize(
    "event_id,asset_id",
    [("", "asset-1"), ("e" * 129, "asset-1"), ("event-1", ""), ("event-1", "a" * 129)],
)
def test_identifiers_must_be_non_empty_and_bounded(event_id: str, asset_id: str) -> None:
    with pytest.raises(EventPayloadError):
        make_intake(FakeSink(), FakeReplayGuard()).accept(
            event_payload(event_id=event_id, asset_id=asset_id),
            credential="bridge-secret-with-enough-length",
        )


def test_timezone_naive_timestamp_is_rejected() -> None:
    with pytest.raises(EventPayloadError):
        make_intake(FakeSink(), FakeReplayGuard()).accept(
            event_payload(occurred_at="2026-08-23T12:00:00"),
            credential="bridge-secret-with-enough-length",
        )


def test_stale_and_future_events_are_rejected() -> None:
    intake = make_intake(FakeSink(), FakeReplayGuard())

    with pytest.raises(EventFreshnessError):
        intake.accept(
            event_payload(occurred_at="2026-08-23T11:56:59+00:00"),
            credential="bridge-secret-with-enough-length",
        )

    with pytest.raises(EventFreshnessError):
        intake.accept(
            event_payload(occurred_at="2026-08-23T12:02:31+00:00"),
            credential="bridge-secret-with-enough-length",
        )


def test_already_claimed_event_is_rejected_without_sink_submission() -> None:
    sink = FakeSink()
    guard = FakeReplayGuard(claim_result=False)

    with pytest.raises(EventReplayError):
        make_intake(sink, guard).accept(
            event_payload(), credential="bridge-secret-with-enough-length"
        )

    assert sink.requests == []
    assert len(guard.claims) == 1


def test_credentials_internal_tokens_and_raw_payload_are_not_exposed() -> None:
    secret = "bridge-secret-with-enough-length"
    internal_token = "internal-workflow-token"
    raw_payload = event_payload(token=internal_token).decode()

    with pytest.raises(EventPayloadError) as exc_info:
        make_intake(FakeSink(), FakeReplayGuard(), secret=secret).accept(
            raw_payload.encode(), credential=secret
        )

    message = str(exc_info.value)
    assert secret not in message
    assert internal_token not in message
    assert raw_payload not in message
    assert secret not in repr(exc_info.value)


def test_api_and_workflow_paths_use_the_same_work_request_type() -> None:
    sink = FakeSink()
    guard = FakeReplayGuard()
    workflow_request = make_intake(sink, guard).accept(
        event_payload(), credential="bridge-secret-with-enough-length"
    )
    api_request = WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL)

    assert type(workflow_request) is type(api_request) is WorkRequest
