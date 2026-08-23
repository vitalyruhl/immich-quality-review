# Architecture direction

The primary product boundary is a standalone Python worker, not an Immich
server extension. It uses the documented Immich API for authentication,
backfill, pagination, asset data, compatibility fallback, and explicitly
supported review synchronization. An optional event-intake adapter may consume
normalized hints from Immich v3 Workflows/plugins when the target instance
explicitly exposes compatible capabilities.

The workflow/plugin adapter is a transport boundary only. It must not make
quality, persistence, or deletion decisions, and the worker remains usable
without the plugin SDK, WebAssembly runtime, or enabled external plugins. See
[ADR 0001](decisions/0001-immich-v3-integration-strategy.md) for the accepted
decision and upstream evidence.

## Core flow

1. API backfill/polling and, optionally, an event-intake adapter produce the same
   provider-neutral work request.
2. A future application boundary will deduplicate work and persist a resume
   cursor behind an injected state port.
3. The worker obtains an image representation suitable for analysis.
4. A quality orchestrator asks enabled providers for metric observations.
5. A scoring layer turns observations into explainable review candidates.
6. An Immich output adapter creates or updates a review destination without
   deleting assets.

## Implemented integration contracts

The application-facing boundary is defined by small Python protocols:

- `AssetDiscoveryPort` returns one bounded `DiscoveryPage` at a time.
- `AssetContentPort` reads asset content with a caller-provided byte limit.
- `CapabilityPort` reports `IntegrationCapabilities`.
- `ReviewSynchronizationPort` accepts only explicitly allowed review
  synchronization for a sequence of asset IDs.
- `WorkSink` accepts a normalized `WorkRequest` from either
  `WorkSource.API_BACKFILL` or `WorkSource.WORKFLOW_EVENT`.

`DiscoveryDispatcher` forwards one page to the same `WorkSink` used by event
intake and returns only the next cursor. These contracts expose no delete,
trash, hide, or source-mutation operation.

## Immich API capability boundary

`ImmichClient` uses an injected `ImmichTransport`; this repository does not yet
provide a concrete `urllib`, `httpx`, or `requests` transport. The transport is
configured for the public API base `/api`, while client paths are relative to
that base. Capability probing first calls the unauthenticated stable
`GET /server/version` endpoint and strictly validates the version fields. Only
Immich major version 3 proceeds to the authenticated stable `GET /server/about`
probe, which requires the least-privilege `server.about` permission and sends
the exact `x-api-key` header. Unknown majors fail safe before authenticated
follow-up or writes. Timeouts, response limits, authentication failures,
permission failures, malformed JSON, and invalid response shapes are surfaced
through redacted typed errors.

Workflow-event support is never inferred from a version number. It is reported
only when separately configured at runtime; API compatibility and polling
fallback remain available when event intake is disabled.

## Provider boundary

The `providers` package will define small, dependency-neutral interfaces. The first providers will implement conventional blur, brightness, exposure, contrast, and resolution metrics. Optional ML/IQA integrations must remain opt-in extras with their licensing documented separately; no non-commercial model library is part of the core dependency set.

## Integration boundaries

- **Transport adapters:** the Immich API adapter and optional workflow/plugin
  event adapter handle authentication, capability detection, serialization,
  and bounded network operations.
- **Application and domain:** normalized work requests, quality analysis,
  scoring, candidate persistence, and retry/resume behavior remain independent
  of Immich transport and plugin SDK types.
- **Review synchronization:** a narrowly scoped API adapter performs explicitly
  allowed review-album operations. No adapter exposes automatic asset deletion.
- **Optional bridge:** workflow/plugin support is preview- and SDK-coupled and
  can be disabled independently. Missing or unknown capabilities fall back to
  API backfill/polling with redacted diagnostics. The bridge-to-worker intake is
  its own trust boundary: bridge-specific runtime authentication or a mutually
  authenticated channel is required, while Immich's internal workflow token is
  never forwarded or reused. Freshness and replay checks use event identity and
  timestamps, durable idempotency stays in an injected replay guard, and
  payload size, identifier length, freshness, and timeout limits bound intake.

The implemented `WorkflowEventIntake` accepts only raw UTF-8 JSON containing
`eventId`, `assetId`, and timezone-aware `occurredAt`. A separately injected
bridge credential is authenticated before normalization. Payloads are bounded
before JSON processing, identifiers are length-limited, events are checked for
freshness, and `ReplayGuard.claim` must atomically succeed before the shared
`WorkSink` receives one request. The runtime secret is not Immich's internal
workflow `authToken`, and neither credential nor raw payload is forwarded.
Durable replay storage is intentionally outside this issue.

## Future surfaces

FastAPI and a web UI are optional consumers of the same worker-facing domain
model. They must not be required to run an analysis job. Likewise, Workflows
and external plugins are optional event sources; they never replace API
backfill or compatibility behavior. No concrete HTTP server, live-network
adapter, WASM plugin, asset-download pipeline, album synchronization, or
durable replay store is implemented yet.
