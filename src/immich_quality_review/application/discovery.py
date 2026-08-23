"""Provider-neutral bounded asset-discovery value types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AssetMediaType(StrEnum):
    """Supported provider-neutral asset media categories."""

    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class AssetDescriptor:
    """One eligible asset with the metadata required for downstream work."""

    asset_id: str
    media_type: AssetMediaType
    mime_type: str
    created_at: datetime
    updated_at: datetime
    width: int | None
    height: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be empty")
        if not isinstance(self.media_type, AssetMediaType):
            raise ValueError("media_type must be an AssetMediaType")
        if not isinstance(self.mime_type, str) or not self.mime_type.strip():
            raise ValueError("mime_type must not be empty")
        self._validate_datetime(self.created_at, "created_at")
        self._validate_datetime(self.updated_at, "updated_at")
        self._validate_dimension(self.width, "width")
        self._validate_dimension(self.height, "height")

    @staticmethod
    def _validate_datetime(value: object, name: str) -> None:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")

    @staticmethod
    def _validate_dimension(value: object, name: str) -> None:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(f"{name} must be positive when provided")


@dataclass(frozen=True, slots=True)
class DiscoveryCheckpoint:
    """The fixed upper boundary completed by a terminal discovery page."""

    completed_through: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.completed_through, datetime)
            or self.completed_through.tzinfo is None
            or self.completed_through.utcoffset() is None
        ):
            raise ValueError("completed_through must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DiscoveryPage:
    """One bounded page and its terminal or resumable traversal state."""

    assets: tuple[AssetDescriptor, ...]
    next_cursor: str | None
    completed_checkpoint: DiscoveryCheckpoint | None

    def __post_init__(self) -> None:
        if not isinstance(self.assets, tuple) or not all(
            isinstance(asset, AssetDescriptor) for asset in self.assets
        ):
            raise ValueError("assets must be a tuple of AssetDescriptor values")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise ValueError("next_cursor must be non-empty when provided")
        if self.next_cursor is None:
            if not isinstance(self.completed_checkpoint, DiscoveryCheckpoint):
                raise ValueError("terminal pages require a completed checkpoint")
        elif self.completed_checkpoint is not None:
            raise ValueError("non-terminal pages cannot contain a completed checkpoint")


@dataclass(frozen=True, slots=True)
class DiscoveryProgress:
    """The resumable progress returned after dispatching one discovery page."""

    next_cursor: str | None
    completed_checkpoint: DiscoveryCheckpoint | None

    def __post_init__(self) -> None:
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise ValueError("next_cursor must be non-empty when provided")
        if self.next_cursor is None:
            if not isinstance(self.completed_checkpoint, DiscoveryCheckpoint):
                raise ValueError("terminal progress requires a completed checkpoint")
        elif self.completed_checkpoint is not None:
            raise ValueError("non-terminal progress cannot contain a completed checkpoint")
