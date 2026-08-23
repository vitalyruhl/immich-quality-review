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
2. The application boundary deduplicates work and persists a resume cursor.
3. The worker obtains an image representation suitable for analysis.
4. A quality orchestrator asks enabled providers for metric observations.
5. A scoring layer turns observations into explainable review candidates.
6. An Immich output adapter creates or updates a review destination without
   deleting assets.

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
  timestamps, durable idempotency stays in the Python application, and payload
  size, identifier length, rate/concurrency, and timeout limits bound intake.

## Future surfaces

FastAPI and a web UI are optional consumers of the same worker-facing domain
model. They must not be required to run an analysis job. Likewise, Workflows
and external plugins are optional event sources; they never replace API
backfill or compatibility behavior.
