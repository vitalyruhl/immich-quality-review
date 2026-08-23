from datetime import UTC, datetime

import pytest

from immich_quality_review.application.discovery import (
    AssetDescriptor,
    AssetMediaType,
    DiscoveryCheckpoint,
    DiscoveryPage,
    DiscoveryProgress,
)
from immich_quality_review.application.integration import (
    DiscoveryDispatcher,
    WorkRequest,
    WorkSource,
)


class FakeDiscovery:
    def __init__(self, pages: dict[str | None, DiscoveryPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, object, int]] = []

    def discover_page(self, *, cursor: str | None, checkpoint: object, limit: int) -> DiscoveryPage:
        self.calls.append((cursor, checkpoint, limit))
        return self.pages[cursor]


class FakeSink:
    def __init__(self) -> None:
        self.requests: list[WorkRequest] = []

    def submit(self, request: WorkRequest) -> None:
        self.requests.append(request)


def test_work_request_accepts_api_backfill_without_event_fields() -> None:
    request = WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL)

    assert request.asset_id == "asset-1"
    assert request.event_id is None
    assert request.occurred_at is None


def test_work_request_accepts_timezone_aware_workflow_event() -> None:
    occurred_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

    request = WorkRequest(
        asset_id="asset-1",
        source=WorkSource.WORKFLOW_EVENT,
        event_id="event-1",
        occurred_at=occurred_at,
    )

    assert request.source is WorkSource.WORKFLOW_EVENT
    assert request.occurred_at == occurred_at


@pytest.mark.parametrize(
    "kwargs",
    [
        {"asset_id": "", "source": WorkSource.API_BACKFILL},
        {
            "asset_id": "asset-1",
            "source": WorkSource.API_BACKFILL,
            "event_id": "event-1",
        },
        {
            "asset_id": "asset-1",
            "source": WorkSource.WORKFLOW_EVENT,
            "occurred_at": datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        },
        {
            "asset_id": "asset-1",
            "source": WorkSource.WORKFLOW_EVENT,
            "event_id": "event-1",
            "occurred_at": datetime(2026, 8, 23, 12, 0),
        },
    ],
)
def test_work_request_rejects_invalid_source_and_event_combinations(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        WorkRequest(**kwargs)


def test_dispatcher_passes_cursor_between_bounded_pages_and_returns_next_cursor() -> None:
    first = AssetDescriptor(
        asset_id="asset-1",
        media_type=AssetMediaType.IMAGE,
        mime_type="image/jpeg",
        created_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 23, 12, 1, tzinfo=UTC),
        width=100,
        height=100,
    )
    second = AssetDescriptor(
        asset_id="asset-2",
        media_type=AssetMediaType.IMAGE,
        mime_type="image/png",
        created_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 23, 12, 1, tzinfo=UTC),
        width=None,
        height=None,
    )
    discovery = FakeDiscovery(
        {
            None: DiscoveryPage(assets=(first,), next_cursor="cursor-1", completed_checkpoint=None),
            "cursor-1": DiscoveryPage(
                assets=(second,),
                next_cursor=None,
                completed_checkpoint=DiscoveryCheckpoint(
                    completed_through=datetime(2026, 8, 23, 12, 2, tzinfo=UTC)
                ),
            ),
        }
    )
    sink = FakeSink()
    dispatcher = DiscoveryDispatcher(discovery=discovery, sink=sink)

    progress = dispatcher.dispatch_page(cursor=None, checkpoint=None, limit=2)
    assert progress == DiscoveryProgress(next_cursor="cursor-1", completed_checkpoint=None)
    assert discovery.calls == [(None, None, 2)]
    assert sink.requests == [WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL)]

    assert dispatcher.dispatch_page(
        cursor=progress.next_cursor, checkpoint=None, limit=2
    ) == DiscoveryProgress(
        next_cursor=None,
        completed_checkpoint=discovery.pages["cursor-1"].completed_checkpoint,
    )
    assert discovery.calls == [(None, None, 2), ("cursor-1", None, 2)]
    assert sink.requests == [
        WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL),
        WorkRequest(asset_id="asset-2", source=WorkSource.API_BACKFILL),
    ]


def test_dispatcher_rejects_non_positive_limit_before_discovery_call() -> None:
    discovery = FakeDiscovery({})
    dispatcher = DiscoveryDispatcher(discovery=discovery, sink=FakeSink())

    with pytest.raises(ValueError):
        dispatcher.dispatch_page(cursor=None, checkpoint=None, limit=0)

    assert discovery.calls == []


def test_dispatcher_rejects_a_page_larger_than_the_requested_limit() -> None:
    assets = tuple(
        AssetDescriptor(
            asset_id=f"asset-{index}",
            media_type=AssetMediaType.IMAGE,
            mime_type="image/jpeg",
            created_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 23, 12, 1, tzinfo=UTC),
            width=1,
            height=1,
        )
        for index in range(3)
    )
    discovery = FakeDiscovery(
        {
            None: DiscoveryPage(
                assets=assets,
                next_cursor=None,
                completed_checkpoint=DiscoveryCheckpoint(
                    completed_through=datetime(2026, 8, 23, 12, 2, tzinfo=UTC)
                ),
            )
        }
    )
    sink = FakeSink()

    with pytest.raises(ValueError):
        DiscoveryDispatcher(discovery=discovery, sink=sink).dispatch_page(
            cursor=None, checkpoint=None, limit=2
        )

    assert sink.requests == []
