"""Provider-neutral durable processing-state contracts."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .discovery import DiscoveryCheckpoint, DiscoveryProgress
from .integration import AssetDiscoveryPort, WorkRequest, WorkSource

_REASON_CODE = re.compile(r"^[a-z0-9_.-]{1,64}$")
_MAX_CURSOR_LENGTH = 4096
_MAX_CLAIM_TOKEN_LENGTH = 256


def _validate_opaque_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty bounded string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{name} must not contain control characters")


def _validate_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AnalysisRevision:
    """Opaque analysis version used to isolate processing state."""

    value: str

    def __post_init__(self) -> None:
        _validate_opaque_text(self.value, "analysis_revision", 128)


@dataclass(frozen=True, slots=True)
class ProcessingKey:
    """Stable processing identity for one asset and analysis revision."""

    asset_id: str
    analysis_revision: AnalysisRevision

    def __post_init__(self) -> None:
        _validate_opaque_text(self.asset_id, "asset_id", 256)
        if not isinstance(self.analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")


class ProcessingStatus(StrEnum):
    """Durable lifecycle states for one processing key."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"


class FailureDisposition(StrEnum):
    """Whether a processing failure may be attempted again."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    """A safe, stable failure classification without exception text."""

    disposition: FailureDisposition
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, FailureDisposition):
            raise ValueError("disposition must be a FailureDisposition")
        if (
            not isinstance(self.reason_code, str)
            or _REASON_CODE.fullmatch(self.reason_code) is None
        ):
            raise ValueError("reason_code must be a stable lowercase identifier")


@dataclass(frozen=True, slots=True)
class WorkClaim:
    """A bounded lease for one atomically claimed work item."""

    key: ProcessingKey
    claim_token: str
    attempt: int
    source: WorkSource
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.key, ProcessingKey):
            raise ValueError("key must be a ProcessingKey")
        _validate_opaque_text(self.claim_token, "claim_token", _MAX_CLAIM_TOKEN_LENGTH)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt <= 0:
            raise ValueError("attempt must be positive")
        if not isinstance(self.source, WorkSource):
            raise ValueError("source must be a WorkSource")
        _validate_aware_datetime(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True, slots=True)
class DiscoveryResumeState:
    """Opaque resume state keyed by stream and analysis revision."""

    stream_id: str
    analysis_revision: AnalysisRevision
    generation: int
    cursor: str | None
    checkpoint: DiscoveryCheckpoint | None

    def __post_init__(self) -> None:
        _validate_opaque_text(self.stream_id, "stream_id", 128)
        if not isinstance(self.analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation <= 0
        ):
            raise ValueError("generation must be positive")
        if self.cursor is not None:
            _validate_opaque_text(self.cursor, "cursor", _MAX_CURSOR_LENGTH)
        if self.checkpoint is not None and not isinstance(self.checkpoint, DiscoveryCheckpoint):
            raise ValueError("checkpoint must be a DiscoveryCheckpoint")
        if (self.cursor is None) == (self.checkpoint is None):
            raise ValueError("exactly one of cursor and checkpoint must be present")


class EnqueueOutcome(StrEnum):
    """Result of inserting provider-neutral work."""

    INSERTED = "inserted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Whether enqueue created a row or found existing work."""

    outcome: EnqueueOutcome


class DiscoveryCommitOutcome(StrEnum):
    """Result of atomically committing one discovery transition."""

    COMMITTED = "committed"
    REPLAYED = "replayed"


@dataclass(frozen=True, slots=True)
class DiscoveryCommitResult:
    """Committed or idempotently replayed discovery state."""

    outcome: DiscoveryCommitOutcome
    state: DiscoveryResumeState


class StateStoreError(RuntimeError):
    """Redacted persistence failure."""


class StateConflictError(StateStoreError):
    """A discovery transition was based on stale incompatible state."""


class ProcessingStatePort(Protocol):
    """Provider-neutral persistence boundary for durable processing state."""

    def load_discovery_state(
        self,
        *,
        stream_id: str,
        analysis_revision: AnalysisRevision,
    ) -> DiscoveryResumeState | None: ...

    def commit_discovery_page(
        self,
        *,
        stream_id: str,
        analysis_revision: AnalysisRevision,
        expected_state: DiscoveryResumeState | None,
        requests: tuple[WorkRequest, ...],
        progress: DiscoveryProgress,
        now: datetime,
    ) -> DiscoveryCommitResult: ...

    def enqueue_work(
        self,
        *,
        request: WorkRequest,
        analysis_revision: AnalysisRevision,
        now: datetime,
    ) -> EnqueueResult: ...

    def claim_next(
        self,
        *,
        analysis_revision: AnalysisRevision,
        now: datetime,
        lease_duration: timedelta,
        claim_token: str,
    ) -> WorkClaim | None: ...

    def complete_success(
        self,
        *,
        key: ProcessingKey,
        claim_token: str,
        now: datetime,
    ) -> None: ...

    def complete_failure(
        self,
        *,
        key: ProcessingKey,
        claim_token: str,
        failure: ProcessingFailure,
        now: datetime,
    ) -> None: ...


class DurableWorkSink:
    """Persist event or API work through one revision-aware enqueue boundary."""

    def __init__(
        self,
        *,
        storage: ProcessingStatePort,
        analysis_revision: AnalysisRevision,
        now: Callable[[], datetime],
    ) -> None:
        if not isinstance(analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        self._storage = storage
        self._analysis_revision = analysis_revision
        self._now = now

    def submit(self, request: WorkRequest) -> EnqueueResult:
        if not isinstance(request, WorkRequest):
            raise ValueError("request must be a WorkRequest")
        return self._storage.enqueue_work(
            request=request,
            analysis_revision=self._analysis_revision,
            now=self._now(),
        )


class DurableDiscoveryCoordinator:
    """Commit one bounded discovery page with its durable resume transition."""

    def __init__(
        self,
        *,
        discovery: AssetDiscoveryPort,
        storage: ProcessingStatePort,
        stream_id: str,
        analysis_revision: AnalysisRevision,
        now: Callable[[], datetime],
    ) -> None:
        if not isinstance(analysis_revision, AnalysisRevision):
            raise ValueError("analysis_revision must be an AnalysisRevision")
        self._discovery = discovery
        self._storage = storage
        self._stream_id = stream_id
        self._analysis_revision = analysis_revision
        self._now = now

    def discover_page(self, *, limit: int) -> DiscoveryCommitResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        expected_state = self._storage.load_discovery_state(
            stream_id=self._stream_id,
            analysis_revision=self._analysis_revision,
        )
        cursor = None if expected_state is None else expected_state.cursor
        checkpoint = None if expected_state is None else expected_state.checkpoint
        page = self._discovery.discover_page(
            cursor=cursor,
            checkpoint=checkpoint,
            limit=limit,
        )
        requests = tuple(
            WorkRequest(asset_id=asset.asset_id, source=WorkSource.API_BACKFILL)
            for asset in page.assets
        )
        progress = DiscoveryProgress(
            next_cursor=page.next_cursor,
            completed_checkpoint=page.completed_checkpoint,
        )
        return self._storage.commit_discovery_page(
            stream_id=self._stream_id,
            analysis_revision=self._analysis_revision,
            expected_state=expected_state,
            requests=requests,
            progress=progress,
            now=self._now(),
        )
