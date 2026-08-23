"""Authenticated, bounded workflow-event intake without durable replay state."""

import hmac
import json
from datetime import datetime, timedelta
from typing import Protocol

from immich_quality_review.application.integration import WorkRequest, WorkSink, WorkSource


class EventAuthenticationError(Exception):
    """The bridge credential was rejected."""

    def __init__(self) -> None:
        super().__init__("Workflow event authentication failed.")


class EventPayloadError(Exception):
    """The bounded event payload was invalid."""

    def __init__(self) -> None:
        super().__init__("Workflow event payload was invalid.")


class EventFreshnessError(Exception):
    """The event timestamp was outside the accepted window."""

    def __init__(self) -> None:
        super().__init__("Workflow event timestamp was outside the allowed window.")


class EventReplayError(Exception):
    """The event ID was already claimed by the injected replay guard."""

    def __init__(self) -> None:
        super().__init__("Workflow event was already claimed.")


class EventAuthenticator(Protocol):
    def authenticate(self, credential: str) -> bool:
        """Validate a bridge-specific runtime credential."""
        ...


class StaticSecretAuthenticator:
    """Compare an injected bridge secret without exposing it in object text."""

    MIN_SECRET_LENGTH = 16

    def __init__(self, secret: str) -> None:
        if not isinstance(secret, str) or len(secret) < self.MIN_SECRET_LENGTH:
            raise ValueError("event secret is too short")
        self._secret = secret

    def authenticate(self, credential: str) -> bool:
        return isinstance(credential, str) and hmac.compare_digest(self._secret, credential)

    def __repr__(self) -> str:
        return "<StaticSecretAuthenticator>"


class ReplayGuard(Protocol):
    def claim(self, event_id: str, expires_at: datetime) -> bool:
        """Atomically claim an event ID until its expiry."""
        ...


class WorkflowEventIntake:
    """Normalize and enqueue one authenticated workflow event."""

    def __init__(
        self,
        *,
        authenticator: EventAuthenticator,
        replay_guard: ReplayGuard,
        sink: WorkSink,
        now: datetime,
        max_age: timedelta = timedelta(minutes=5),
        max_future_skew: timedelta = timedelta(seconds=30),
        max_payload_bytes: int = 4096,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if max_age <= timedelta(0) or max_future_skew < timedelta(0):
            raise ValueError("freshness windows must be non-negative")
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._authenticator = authenticator
        self._replay_guard = replay_guard
        self._sink = sink
        self._now = now
        self._max_age = max_age
        self._max_future_skew = max_future_skew
        self._max_payload_bytes = max_payload_bytes

    def accept(self, raw_payload: bytes, *, credential: str) -> WorkRequest:
        if not self._authenticator.authenticate(credential):
            raise EventAuthenticationError
        if not isinstance(raw_payload, bytes) or len(raw_payload) > self._max_payload_bytes:
            raise EventPayloadError
        payload = self._parse_payload(raw_payload)
        event_id = self._bounded_identifier(payload, "eventId")
        asset_id = self._bounded_identifier(payload, "assetId")
        occurred_at = self._parse_timestamp(payload)
        if (
            occurred_at < self._now - self._max_age
            or occurred_at > self._now + self._max_future_skew
        ):
            raise EventFreshnessError

        expires_at = occurred_at + self._max_age
        if not self._replay_guard.claim(event_id, expires_at):
            raise EventReplayError
        request = WorkRequest(
            asset_id=asset_id,
            source=WorkSource.WORKFLOW_EVENT,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        self._sink.submit(request)
        return request

    @staticmethod
    def _parse_payload(raw_payload: bytes) -> dict[str, object]:
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EventPayloadError from None
        if not isinstance(payload, dict) or set(payload) != {"eventId", "assetId", "occurredAt"}:
            raise EventPayloadError
        return payload

    @staticmethod
    def _bounded_identifier(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise EventPayloadError
        return value

    @staticmethod
    def _parse_timestamp(payload: dict[str, object]) -> datetime:
        value = payload.get("occurredAt")
        if not isinstance(value, str):
            raise EventPayloadError
        try:
            occurred_at = datetime.fromisoformat(value)
        except ValueError:
            raise EventPayloadError from None
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise EventPayloadError
        return occurred_at
