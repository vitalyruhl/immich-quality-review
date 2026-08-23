import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from immich_quality_review.application.discovery import (
    AssetMediaType,
    DiscoveryCheckpoint,
)
from immich_quality_review.immich.discovery import (
    DiscoveryCursor,
    DiscoveryCursorCodec,
    DiscoveryCursorError,
    DiscoveryProtocolError,
    ImmichDiscovery,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, responses: list[dict[str, object] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def search_assets(
        self,
        *,
        page: int,
        size: int,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "page": page,
                "size": size,
                "updated_after": updated_after,
                "updated_before": updated_before,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def item(
    *,
    asset_id: str = "asset-1",
    asset_type: str = "IMAGE",
    visibility: str = "timeline",
    is_trashed: bool = False,
    is_offline: bool = False,
    mime_type: str = "image/jpeg",
    created_at: str = "2026-08-23T11:00:00Z",
    updated_at: str = "2026-08-23T11:01:00Z",
    width: int | None = 100,
    height: int | None = 80,
) -> dict[str, object]:
    return {
        "id": asset_id,
        "type": asset_type,
        "visibility": visibility,
        "isTrashed": is_trashed,
        "isOffline": is_offline,
        "originalMimeType": mime_type,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "width": width,
        "height": height,
    }


def search_response(items: list[dict[str, object]], next_page: object = None) -> dict[str, object]:
    return {"assets": {"items": items, "nextPage": next_page}}


def make_discovery(client: FakeClient) -> ImmichDiscovery:
    return ImmichDiscovery(client=client, clock=lambda: NOW)


def test_initial_discovery_captures_fixed_upper_boundary_and_returns_terminal_checkpoint() -> None:
    client = FakeClient([search_response([item()])])

    page = make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=100)

    assert page.next_cursor is None
    assert page.completed_checkpoint == DiscoveryCheckpoint(completed_through=NOW)
    assert page.assets[0].media_type is AssetMediaType.IMAGE
    assert client.calls == [
        {
            "page": 1,
            "size": 100,
            "updated_after": None,
            "updated_before": NOW,
        }
    ]


def test_discovery_filters_ineligible_assets_and_sorts_eligible_assets_deterministically() -> None:
    client = FakeClient(
        [
            search_response(
                [
                    item(asset_id="asset-b", updated_at="2026-08-23T11:02:00Z"),
                    item(asset_id="asset-a", updated_at="2026-08-23T11:02:00Z"),
                    item(asset_id="video", asset_type="VIDEO"),
                    item(asset_id="archived", visibility="archive"),
                    item(asset_id="trashed", is_trashed=True),
                    item(asset_id="offline", is_offline=True),
                    item(asset_id="other-mime", mime_type="video/mp4"),
                ]
            )
        ]
    )

    page = make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=100)

    assert [asset.asset_id for asset in page.assets] == ["asset-a", "asset-b"]
    assert all(asset.media_type is AssetMediaType.IMAGE for asset in page.assets)


def test_discovery_reuses_the_same_window_across_multiple_pages() -> None:
    client = FakeClient(
        [
            search_response([item(asset_id="asset-1")], next_page=2),
            search_response([item(asset_id="asset-2")]),
        ]
    )
    discovery = make_discovery(client)

    first = discovery.discover_page(cursor=None, checkpoint=None, limit=2)
    second = discovery.discover_page(cursor=first.next_cursor, checkpoint=None, limit=2)

    assert second.completed_checkpoint == DiscoveryCheckpoint(completed_through=NOW)
    assert client.calls[0]["updated_before"] == NOW
    assert client.calls[1]["updated_before"] == NOW
    assert client.calls[1]["page"] == 2


def test_empty_non_terminal_page_is_valid() -> None:
    client = FakeClient([search_response([], next_page=2)])

    page = make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=10)

    assert page.assets == ()
    assert page.next_cursor is not None
    assert page.completed_checkpoint is None


def test_missing_next_page_marks_an_empty_response_terminal() -> None:
    client = FakeClient([{"assets": {"items": []}}])

    page = make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=10)

    assert page.next_cursor is None
    assert page.completed_checkpoint == DiscoveryCheckpoint(completed_through=NOW)


def test_response_cannot_return_more_eligible_assets_than_requested() -> None:
    client = FakeClient([search_response([item(asset_id="asset-1"), item(asset_id="asset-2")])])

    with pytest.raises(DiscoveryProtocolError):
        make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=1)


@pytest.mark.parametrize("next_page", [0, -1, True, "2"])
def test_invalid_next_page_is_a_redacted_protocol_error(next_page: object) -> None:
    client = FakeClient([search_response([], next_page=next_page)])

    with pytest.raises(DiscoveryProtocolError) as exc_info:
        make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=10)

    assert str(exc_info.value) == "Immich discovery response was invalid."


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"assets": []},
        {"assets": {"items": {}, "nextPage": None}},
        {"assets": {"items": [{"id": "asset-1"}], "nextPage": None}},
    ],
)
def test_malformed_required_response_fields_are_protocol_errors(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DiscoveryProtocolError):
        make_discovery(FakeClient([payload])).discover_page(cursor=None, checkpoint=None, limit=10)


def test_duplicate_identical_assets_are_emitted_once() -> None:
    duplicate = item()
    client = FakeClient([search_response([duplicate, dict(duplicate)])])

    page = make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=10)

    assert [asset.asset_id for asset in page.assets] == ["asset-1"]


def test_conflicting_duplicate_metadata_is_a_protocol_error() -> None:
    client = FakeClient([search_response([item(), item(updated_at="2026-08-23T11:02:00Z")])])

    with pytest.raises(DiscoveryProtocolError):
        make_discovery(client).discover_page(cursor=None, checkpoint=None, limit=10)


def test_incremental_discovery_uses_one_second_overlap() -> None:
    completed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    client = FakeClient([search_response([])])

    make_discovery(client).discover_page(
        cursor=None,
        checkpoint=DiscoveryCheckpoint(completed_through=completed),
        limit=10,
    )

    assert client.calls[0]["updated_after"] == completed - timedelta(seconds=1)


def test_cursor_round_trip_preserves_fixed_window() -> None:
    cursor = DiscoveryCursor(
        next_page=7,
        lower=datetime(2026, 8, 23, 11, 59, 59, tzinfo=UTC),
        upper=NOW,
    )

    encoded = DiscoveryCursorCodec.encode(cursor)

    assert DiscoveryCursorCodec.decode(encoded) == cursor
    assert len(encoded) <= DiscoveryCursorCodec.MAX_LENGTH


def test_cursor_can_resume_with_a_fresh_adapter_instance() -> None:
    first_client = FakeClient([search_response([], next_page=2)])
    first_page = make_discovery(first_client).discover_page(cursor=None, checkpoint=None, limit=10)
    second_client = FakeClient([search_response([])])

    second_page = make_discovery(second_client).discover_page(
        cursor=first_page.next_cursor, checkpoint=None, limit=10
    )

    assert second_page.completed_checkpoint == DiscoveryCheckpoint(completed_through=NOW)
    assert second_client.calls[0]["page"] == 2
    assert second_client.calls[0]["updated_before"] == NOW


def cursor_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "lower": None,
        "nextPage": 2,
        "upper": "2026-08-23T12:00:00Z",
        "version": 1,
    }
    payload.update(overrides)
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )


@pytest.mark.parametrize(
    "cursor",
    [
        cursor_payload(version=2),
        cursor_payload(extra="not-accepted"),
        cursor_payload(nextPage=None),
        cursor_payload(upper="2026-08-23T12:00:00"),
        cursor_payload(lower="2026-08-23T12:00:01Z"),
        cursor_payload(upper="0001-01-01T00:00:00Z"),
        "A" * 1025,
    ],
)
def test_malformed_cursor_is_rejected_without_leaking_cursor_data(cursor: str) -> None:
    with pytest.raises(DiscoveryCursorError) as exc_info:
        DiscoveryCursorCodec.decode(cursor)

    assert str(exc_info.value) == "Immich discovery cursor was invalid."
    assert cursor not in str(exc_info.value)


def test_cursor_and_checkpoint_cannot_be_combined() -> None:
    client = FakeClient([search_response([])])
    cursor = DiscoveryCursorCodec.encode(DiscoveryCursor(next_page=2, lower=None, upper=NOW))

    with pytest.raises(ValueError):
        make_discovery(client).discover_page(
            cursor=cursor,
            checkpoint=DiscoveryCheckpoint(completed_through=NOW - timedelta(minutes=1)),
            limit=10,
        )


def test_failed_start_retries_with_the_identical_fixed_window() -> None:
    client = FakeClient([RuntimeError("private response and asset metadata"), search_response([])])
    discovery = make_discovery(client)

    with pytest.raises(RuntimeError):
        discovery.discover_page(cursor=None, checkpoint=None, limit=10)
    page = discovery.discover_page(cursor=None, checkpoint=None, limit=10)

    assert page.completed_checkpoint == DiscoveryCheckpoint(completed_through=NOW)
    assert client.calls[0]["updated_before"] == client.calls[1]["updated_before"] == NOW


def test_checkpoint_is_not_advanced_when_a_page_fails() -> None:
    completed = DiscoveryCheckpoint(completed_through=datetime(2026, 8, 23, 11, 0, tzinfo=UTC))
    client = FakeClient([RuntimeError("temporary failure")])

    with pytest.raises(RuntimeError):
        make_discovery(client).discover_page(cursor=None, checkpoint=completed, limit=10)

    assert client.calls[0]["updated_after"] == completed.completed_through - timedelta(seconds=1)
