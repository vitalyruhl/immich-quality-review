"""Provider-neutral ports and bounded discovery dispatch."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .discovery import DiscoveryCheckpoint, DiscoveryPage, DiscoveryProgress


class WorkSource(StrEnum):
    """The supported sources of review work."""

    API_BACKFILL = "api_backfill"
    WORKFLOW_EVENT = "workflow_event"


@dataclass(frozen=True, slots=True)
class WorkRequest:
    """A provider-neutral request to process one Immich asset."""

    asset_id: str
    source: WorkSource
    event_id: str | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be empty")
        if not isinstance(self.source, WorkSource):
            raise ValueError("source must be a WorkSource")

        if self.source is WorkSource.API_BACKFILL:
            if self.event_id is not None or self.occurred_at is not None:
                raise ValueError("API backfill requests cannot contain event fields")
            return

        if self.source is WorkSource.WORKFLOW_EVENT:
            if not isinstance(self.event_id, str) or not self.event_id.strip():
                raise ValueError("workflow events require an event_id")
            if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None:
                raise ValueError("workflow events require a timezone-aware occurred_at")
            if self.occurred_at.utcoffset() is None:
                raise ValueError("workflow events require a timezone-aware occurred_at")


@dataclass(frozen=True, slots=True)
class IntegrationCapabilities:
    """A redacted capability report for the configured integration."""

    provider_version: str
    api_compatible: bool
    authenticated: bool
    workflow_event_intake: bool
    polling_fallback: bool
    reason_code: str | None = None


class AssetDiscoveryPort(Protocol):
    def discover_page(
        self,
        *,
        cursor: str | None,
        checkpoint: DiscoveryCheckpoint | None,
        limit: int,
    ) -> DiscoveryPage:
        """Return exactly one bounded discovery page."""
        ...


class AssetContentPort(Protocol):
    def read_asset(self, asset_id: str, *, max_bytes: int) -> bytes:
        """Read bounded asset content without exposing transport details."""
        ...


class CapabilityPort(Protocol):
    def probe_capabilities(self) -> IntegrationCapabilities:
        """Report current integration capabilities."""
        ...


class ReviewSynchronizationPort(Protocol):
    def synchronize(self, asset_ids: Sequence[str]) -> None:
        """Synchronize explicitly allowed review state for asset IDs."""
        ...


class WorkSink(Protocol):
    def submit(self, request: WorkRequest) -> None:
        """Accept one normalized work request."""
        ...


class DiscoveryDispatcher:
    """Forward one bounded discovery page to the shared application sink."""

    def __init__(self, *, discovery: AssetDiscoveryPort, sink: WorkSink) -> None:
        self._discovery = discovery
        self._sink = sink

    def dispatch_page(
        self,
        *,
        cursor: str | None,
        checkpoint: DiscoveryCheckpoint | None,
        limit: int,
    ) -> DiscoveryProgress:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if cursor is not None and checkpoint is not None:
            raise ValueError("cursor and checkpoint cannot be provided together")
        page = self._discovery.discover_page(
            cursor=cursor,
            checkpoint=checkpoint,
            limit=limit,
        )
        if len(page.assets) > limit:
            raise ValueError("discovery page exceeded the requested limit")
        for asset in page.assets:
            self._sink.submit(WorkRequest(asset_id=asset.asset_id, source=WorkSource.API_BACKFILL))
        return DiscoveryProgress(
            next_cursor=page.next_cursor,
            completed_checkpoint=page.completed_checkpoint,
        )
