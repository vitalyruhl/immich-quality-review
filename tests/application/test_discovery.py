from datetime import UTC, datetime

import pytest

from immich_quality_review.application.discovery import (
    AssetDescriptor,
    AssetMediaType,
    DiscoveryCheckpoint,
    DiscoveryPage,
    DiscoveryProgress,
)


def descriptor(
    *,
    asset_id: str = "asset-1",
    mime_type: str = "image/jpeg",
    created_at: datetime = datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    updated_at: datetime = datetime(2026, 8, 23, 12, 1, tzinfo=UTC),
    width: int | None = 1920,
    height: int | None = 1080,
) -> AssetDescriptor:
    return AssetDescriptor(
        asset_id=asset_id,
        media_type=AssetMediaType.IMAGE,
        mime_type=mime_type,
        created_at=created_at,
        updated_at=updated_at,
        width=width,
        height=height,
    )


def checkpoint() -> DiscoveryCheckpoint:
    return DiscoveryCheckpoint(completed_through=datetime(2026, 8, 23, 12, 2, tzinfo=UTC))


def test_asset_descriptor_is_immutable_and_accepts_optional_positive_dimensions() -> None:
    result = descriptor(width=None, height=1)

    assert result.asset_id == "asset-1"
    assert result.media_type is AssetMediaType.IMAGE
    assert result.width is None
    with pytest.raises(AttributeError):
        result.asset_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"asset_id": ""},
        {"mime_type": ""},
        {"created_at": datetime(2026, 8, 23, 12, 0)},
        {"updated_at": datetime(2026, 8, 23, 12, 1)},
        {"width": 0},
        {"height": -1},
        {"width": True},
    ],
)
def test_asset_descriptor_rejects_invalid_required_fields(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        descriptor(**kwargs)


def test_checkpoint_requires_a_timezone_aware_completion_time() -> None:
    with pytest.raises(ValueError):
        DiscoveryCheckpoint(completed_through=datetime(2026, 8, 23, 12, 2))


def test_non_terminal_page_requires_cursor_without_checkpoint() -> None:
    page = DiscoveryPage(assets=(descriptor(),), next_cursor="cursor-v1", completed_checkpoint=None)

    assert page.next_cursor == "cursor-v1"
    assert page.completed_checkpoint is None


def test_terminal_page_requires_checkpoint_without_cursor() -> None:
    page = DiscoveryPage(assets=(), next_cursor=None, completed_checkpoint=checkpoint())

    assert page.completed_checkpoint == checkpoint()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"assets": (), "next_cursor": None, "completed_checkpoint": None},
        {
            "assets": (),
            "next_cursor": "cursor-v1",
            "completed_checkpoint": checkpoint(),
        },
        {
            "assets": (),
            "next_cursor": None,
            "completed_checkpoint": None,
        },
    ],
)
def test_page_rejects_invalid_terminal_state(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DiscoveryPage(**kwargs)


def test_progress_matches_terminal_and_non_terminal_page_state() -> None:
    assert DiscoveryProgress(next_cursor="cursor-v1", completed_checkpoint=None)
    assert DiscoveryProgress(next_cursor=None, completed_checkpoint=checkpoint())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"next_cursor": "", "completed_checkpoint": None},
        {"next_cursor": None, "completed_checkpoint": None},
        {"next_cursor": "cursor-v1", "completed_checkpoint": checkpoint()},
    ],
)
def test_progress_rejects_invalid_state(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DiscoveryProgress(**kwargs)
