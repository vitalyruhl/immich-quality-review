"""Durable provider-neutral processing-state adapters."""

from .sqlite import SQLiteProcessingState

__all__ = ["SQLiteProcessingState"]
