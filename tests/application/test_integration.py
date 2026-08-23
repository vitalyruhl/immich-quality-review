from datetime import UTC, datetime

import pytest

from immich_quality_review.application.integration import (
    DiscoveryDispatcher,
    DiscoveryPage,
    WorkRequest,
    WorkSource,
)


class FakeDiscovery:
    def __init__(self, pages: dict[str | None, DiscoveryPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, int]] = []

    def discover_page(self, *, cursor: str | None, limit: int) -> DiscoveryPage:
        self.calls.append((cursor, limit))
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
    first = WorkRequest(asset_id="asset-1", source=WorkSource.API_BACKFILL)
    second = WorkRequest(asset_id="asset-2", source=WorkSource.API_BACKFILL)
    discovery = FakeDiscovery(
        {
            None: DiscoveryPage(requests=(first,), next_cursor="cursor-1"),
            "cursor-1": DiscoveryPage(requests=(second,), next_cursor=None),
        }
    )
    sink = FakeSink()
    dispatcher = DiscoveryDispatcher(discovery=discovery, sink=sink)

    next_cursor = dispatcher.dispatch_page(cursor=None, limit=2)
    assert next_cursor == "cursor-1"
    assert discovery.calls == [(None, 2)]
    assert sink.requests == [first]

    assert dispatcher.dispatch_page(cursor=next_cursor, limit=2) is None
    assert discovery.calls == [(None, 2), ("cursor-1", 2)]
    assert sink.requests == [first, second]


def test_dispatcher_rejects_non_positive_limit_before_discovery_call() -> None:
    discovery = FakeDiscovery({})
    dispatcher = DiscoveryDispatcher(discovery=discovery, sink=FakeSink())

    with pytest.raises(ValueError):
        dispatcher.dispatch_page(cursor=None, limit=0)

    assert discovery.calls == []
