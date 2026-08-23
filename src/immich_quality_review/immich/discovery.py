"""Bounded, deterministic Immich asset discovery behind the API client."""

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from immich_quality_review.application.discovery import (
    AssetDescriptor,
    AssetMediaType,
    DiscoveryCheckpoint,
    DiscoveryPage,
)

from .client import ImmichClient

type AssetRecord = tuple[
    str,
    str,
    str,
    bool,
    bool,
    str,
    datetime,
    datetime,
    int | None,
    int | None,
]


class ImmichDiscoveryError(Exception):
    """Base class for redacted discovery failures."""


class DiscoveryProtocolError(ImmichDiscoveryError):
    """The bounded Immich discovery response was invalid."""

    def __init__(self) -> None:
        super().__init__("Immich discovery response was invalid.")


class DiscoveryCursorError(ImmichDiscoveryError):
    """The opaque discovery cursor was invalid or unsafe."""

    def __init__(self) -> None:
        super().__init__("Immich discovery cursor was invalid.")


@dataclass(frozen=True, slots=True)
class DiscoveryCursor:
    """The local state required to resume one fixed discovery window."""

    next_page: int
    lower: datetime | None
    upper: datetime

    def __post_init__(self) -> None:
        if isinstance(self.next_page, bool) or not isinstance(self.next_page, int):
            raise ValueError("next_page must be a positive integer")
        if self.next_page <= 0:
            raise ValueError("next_page must be a positive integer")
        _validate_cursor_datetime(self.upper)
        if self.lower is not None:
            _validate_cursor_datetime(self.lower)
            if self.lower >= self.upper:
                raise ValueError("lower cursor boundary must be before upper boundary")


class DiscoveryCursorCodec:
    """Encode and decode a small versioned URL-safe discovery cursor."""

    VERSION = 1
    MAX_LENGTH = 1024
    _ENCODED_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

    @classmethod
    def encode(cls, cursor: DiscoveryCursor) -> str:
        payload = {
            "lower": None if cursor.lower is None else _format_cursor_datetime(cursor.lower),
            "nextPage": cursor.next_page,
            "upper": _format_cursor_datetime(cursor.upper),
            "version": cls.VERSION,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        if len(encoded) > cls.MAX_LENGTH:
            raise DiscoveryCursorError
        return encoded

    @classmethod
    def decode(cls, value: str) -> DiscoveryCursor:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > cls.MAX_LENGTH
            or cls._ENCODED_PATTERN.fullmatch(value) is None
        ):
            raise DiscoveryCursorError
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(
                base64.b64decode(value + padding, altchars=b"-_", validate=True).decode("utf-8")
            )
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            raise DiscoveryCursorError from None
        if not isinstance(payload, dict) or set(payload) != {
            "lower",
            "nextPage",
            "upper",
            "version",
        }:
            raise DiscoveryCursorError
        if payload["version"] != cls.VERSION or isinstance(payload["version"], bool):
            raise DiscoveryCursorError
        next_page = payload["nextPage"]
        if isinstance(next_page, bool) or not isinstance(next_page, int) or next_page <= 0:
            raise DiscoveryCursorError
        lower = _parse_cursor_datetime(payload["lower"], allow_none=True)
        upper = _parse_cursor_datetime(payload["upper"], allow_none=False)
        if upper is None:
            raise DiscoveryCursorError
        try:
            return DiscoveryCursor(next_page=next_page, lower=lower, upper=upper)
        except ValueError:
            raise DiscoveryCursorError from None


class ImmichDiscovery:
    """Discover eligible timeline images through one fixed bounded API window."""

    MAX_LIMIT = 1000
    OVERLAP = timedelta(seconds=1)
    _REQUIRED_ITEM_FIELDS = {
        "createdAt",
        "height",
        "id",
        "isOffline",
        "isTrashed",
        "originalMimeType",
        "type",
        "updatedAt",
        "visibility",
        "width",
    }

    def __init__(self, *, client: ImmichClient, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock
        self._pending_start: tuple[DiscoveryCheckpoint | None, DiscoveryCursor] | None = None

    def discover_page(
        self,
        *,
        cursor: str | None,
        checkpoint: DiscoveryCheckpoint | None,
        limit: int,
    ) -> DiscoveryPage:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= self.MAX_LIMIT
        ):
            raise ValueError("limit must be between 1 and 1000")
        if cursor is not None and checkpoint is not None:
            raise ValueError("cursor and checkpoint cannot be provided together")
        if cursor is None:
            state = self._start_state(checkpoint)
        else:
            state = DiscoveryCursorCodec.decode(cursor)

        response = self._client.search_assets(
            page=state.next_page,
            size=limit,
            updated_after=state.lower,
            updated_before=state.upper,
        )
        assets, next_page = self._parse_response(response)
        if len(assets) > limit:
            raise DiscoveryProtocolError
        if next_page is None:
            self._pending_start = None
            return DiscoveryPage(
                assets=assets,
                next_cursor=None,
                completed_checkpoint=DiscoveryCheckpoint(completed_through=state.upper),
            )

        self._pending_start = None
        next_cursor = DiscoveryCursorCodec.encode(
            DiscoveryCursor(next_page=next_page, lower=state.lower, upper=state.upper)
        )
        return DiscoveryPage(assets=assets, next_cursor=next_cursor, completed_checkpoint=None)

    def _start_state(self, checkpoint: DiscoveryCheckpoint | None) -> DiscoveryCursor:
        if self._pending_start is not None and self._pending_start[0] == checkpoint:
            return self._pending_start[1]
        upper = self._clock()
        if not isinstance(upper, datetime) or upper.tzinfo is None or upper.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        upper = upper.astimezone(UTC)
        lower = (
            None
            if checkpoint is None
            else checkpoint.completed_through.astimezone(UTC) - self.OVERLAP
        )
        try:
            state = DiscoveryCursor(next_page=1, lower=lower, upper=upper)
        except ValueError:
            raise DiscoveryProtocolError from None
        self._pending_start = (checkpoint, state)
        return state

    @classmethod
    def _parse_response(cls, payload: object) -> tuple[tuple[AssetDescriptor, ...], int | None]:
        if not isinstance(payload, dict):
            raise DiscoveryProtocolError
        assets_payload = payload.get("assets")
        if not isinstance(assets_payload, dict):
            raise DiscoveryProtocolError
        if "items" not in assets_payload:
            raise DiscoveryProtocolError
        items = assets_payload["items"]
        if not isinstance(items, list):
            raise DiscoveryProtocolError
        next_page = assets_payload.get("nextPage")
        if next_page is not None and (
            isinstance(next_page, bool) or not isinstance(next_page, int) or next_page <= 0
        ):
            raise DiscoveryProtocolError

        records: dict[str, AssetRecord] = {}
        for item in items:
            record = cls._parse_item(item)
            asset_id = record[0]
            previous = records.get(asset_id)
            if previous is not None and previous != record:
                raise DiscoveryProtocolError
            records[asset_id] = record

        descriptors = [
            cls._to_descriptor(record) for record in records.values() if cls._is_eligible(record)
        ]
        descriptors.sort(key=lambda asset: (asset.updated_at, asset.asset_id))
        return tuple(descriptors), next_page

    @classmethod
    def _parse_item(cls, item: object) -> AssetRecord:
        if not isinstance(item, dict) or not cls._REQUIRED_ITEM_FIELDS.issubset(item):
            raise DiscoveryProtocolError
        asset_id = item["id"]
        asset_type = item["type"]
        visibility = item["visibility"]
        mime_type = item["originalMimeType"]
        if (
            not isinstance(asset_id, str)
            or not asset_id.strip()
            or not isinstance(asset_type, str)
            or not isinstance(visibility, str)
            or not isinstance(mime_type, str)
            or not mime_type.strip()
        ):
            raise DiscoveryProtocolError
        if not isinstance(item["isTrashed"], bool) or not isinstance(item["isOffline"], bool):
            raise DiscoveryProtocolError
        created_at = _parse_response_datetime(item["createdAt"])
        updated_at = _parse_response_datetime(item["updatedAt"])
        width = _parse_dimension(item["width"])
        height = _parse_dimension(item["height"])
        return (
            asset_id,
            asset_type,
            visibility,
            item["isTrashed"],
            item["isOffline"],
            mime_type,
            created_at,
            updated_at,
            width,
            height,
        )

    @staticmethod
    def _is_eligible(record: AssetRecord) -> bool:
        (
            _,
            asset_type,
            visibility,
            is_trashed,
            is_offline,
            mime_type,
            _,
            _,
            _,
            _,
        ) = record
        return (
            asset_type == "IMAGE"
            and visibility == "timeline"
            and is_trashed is False
            and is_offline is False
            and isinstance(mime_type, str)
            and mime_type.startswith("image/")
        )

    @staticmethod
    def _to_descriptor(record: AssetRecord) -> AssetDescriptor:
        (
            asset_id,
            _,
            _,
            _,
            _,
            mime_type,
            created_at,
            updated_at,
            width,
            height,
        ) = record
        try:
            return AssetDescriptor(
                asset_id=asset_id,
                media_type=AssetMediaType.IMAGE,
                mime_type=mime_type,
                created_at=created_at,
                updated_at=updated_at,
                width=width,
                height=height,
            )
        except (TypeError, ValueError):
            raise DiscoveryProtocolError from None


def _validate_cursor_datetime(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or not 1970 <= value.year <= 2100
    ):
        raise ValueError("cursor datetime is invalid")


def _format_cursor_datetime(value: datetime) -> str:
    _validate_cursor_datetime(value)
    return value.astimezone(UTC).isoformat(timespec="auto").replace("+00:00", "Z")


def _parse_cursor_datetime(value: object, *, allow_none: bool) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise DiscoveryCursorError
    try:
        parsed = _parse_response_datetime(value)
    except DiscoveryProtocolError:
        raise DiscoveryCursorError from None
    try:
        _validate_cursor_datetime(parsed)
    except ValueError:
        raise DiscoveryCursorError from None
    return parsed.astimezone(UTC)


def _parse_response_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise DiscoveryProtocolError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DiscoveryProtocolError from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DiscoveryProtocolError
    return parsed.astimezone(UTC)


def _parse_dimension(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DiscoveryProtocolError
    return value
