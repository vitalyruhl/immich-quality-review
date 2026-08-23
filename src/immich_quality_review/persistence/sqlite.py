"""SQLite implementation of the provider-neutral processing-state port."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_quality_review.application.discovery import DiscoveryCheckpoint, DiscoveryProgress
from immich_quality_review.application.integration import WorkRequest, WorkSource
from immich_quality_review.application.state import (
    AnalysisRevision,
    DiscoveryCommitOutcome,
    DiscoveryCommitResult,
    DiscoveryResumeState,
    EnqueueOutcome,
    EnqueueResult,
    FailureDisposition,
    ProcessingFailure,
    ProcessingKey,
    ProcessingStatePort,
    ProcessingStatus,
    StateConflictError,
    StateStoreError,
    WorkClaim,
    _validate_aware_datetime,
    _validate_opaque_text,
)

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 5.0
_MAX_LEASE_DURATION = timedelta(days=1)


def _timestamp(value: datetime, name: str = "timestamp") -> str:
    _validate_aware_datetime(value, name)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("persisted timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _validate_aware_datetime(parsed, "persisted timestamp")
    return parsed.astimezone(UTC)


class SQLiteProcessingState(ProcessingStatePort):
    """Persist processing state in an explicitly selected local SQLite database."""

    def __init__(self, path: str | Path) -> None:
        database = Path(path)
        if database.exists() and database.is_dir():
            raise StateStoreError("state database path must be a file")
        if not database.parent.exists() or not database.parent.is_dir():
            raise StateStoreError("state database parent must already exist")
        self.path = database
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    connection.execute("BEGIN IMMEDIATE")
                    self._create_schema(connection)
                    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                    connection.commit()
                elif version == _SCHEMA_VERSION:
                    self._verify_schema(connection)
                else:
                    raise StateStoreError("unsupported state schema version")
        except StateStoreError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise StateStoreError("state database initialization failed") from None

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE work_items (
                asset_id TEXT NOT NULL,
                analysis_revision TEXT NOT NULL,
                status TEXT NOT NULL,
                first_source TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                claim_token TEXT,
                lease_expires_at TEXT,
                failure_disposition TEXT,
                failure_reason_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (asset_id, analysis_revision),
                CHECK (status IN (
                    'pending', 'in_progress', 'succeeded',
                    'retryable_failure', 'terminal_failure'
                )),
                CHECK (first_source IN ('api_backfill', 'workflow_event')),
                CHECK (
                    (status = 'in_progress'
                        AND claim_token IS NOT NULL
                        AND lease_expires_at IS NOT NULL)
                    OR (status <> 'in_progress'
                        AND claim_token IS NULL
                        AND lease_expires_at IS NULL)
                ),
                CHECK (
                    (status IN ('retryable_failure', 'terminal_failure')
                        AND failure_disposition IS NOT NULL
                        AND failure_reason_code IS NOT NULL)
                    OR (status NOT IN ('retryable_failure', 'terminal_failure')
                        AND failure_disposition IS NULL
                        AND failure_reason_code IS NULL)
                ),
                CHECK (failure_disposition IS NULL OR failure_disposition IN ('retryable', 'terminal'))
            );

            CREATE TABLE discovery_state (
                stream_id TEXT NOT NULL,
                analysis_revision TEXT NOT NULL,
                generation INTEGER NOT NULL CHECK (generation > 0),
                cursor TEXT,
                checkpoint TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stream_id, analysis_revision),
                CHECK ((cursor IS NOT NULL) <> (checkpoint IS NOT NULL))
            );

            CREATE INDEX work_items_claim_order
                ON work_items (analysis_revision, status, lease_expires_at, created_at, asset_id);
            """
        )

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not {"work_items", "discovery_state"}.issubset(tables):
            raise StateStoreError("state schema is incomplete")

    @staticmethod
    def _key(request: WorkRequest, analysis_revision: AnalysisRevision) -> ProcessingKey:
        return ProcessingKey(request.asset_id, analysis_revision)

    @staticmethod
    def _validate_now(now: datetime) -> str:
        return _timestamp(now, "now")

    @staticmethod
    def _validate_claim_token(claim_token: str) -> None:
        _validate_opaque_text(claim_token, "claim_token", 256)

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> DiscoveryResumeState:
        try:
            checkpoint = (
                None
                if row["checkpoint"] is None
                else DiscoveryCheckpoint(_parse_timestamp(row["checkpoint"]))
            )
            return DiscoveryResumeState(
                stream_id=row["stream_id"],
                analysis_revision=AnalysisRevision(row["analysis_revision"]),
                generation=row["generation"],
                cursor=row["cursor"],
                checkpoint=checkpoint,
            )
        except (TypeError, ValueError, KeyError):
            raise StateStoreError("persisted discovery state is invalid") from None

    def load_discovery_state(
        self,
        *,
        stream_id: str,
        analysis_revision: AnalysisRevision,
    ) -> DiscoveryResumeState | None:
        _validate_opaque_text(stream_id, "stream_id", 128)
        if not isinstance(analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT stream_id, analysis_revision, generation, cursor, checkpoint, updated_at
                    FROM discovery_state
                    WHERE stream_id = ? AND analysis_revision = ?
                    """,
                    (stream_id, analysis_revision.value),
                ).fetchone()
                if row is None:
                    return None
                state = self._state_from_row(row)
                if state.stream_id != stream_id or state.analysis_revision != analysis_revision:
                    raise StateStoreError("persisted discovery state identity is invalid")
                _parse_timestamp(row["updated_at"])
                return state
        except StateStoreError:
            raise
        except (sqlite3.Error, OSError, ValueError):
            raise StateStoreError("state read failed") from None

    def enqueue_work(
        self,
        *,
        request: WorkRequest,
        analysis_revision: AnalysisRevision,
        now: datetime,
    ) -> EnqueueResult:
        if not isinstance(request, WorkRequest):
            raise ValueError("request must be a WorkRequest")
        key = self._key(request, analysis_revision)
        timestamp = self._validate_now(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                result = connection.execute(
                    """
                    INSERT INTO work_items (
                        asset_id, analysis_revision, status, first_source, attempt_count,
                        claim_token, lease_expires_at, failure_disposition,
                        failure_reason_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, ?, ?)
                    ON CONFLICT (asset_id, analysis_revision) DO NOTHING
                    """,
                    (
                        key.asset_id,
                        key.analysis_revision.value,
                        ProcessingStatus.PENDING.value,
                        request.source.value,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
                return EnqueueResult(
                    EnqueueOutcome.INSERTED if result.rowcount == 1 else EnqueueOutcome.DUPLICATE
                )
        except sqlite3.Error:
            raise StateStoreError("state enqueue failed") from None

    def claim_next(
        self,
        *,
        analysis_revision: AnalysisRevision,
        now: datetime,
        lease_duration: timedelta,
        claim_token: str,
    ) -> WorkClaim | None:
        if not isinstance(analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        now_text = self._validate_now(now)
        self._validate_claim_token(claim_token)
        if (
            not isinstance(lease_duration, timedelta)
            or lease_duration <= timedelta(0)
            or lease_duration > _MAX_LEASE_DURATION
        ):
            raise ValueError("lease_duration must be positive and at most one day")
        lease_expires_at = now + lease_duration
        lease_text = _timestamp(lease_expires_at, "lease_expires_at")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT asset_id, analysis_revision, first_source, attempt_count
                    FROM work_items
                    WHERE analysis_revision = ?
                      AND (
                          status IN (?, ?)
                          OR (status = ? AND lease_expires_at <= ?)
                      )
                    ORDER BY created_at ASC, asset_id ASC
                    LIMIT 1
                    """,
                    (
                        analysis_revision.value,
                        ProcessingStatus.PENDING.value,
                        ProcessingStatus.RETRYABLE_FAILURE.value,
                        ProcessingStatus.IN_PROGRESS.value,
                        now_text,
                    ),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                attempt = row["attempt_count"] + 1
                try:
                    source = WorkSource(row["first_source"])
                    key = ProcessingKey(row["asset_id"], analysis_revision)
                    claim = WorkClaim(key, claim_token, attempt, source, lease_expires_at)
                except (TypeError, ValueError):
                    raise StateStoreError("persisted work item is invalid") from None
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = ?, attempt_count = ?, claim_token = ?, lease_expires_at = ?,
                        failure_disposition = NULL, failure_reason_code = NULL, updated_at = ?
                    WHERE asset_id = ? AND analysis_revision = ?
                    """,
                    (
                        ProcessingStatus.IN_PROGRESS.value,
                        attempt,
                        claim_token,
                        lease_text,
                        now_text,
                        row["asset_id"],
                        row["analysis_revision"],
                    ),
                )
                connection.commit()
                return claim
        except StateStoreError:
            raise
        except sqlite3.Error:
            raise StateStoreError("state claim failed") from None

    def complete_success(
        self,
        *,
        key: ProcessingKey,
        claim_token: str,
        now: datetime,
    ) -> None:
        self._validate_key_and_claim(key, claim_token)
        now_text = self._validate_now(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._load_work_row(connection, key)
                if row is None:
                    raise StateStoreError("work item not found")
                if row["status"] == ProcessingStatus.SUCCEEDED.value:
                    connection.commit()
                    return
                if (
                    row["status"] != ProcessingStatus.IN_PROGRESS.value
                    or row["claim_token"] != claim_token
                ):
                    raise StateStoreError("claim is stale")
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = ?, claim_token = NULL, lease_expires_at = NULL,
                        failure_disposition = NULL, failure_reason_code = NULL, updated_at = ?
                    WHERE asset_id = ? AND analysis_revision = ? AND claim_token = ?
                    """,
                    (
                        ProcessingStatus.SUCCEEDED.value,
                        now_text,
                        key.asset_id,
                        key.analysis_revision.value,
                        claim_token,
                    ),
                )
                connection.commit()
        except StateStoreError:
            raise
        except sqlite3.Error:
            raise StateStoreError("state success completion failed") from None

    def complete_failure(
        self,
        *,
        key: ProcessingKey,
        claim_token: str,
        failure: ProcessingFailure,
        now: datetime,
    ) -> None:
        self._validate_key_and_claim(key, claim_token)
        if not isinstance(failure, ProcessingFailure):
            raise ValueError("failure must be a ProcessingFailure")
        now_text = self._validate_now(now)
        status = (
            ProcessingStatus.RETRYABLE_FAILURE
            if failure.disposition is FailureDisposition.RETRYABLE
            else ProcessingStatus.TERMINAL_FAILURE
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._load_work_row(connection, key)
                if row is None:
                    raise StateStoreError("work item not found")
                if (
                    row["status"] != ProcessingStatus.IN_PROGRESS.value
                    or row["claim_token"] != claim_token
                ):
                    raise StateStoreError("claim is stale")
                connection.execute(
                    """
                    UPDATE work_items
                    SET status = ?, claim_token = NULL, lease_expires_at = NULL,
                        failure_disposition = ?, failure_reason_code = ?, updated_at = ?
                    WHERE asset_id = ? AND analysis_revision = ? AND claim_token = ?
                    """,
                    (
                        status.value,
                        failure.disposition.value,
                        failure.reason_code,
                        now_text,
                        key.asset_id,
                        key.analysis_revision.value,
                        claim_token,
                    ),
                )
                connection.commit()
        except StateStoreError:
            raise
        except sqlite3.Error:
            raise StateStoreError("state failure completion failed") from None

    @staticmethod
    def _validate_key_and_claim(key: ProcessingKey, claim_token: str) -> None:
        if not isinstance(key, ProcessingKey):
            raise ValueError("key must be a ProcessingKey")
        SQLiteProcessingState._validate_claim_token(claim_token)

    @staticmethod
    def _load_work_row(connection: sqlite3.Connection, key: ProcessingKey) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT asset_id, analysis_revision, status, claim_token
            FROM work_items
            WHERE asset_id = ? AND analysis_revision = ?
            """,
            (key.asset_id, key.analysis_revision.value),
        ).fetchone()

    def commit_discovery_page(
        self,
        *,
        stream_id: str,
        analysis_revision: AnalysisRevision,
        expected_state: DiscoveryResumeState | None,
        requests: tuple[WorkRequest, ...],
        progress: DiscoveryProgress,
        now: datetime,
    ) -> DiscoveryCommitResult:
        _validate_opaque_text(stream_id, "stream_id", 128)
        if not isinstance(analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        if expected_state is not None:
            if not isinstance(expected_state, DiscoveryResumeState):
                raise ValueError("expected_state must be a DiscoveryResumeState")
            if (
                expected_state.stream_id != stream_id
                or expected_state.analysis_revision != analysis_revision
            ):
                raise ValueError("expected_state identity does not match the request")
        if not isinstance(requests, tuple) or not all(
            isinstance(request, WorkRequest) for request in requests
        ):
            raise ValueError("requests must be a tuple of WorkRequest values")
        for request in requests:
            self._key(request, analysis_revision)
        if not isinstance(progress, DiscoveryProgress):
            raise ValueError("progress must be a DiscoveryProgress")
        timestamp = self._validate_now(now)
        desired_state = DiscoveryResumeState(
            stream_id=stream_id,
            analysis_revision=analysis_revision,
            generation=1 if expected_state is None else expected_state.generation + 1,
            cursor=progress.next_cursor,
            checkpoint=progress.completed_checkpoint,
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_row = connection.execute(
                    """
                    SELECT stream_id, analysis_revision, generation, cursor, checkpoint, updated_at
                    FROM discovery_state
                    WHERE stream_id = ? AND analysis_revision = ?
                    """,
                    (stream_id, analysis_revision.value),
                ).fetchone()
                current_state = None if current_row is None else self._state_from_row(current_row)
                if current_state is not None and current_state == desired_state:
                    connection.rollback()
                    return DiscoveryCommitResult(DiscoveryCommitOutcome.REPLAYED, current_state)
                if current_state is None:
                    if expected_state is not None:
                        raise StateConflictError("discovery state conflict")
                elif expected_state is None or current_state != expected_state:
                    raise StateConflictError("discovery state conflict")

                for request in requests:
                    key = self._key(request, analysis_revision)
                    connection.execute(
                        """
                        INSERT INTO work_items (
                            asset_id, analysis_revision, status, first_source, attempt_count,
                            claim_token, lease_expires_at, failure_disposition,
                            failure_reason_code, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, ?, ?)
                        ON CONFLICT (asset_id, analysis_revision) DO NOTHING
                        """,
                        (
                            key.asset_id,
                            key.analysis_revision.value,
                            ProcessingStatus.PENDING.value,
                            request.source.value,
                            timestamp,
                            timestamp,
                        ),
                    )
                if current_state is None:
                    connection.execute(
                        """
                        INSERT INTO discovery_state (
                            stream_id, analysis_revision, generation, cursor, checkpoint, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            desired_state.stream_id,
                            desired_state.analysis_revision.value,
                            desired_state.generation,
                            desired_state.cursor,
                            None
                            if desired_state.checkpoint is None
                            else _timestamp(desired_state.checkpoint.completed_through),
                            timestamp,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE discovery_state
                        SET generation = ?, cursor = ?, checkpoint = ?, updated_at = ?
                        WHERE stream_id = ? AND analysis_revision = ? AND generation = ?
                        """,
                        (
                            desired_state.generation,
                            desired_state.cursor,
                            None
                            if desired_state.checkpoint is None
                            else _timestamp(desired_state.checkpoint.completed_through),
                            timestamp,
                            stream_id,
                            analysis_revision.value,
                            current_state.generation,
                        ),
                    )
                connection.commit()
                return DiscoveryCommitResult(DiscoveryCommitOutcome.COMMITTED, desired_state)
        except StateConflictError:
            raise
        except StateStoreError:
            raise
        except sqlite3.Error:
            raise StateStoreError("state discovery commit failed") from None
