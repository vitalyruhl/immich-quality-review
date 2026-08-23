# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - Unreleased

### Added

- Provider-neutral integration ports for bounded review work.
- A version-aware Immich v3 API capability client.
- Authenticated, bounded workflow-event intake with freshness and replay guards.
- Bounded initial and incremental timeline-image discovery with resumable cursors.
- Provider-neutral durable processing state keyed by asset identity and analysis revision.
- SQLite schema version 1 with atomic discovery-page commits, restart-safe leases,
  retryable and terminal failure states, and cross-source deduplication.

## [0.1.0] - 2026-08-23

### Added

- Initial open-source project bootstrap.
