from datetime import UTC, datetime, timedelta

import pytest

from immich_quality_review.application.discovery import DiscoveryCheckpoint, DiscoveryPage
from immich_quality_review.application.integration import WorkRequest, WorkSource
from immich_quality_review.application.state import (
    AnalysisRevision,
    DiscoveryCommitOutcome,
    DiscoveryCommitResult,
    DiscoveryResumeState,
    DurableDiscoveryCoordinator,
    DurableWorkSink,
    EnqueueOutcome,
    EnqueueResult,
    FailureDisposition,
    ProcessingFailure,
    ProcessingKey,
    ProcessingStatus,
    WorkClaim,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_processing_key_is_immutable_and_validates_opaque_identifiers() -> None:
    revision = AnalysisRevision("quality-v1")
    key = ProcessingKey(asset_id="asset/opaque-id", analysis_revision=revision)

    assert key.asset_id == "asset/opaque-id"
    with pytest.raises((AttributeError, TypeError)):
        key.asset_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("constructor", "value"),
    [
        (AnalysisRevision, ""),
        (AnalysisRevision, "x" * 129),
        (AnalysisRevision, "quality\nrevision"),
        (ProcessingKey, ""),
        (ProcessingKey, "x" * 257),
        (ProcessingKey, "asset\tidentifier"),
    ],
)
def test_identifiers_reject_empty_oversized_and_control_values(constructor, value) -> None:
    if constructor is ProcessingKey:
        with pytest.raises(ValueError):
            constructor(asset_id=value, analysis_revision=AnalysisRevision("v1"))
    else:
        with pytest.raises(ValueError):
            constructor(value)


def test_processing_lifecycle_and_failure_disposition_are_stable_enums() -> None:
    assert ProcessingStatus.PENDING.value == "pending"
    assert ProcessingStatus.IN_PROGRESS.value == "in_progress"
    assert ProcessingStatus.SUCCEEDED.value == "succeeded"
    assert ProcessingStatus.RETRYABLE_FAILURE.value == "retryable_failure"
    assert ProcessingStatus.TERMINAL_FAILURE.value == "terminal_failure"
    assert FailureDisposition.RETRYABLE.value == "retryable"
    assert FailureDisposition.TERMINAL.value == "terminal"


@pytest.mark.parametrize("reason_code", ["", "BadCode", "has space", "x" * 65, "secret\ntext"])
def test_processing_failure_rejects_unstable_reason_codes(reason_code: str) -> None:
    with pytest.raises(ValueError):
        ProcessingFailure(FailureDisposition.RETRYABLE, reason_code)


def test_processing_failure_accepts_stable_reason_code_without_exception_text() -> None:
    failure = ProcessingFailure(FailureDisposition.TERMINAL, "decode.invalid-format")

    assert failure.disposition is FailureDisposition.TERMINAL
    assert failure.reason_code == "decode.invalid-format"


def test_work_claim_requires_matching_shape_and_timezone_aware_lease() -> None:
    key = ProcessingKey("asset-1", AnalysisRevision("v1"))
    claim = WorkClaim(
        key=key,
        claim_token="token-1",
        attempt=1,
        source=WorkSource.API_BACKFILL,
        lease_expires_at=NOW + timedelta(minutes=5),
    )

    assert claim.attempt == 1
    assert claim.lease_expires_at.tzinfo is not None

    with pytest.raises(ValueError):
        WorkClaim(key, "token-1", 0, WorkSource.API_BACKFILL, NOW)
    with pytest.raises(ValueError):
        WorkClaim(key, "token-1", 1, WorkSource.API_BACKFILL, NOW.replace(tzinfo=None))


def test_discovery_resume_state_requires_exactly_one_progress_marker() -> None:
    revision = AnalysisRevision("v1")
    checkpoint = DiscoveryCheckpoint(completed_through=NOW)

    cursor_state = DiscoveryResumeState("asset-stream", revision, 1, "cursor-1", None)
    checkpoint_state = DiscoveryResumeState("asset-stream", revision, 2, None, checkpoint)

    assert cursor_state.cursor == "cursor-1"
    assert checkpoint_state.checkpoint == checkpoint

    with pytest.raises(ValueError):
        DiscoveryResumeState("asset-stream", revision, 1, None, None)
    with pytest.raises(ValueError):
        DiscoveryResumeState("asset-stream", revision, 1, "cursor-1", checkpoint)
    with pytest.raises(ValueError):
        DiscoveryResumeState("asset\nstream", revision, 1, "cursor-1", None)


def test_result_types_distinguish_insert_duplicate_commit_and_replay() -> None:
    state = DiscoveryResumeState("stream", AnalysisRevision("v1"), 1, "cursor", None)

    assert EnqueueResult(EnqueueOutcome.INSERTED).outcome is EnqueueOutcome.INSERTED
    assert EnqueueResult(EnqueueOutcome.DUPLICATE).outcome is EnqueueOutcome.DUPLICATE
    result = DiscoveryCommitResult(DiscoveryCommitOutcome.REPLAYED, state)
    assert result.outcome is DiscoveryCommitOutcome.REPLAYED
    assert result.state == state


class FakeDiscovery:
    def __init__(self, page: DiscoveryPage) -> None:
        self.page = page
        self.calls: list[tuple[str | None, DiscoveryCheckpoint | None, int]] = []

    def discover_page(
        self,
        *,
        cursor: str | None,
        checkpoint: DiscoveryCheckpoint | None,
        limit: int,
    ) -> DiscoveryPage:
        self.calls.append((cursor, checkpoint, limit))
        return self.page


class FakeState:
    def __init__(self, state: DiscoveryResumeState | None = None) -> None:
        self.state = state
        self.enqueued: list[tuple[WorkRequest, AnalysisRevision, datetime]] = []
        self.commits: list[dict[str, object]] = []

    def load_discovery_state(self, *, stream_id, analysis_revision):
        return self.state

    def enqueue_work(self, *, request, analysis_revision, now):
        self.enqueued.append((request, analysis_revision, now))
        return EnqueueResult(EnqueueOutcome.INSERTED)

    def commit_discovery_page(
        self,
        *,
        stream_id,
        analysis_revision,
        expected_state,
        requests,
        progress,
        now,
    ):
        self.commits.append(
            {
                "stream_id": stream_id,
                "analysis_revision": analysis_revision,
                "expected_state": expected_state,
                "requests": requests,
                "progress": progress,
                "now": now,
            }
        )
        return DiscoveryCommitResult(
            DiscoveryCommitOutcome.COMMITTED,
            DiscoveryResumeState(
                stream_id,
                analysis_revision,
                1 if expected_state is None else expected_state.generation + 1,
                progress.next_cursor,
                progress.completed_checkpoint,
            ),
        )


def _descriptor(asset_id: str):
    from immich_quality_review.application.discovery import AssetDescriptor, AssetMediaType

    return AssetDescriptor(
        asset_id=asset_id,
        media_type=AssetMediaType.IMAGE,
        mime_type="image/jpeg",
        created_at=NOW,
        updated_at=NOW,
        width=10,
        height=10,
    )


def test_durable_coordinator_starts_new_revision_without_saved_state() -> None:
    revision = AnalysisRevision("v1")
    storage = FakeState()
    discovery = FakeDiscovery(
        DiscoveryPage(
            assets=(_descriptor("asset-1"),),
            next_cursor="cursor-1",
            completed_checkpoint=None,
        )
    )
    coordinator = DurableDiscoveryCoordinator(
        discovery=discovery,
        storage=storage,
        stream_id="asset-stream",
        analysis_revision=revision,
        now=lambda: NOW,
    )

    result = coordinator.discover_page(limit=100)

    assert discovery.calls == [(None, None, 100)]
    assert result.outcome is DiscoveryCommitOutcome.COMMITTED
    assert storage.commits[0]["expected_state"] is None
    assert storage.commits[0]["requests"] == (
        WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL),
    )


def test_durable_coordinator_resumes_cursor_or_terminal_checkpoint() -> None:
    revision = AnalysisRevision("v1")
    cursor_state = DiscoveryResumeState("asset-stream", revision, 1, "cursor-1", None)
    storage = FakeState(cursor_state)
    discovery = FakeDiscovery(
        DiscoveryPage(
            assets=(),
            next_cursor=None,
            completed_checkpoint=DiscoveryCheckpoint(NOW),
        )
    )
    coordinator = DurableDiscoveryCoordinator(
        discovery=discovery,
        storage=storage,
        stream_id="asset-stream",
        analysis_revision=revision,
        now=lambda: NOW,
    )

    coordinator.discover_page(limit=50)

    assert discovery.calls == [("cursor-1", None, 50)]
    assert storage.commits[0]["expected_state"] == cursor_state


def test_durable_work_sink_uses_one_revision_aware_enqueue_path() -> None:
    revision = AnalysisRevision("v1")
    storage = FakeState()
    sink = DurableWorkSink(storage=storage, analysis_revision=revision, now=lambda: NOW)
    request = WorkRequest(
        asset_id="asset-1",
        source=WorkSource.WORKFLOW_EVENT,
        event_id="event-1",
        occurred_at=NOW,
    )

    sink.submit(request)

    assert storage.enqueued == [(request, revision, NOW)]
