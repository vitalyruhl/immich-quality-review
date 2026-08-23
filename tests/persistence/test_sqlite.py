import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from immich_quality_review.application.discovery import DiscoveryCheckpoint, DiscoveryProgress
from immich_quality_review.application.integration import WorkRequest, WorkSource
from immich_quality_review.application.state import (
    AnalysisRevision,
    EnqueueOutcome,
    FailureDisposition,
    ProcessingFailure,
    ProcessingKey,
    StateStoreError,
)
from immich_quality_review.persistence.sqlite import SQLiteProcessingState

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_empty_database_initializes_schema_version_one_and_reopens(tmp_path) -> None:
    database = tmp_path / "state.sqlite"

    SQLiteProcessingState(database)
    reopened = SQLiteProcessingState(database)

    assert reopened.path == database
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"work_items", "discovery_state"}.issubset(tables)


def test_missing_parent_is_rejected_without_creating_directories(tmp_path) -> None:
    database = tmp_path / "missing" / "state.sqlite"

    with pytest.raises(StateStoreError):
        SQLiteProcessingState(database)

    assert not database.parent.exists()


def test_unsupported_schema_version_is_rejected_without_path_leak(tmp_path) -> None:
    database = tmp_path / "future.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(StateStoreError) as error:
        SQLiteProcessingState(database)

    assert str(database) not in str(error.value)
    assert "future.sqlite" not in str(error.value)


def test_schema_enforces_exactly_one_discovery_marker(tmp_path) -> None:
    database = tmp_path / "constraints.sqlite"
    SQLiteProcessingState(database)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO discovery_state
                    (stream_id, analysis_revision, generation, cursor, checkpoint, updated_at)
                VALUES ('stream', 'v1', 1, NULL, NULL, '2026-08-23T12:00:00Z')
                """
            )


def test_enqueue_uses_parameterized_identifiers_and_utc_timestamps(tmp_path) -> None:
    database = tmp_path / "identifiers.sqlite"
    store = SQLiteProcessingState(database)
    revision = AnalysisRevision("revision' OR 1=1 --")
    request = WorkRequest(
        asset_id="asset' OR 1=1 --",
        source=WorkSource.API_BACKFILL,
    )
    local_time = datetime(2026, 8, 23, 14, 0, tzinfo=timezone(timedelta(hours=2)))

    result = store.enqueue_work(request=request, analysis_revision=revision, now=local_time)

    assert result.outcome is EnqueueOutcome.INSERTED
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT asset_id, analysis_revision, created_at FROM work_items"
        ).fetchone()
    assert row == (request.asset_id, revision.value, "2026-08-23T12:00:00.000000Z")


def _request(asset_id: str, source: WorkSource = WorkSource.API_BACKFILL) -> WorkRequest:
    if source is WorkSource.API_BACKFILL:
        return WorkRequest(asset_id=asset_id, source=source)
    return WorkRequest(
        asset_id=asset_id,
        source=source,
        event_id="event-1",
        occurred_at=NOW,
    )


def test_enqueue_deduplicates_api_and_workflow_sources_by_revision(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "dedupe.sqlite")
    revision = AnalysisRevision("v1")

    first = store.enqueue_work(request=_request("asset-1"), analysis_revision=revision, now=NOW)
    api_duplicate = store.enqueue_work(
        request=_request("asset-1"), analysis_revision=revision, now=NOW + timedelta(seconds=1)
    )
    workflow_duplicate = store.enqueue_work(
        request=_request("asset-1", WorkSource.WORKFLOW_EVENT),
        analysis_revision=revision,
        now=NOW + timedelta(seconds=2),
    )

    assert first.outcome is EnqueueOutcome.INSERTED
    assert api_duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert workflow_duplicate.outcome is EnqueueOutcome.DUPLICATE
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_items").fetchone() == (1,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(work_items)")}
    assert "event_id" not in columns
    assert "occurred_at" not in columns


def test_new_analysis_revision_creates_independent_work_without_reopening_success(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "revisions.sqlite")
    old_revision = AnalysisRevision("v1")
    new_revision = AnalysisRevision("v2")
    request = _request("asset-1")

    store.enqueue_work(request=request, analysis_revision=old_revision, now=NOW)
    claim = store.claim_next(
        analysis_revision=old_revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="old-token",
    )
    assert claim is not None
    store.complete_success(key=claim.key, claim_token=claim.claim_token, now=NOW)

    duplicate = store.enqueue_work(request=request, analysis_revision=old_revision, now=NOW)
    fresh = store.enqueue_work(request=request, analysis_revision=new_revision, now=NOW)

    assert duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert fresh.outcome is EnqueueOutcome.INSERTED
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "SELECT analysis_revision, status FROM work_items ORDER BY analysis_revision"
        ).fetchall()
    assert rows == [("v1", "succeeded"), ("v2", "pending")]


def test_duplicate_delivery_does_not_reopen_terminal_work(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "terminal.sqlite")
    revision = AnalysisRevision("v1")
    request = _request("asset-1")
    store.enqueue_work(request=request, analysis_revision=revision, now=NOW)
    claim = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="token",
    )
    assert claim is not None
    store.complete_failure(
        key=claim.key,
        claim_token=claim.claim_token,
        failure=ProcessingFailure(FailureDisposition.TERMINAL, "asset.unavailable"),
        now=NOW,
    )

    assert (
        store.enqueue_work(
            request=_request("asset-1", WorkSource.WORKFLOW_EVENT),
            analysis_revision=revision,
            now=NOW,
        ).outcome
        is EnqueueOutcome.DUPLICATE
    )
    assert (
        store.claim_next(
            analysis_revision=revision,
            now=NOW,
            lease_duration=timedelta(minutes=5),
            claim_token="new-token",
        )
        is None
    )


def test_claim_order_attempts_and_lease_recovery_survive_reopen(tmp_path) -> None:
    database = tmp_path / "claims.sqlite"
    store = SQLiteProcessingState(database)
    revision = AnalysisRevision("v1")
    store.enqueue_work(request=_request("asset-b"), analysis_revision=revision, now=NOW)
    store.enqueue_work(request=_request("asset-a"), analysis_revision=revision, now=NOW)

    first = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="first",
    )
    assert first is not None
    assert first.key.asset_id == "asset-a"
    assert first.attempt == 1
    assert (
        SQLiteProcessingState(database).claim_next(
            analysis_revision=revision,
            now=NOW + timedelta(minutes=1),
            lease_duration=timedelta(minutes=5),
            claim_token="blocked",
        )
        is not None
    )

    recovered = SQLiteProcessingState(database).claim_next(
        analysis_revision=revision,
        now=NOW + timedelta(minutes=6),
        lease_duration=timedelta(minutes=5),
        claim_token="recovered",
    )
    assert recovered is not None
    assert recovered.key.asset_id == "asset-a"
    assert recovered.attempt == 2


def test_retryable_failure_is_claimable_and_terminal_or_success_is_not(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "lifecycle.sqlite")
    revision = AnalysisRevision("v1")
    store.enqueue_work(request=_request("retry"), analysis_revision=revision, now=NOW)
    retry_claim = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="retry-1",
    )
    assert retry_claim is not None
    store.complete_failure(
        key=retry_claim.key,
        claim_token="retry-1",
        failure=ProcessingFailure(FailureDisposition.RETRYABLE, "network.timeout"),
        now=NOW,
    )
    retry_again = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="retry-2",
    )
    assert retry_again is not None
    assert retry_again.attempt == 2
    store.complete_success(key=retry_again.key, claim_token="retry-2", now=NOW)
    assert (
        store.claim_next(
            analysis_revision=revision,
            now=NOW,
            lease_duration=timedelta(minutes=5),
            claim_token="retry-3",
        )
        is None
    )


def test_stale_claim_cannot_complete_work_and_invalid_lease_is_rejected(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "stale.sqlite")
    revision = AnalysisRevision("v1")
    store.enqueue_work(request=_request("asset-1"), analysis_revision=revision, now=NOW)
    claim = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="current",
    )
    assert claim is not None
    with pytest.raises(ValueError):
        store.claim_next(
            analysis_revision=revision,
            now=NOW,
            lease_duration=timedelta(0),
            claim_token="bad",
        )
    with pytest.raises(StateStoreError):
        store.complete_failure(
            key=ProcessingKey("asset-1", revision),
            claim_token="stale",
            failure=ProcessingFailure(FailureDisposition.TERMINAL, "asset.invalid"),
            now=NOW,
        )


def test_success_completion_replay_is_idempotent_without_updating_timestamp(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "success-replay.sqlite")
    revision = AnalysisRevision("v1")
    store.enqueue_work(request=_request("asset-1"), analysis_revision=revision, now=NOW)
    claim = store.claim_next(
        analysis_revision=revision,
        now=NOW,
        lease_duration=timedelta(minutes=5),
        claim_token="token",
    )
    assert claim is not None
    store.complete_success(key=claim.key, claim_token=claim.claim_token, now=NOW)
    store.complete_success(
        key=claim.key,
        claim_token=claim.claim_token,
        now=NOW + timedelta(minutes=1),
    )

    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT status, updated_at FROM work_items").fetchone() == (
            "succeeded",
            "2026-08-23T12:00:00.000000Z",
        )


def test_concurrent_connections_claim_distinct_items(tmp_path) -> None:
    database = tmp_path / "concurrent.sqlite"
    revision = AnalysisRevision("v1")
    store = SQLiteProcessingState(database)
    store.enqueue_work(request=_request("asset-a"), analysis_revision=revision, now=NOW)
    store.enqueue_work(request=_request("asset-b"), analysis_revision=revision, now=NOW)

    def claim(token: str):
        return SQLiteProcessingState(database).claim_next(
            analysis_revision=revision,
            now=NOW,
            lease_duration=timedelta(minutes=5),
            claim_token=token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ["one", "two"]))

    assert {item.key.asset_id for item in claims if item is not None} == {"asset-a", "asset-b"}


def test_discovery_page_commits_work_and_cursor_together(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "discovery.sqlite")
    revision = AnalysisRevision("v1")
    progress = DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None)

    result = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=(_request("asset-1"), _request("asset-2")),
        progress=progress,
        now=NOW,
    )

    assert result.outcome.value == "committed"
    assert result.state.generation == 1
    assert (
        store.load_discovery_state(stream_id="asset-stream", analysis_revision=revision)
        == result.state
    )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_items").fetchone() == (2,)


def test_terminal_discovery_page_commits_checkpoint_and_empty_pages_advance_state(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "terminal-discovery.sqlite")
    revision = AnalysisRevision("v1")
    first = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=(),
        progress=DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None),
        now=NOW,
    )
    second = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=first.state,
        requests=(),
        progress=DiscoveryProgress(
            next_cursor=None,
            completed_checkpoint=DiscoveryCheckpoint(completed_through=NOW),
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert second.state.generation == 2
    assert second.state.checkpoint is not None
    assert second.state.cursor is None


def test_exact_discovery_transition_replay_does_not_reinsert_or_increment_generation(
    tmp_path,
) -> None:
    store = SQLiteProcessingState(tmp_path / "replay.sqlite")
    revision = AnalysisRevision("v1")
    progress = DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None)
    requests = (_request("asset-1"),)
    first = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=requests,
        progress=progress,
        now=NOW,
    )

    replay = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=requests,
        progress=progress,
        now=NOW + timedelta(seconds=1),
    )

    assert replay.outcome.value == "replayed"
    assert replay.state == first.state
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_items").fetchone() == (1,)


def test_stale_incompatible_discovery_generation_is_rejected(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "conflict.sqlite")
    revision = AnalysisRevision("v1")
    first = store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=(),
        progress=DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None),
        now=NOW,
    )
    store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=first.state,
        requests=(),
        progress=DiscoveryProgress(next_cursor="cursor-2", completed_checkpoint=None),
        now=NOW,
    )

    with pytest.raises(StateStoreError):
        store.commit_discovery_page(
            stream_id="asset-stream",
            analysis_revision=revision,
            expected_state=first.state,
            requests=(_request("stale"),),
            progress=DiscoveryProgress(next_cursor="cursor-stale", completed_checkpoint=None),
            now=NOW,
        )
    assert (
        store.load_discovery_state(stream_id="asset-stream", analysis_revision=revision).cursor
        == "cursor-2"
    )


def test_api_and_workflow_overlap_resolves_to_one_row_in_discovery_commit(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "overlap.sqlite")
    revision = AnalysisRevision("v1")
    store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=(
            _request("asset-1"),
            _request("asset-1", WorkSource.WORKFLOW_EVENT),
        ),
        progress=DiscoveryProgress(next_cursor=None, completed_checkpoint=DiscoveryCheckpoint(NOW)),
        now=NOW,
    )

    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_items").fetchone() == (1,)


def test_discovery_commit_rolls_back_work_and_progress_after_trigger_failure(tmp_path) -> None:
    database = tmp_path / "rollback.sqlite"
    store = SQLiteProcessingState(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER abort_selected_work_insert
            BEFORE INSERT ON work_items
            WHEN NEW.asset_id = 'asset-fail'
            BEGIN
                SELECT RAISE(ABORT, 'test-only failure');
            END;
            """
        )

    with pytest.raises(StateStoreError) as error:
        store.commit_discovery_page(
            stream_id="asset-stream",
            analysis_revision=AnalysisRevision("v1"),
            expected_state=None,
            requests=(_request("asset-good"), _request("asset-fail")),
            progress=DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None),
            now=NOW,
        )

    assert "test-only failure" not in str(error.value)
    assert (
        store.load_discovery_state(
            stream_id="asset-stream", analysis_revision=AnalysisRevision("v1")
        )
        is None
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_items").fetchone() == (0,)


def test_corrupted_persisted_discovery_timestamp_is_rejected_redacted(tmp_path) -> None:
    database = tmp_path / "corrupt.sqlite"
    store = SQLiteProcessingState(database)
    revision = AnalysisRevision("v1")
    store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=revision,
        expected_state=None,
        requests=(),
        progress=DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None),
        now=NOW,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE discovery_state SET updated_at = 'raw-invalid-value'")

    with pytest.raises(StateStoreError) as error:
        store.load_discovery_state(stream_id="asset-stream", analysis_revision=revision)

    assert "raw-invalid-value" not in str(error.value)


def test_new_analysis_revision_has_no_previous_discovery_resume_state(tmp_path) -> None:
    store = SQLiteProcessingState(tmp_path / "revision-state.sqlite")
    store.commit_discovery_page(
        stream_id="asset-stream",
        analysis_revision=AnalysisRevision("v1"),
        expected_state=None,
        requests=(),
        progress=DiscoveryProgress(next_cursor="old-cursor", completed_checkpoint=None),
        now=NOW,
    )

    assert (
        store.load_discovery_state(
            stream_id="asset-stream", analysis_revision=AnalysisRevision("v2")
        )
        is None
    )
