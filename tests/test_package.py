"""Bootstrap-level package checks."""

from immich_quality_review import __version__
from immich_quality_review.application import (
    AssetDescriptor,
    AssetMediaType,
    DiscoveryCheckpoint,
    DiscoveryPage,
    DiscoveryProgress,
)
from immich_quality_review.immich import (
    DiscoveryCursor,
    DiscoveryCursorCodec,
    DiscoveryCursorError,
    DiscoveryProtocolError,
    ImmichDiscovery,
)


def test_package_exposes_version() -> None:
    assert __version__ == "0.3.0"


def test_discovery_contracts_are_publicly_exported() -> None:
    assert AssetMediaType.IMAGE.value == "image"
    assert all(
        exported is not None
        for exported in (
            AssetDescriptor,
            DiscoveryCheckpoint,
            DiscoveryPage,
            DiscoveryProgress,
            DiscoveryCursor,
            DiscoveryCursorCodec,
            DiscoveryCursorError,
            DiscoveryProtocolError,
            ImmichDiscovery,
        )
    )
