# ADR 0001: Immich v3 Integration Strategy

- Status: Accepted
- Date: 2026-08-23

## Context

`immich-quality-review` is an independent Python worker that identifies review
candidates without mutating source assets. Immich v3 introduced Workflows as a
Preview feature, and the current server implementation executes WebAssembly
plugins through Extism and the Immich plugin SDK. The public API remains the
documented integration surface for third-party tools and describes
authentication, authorization, API-key permissions, and request/response
formats.

The worker needs reliable initial discovery, asset access, and compatibility
behavior even when Workflows or external plugins are unavailable. It must also
avoid coupling quality analysis and persistence to Immich's preview workflow
runtime or internal server types.

## Verified upstream evidence

- Immich v3 release notes describe Workflows as a Preview feature and expose
  trigger, filter, and action steps.
- The July 2026 recap says Workflows are technically released but are not yet
  considered complete, and that new triggers are still being developed.
- The current workflow execution service imports `@extism/extism` and
  `@immich/plugin-sdk`, loads plugin WASM, and separates methods with and
  without host functions.
- The workflow execution service obtains `allowedHosts` from each step and
  rejects plugin HTTP requests whose hostname does not match the configured
  patterns. External plugin folders are imported only when the server's
  external-plugin configuration allows them.
- Immich's API documentation is the stated home for third-party integrations
  and documents authentication, authorization, API-key permissions, and
  request/response formats.

## Decision

Keep the standalone Python worker and the documented Immich API as the primary
integration boundary. Add an optional, thin workflow/plugin bridge only for
event-driven intake when the target Immich instance explicitly exposes the
required capabilities.

The bridge emits a normalized work request into the same application port used
by API backfill and polling. It carries only the minimum asset/event identity
needed to enqueue work; it does not contain image bytes, quality logic,
credentials, or repository secrets. The Python worker remains runnable without
the plugin SDK, WebAssembly runtime, or an enabled Immich plugin installation.

Events are hints, not a complete discovery mechanism. The API path remains
responsible for initial backfill, pagination, asset retrieval, compatibility
fallback, and any operation not explicitly supported by the bridge. No path may
automatically delete an Immich asset.

## Responsibility split

| Responsibility | Optional bridge | Python worker/API boundary |
| --- | --- | --- |
| Asset-event notification | Optional | Accept normalized work request |
| Initial library backfill | No | Yes |
| Asset metadata/content access | No | Yes |
| Quality analysis/scoring | No | Yes |
| Candidate persistence | No | Yes |
| Review-album synchronization | No | Yes |
| API fallback/polling | No | Yes |
| Automatic asset deletion | Never | Never |

The bridge is a transport adapter. Domain decisions, provider execution,
candidate state, and review synchronization stay in the Python application and
provider-neutral ports. The worker must not import or depend on Immich's plugin
SDK or internal server types.

## Capability detection and fallback

At startup, the integration probes the documented API and separately detects
the capabilities needed for workflow/plugin intake. It must not infer support
from an Immich version string alone. Missing workflow support, disabled
external plugins, unavailable methods, incompatible manifests, or unsupported
versions disable only the optional bridge and leave API backfill/polling
available.

Unknown or unsupported capabilities fail safe: record a redacted diagnostic,
continue with the API path when possible, and do not attempt an unverified
write. Event delivery never replaces a bounded, repeatable backfill or resume
process; duplicate event and backfill work must be harmless at the application
boundary.

## Security and privacy

- Use separate, least-privilege Immich API keys for discovery/read access and
  explicitly supported review-album writes. Do not request delete permission.
- Keep API keys, private Immich URLs, and other credentials in runtime
  configuration only. Never place them in source, ADRs, fixtures, or logs.
- Restrict plugin HTTP calls to an explicit `allowedHosts` list. The bridge must
  not broaden that list or use arbitrary outbound destinations.
- Send only normalized asset/event identity from the bridge. Do not send image
  data, personal metadata beyond the required identity, repository secrets, or
  infrastructure details.
- Redact URLs, tokens, personal image data, and infrastructure details from
  diagnostics and audit logs.
- Preserve the non-destructive invariant: review state and album membership
  never imply permission to delete or alter the source asset.

## Compatibility and version policy

The documented API is the compatibility baseline. API request/response changes
are handled in the Immich adapter and capability checks. Workflow/plugin
integration is explicitly preview- and SDK-coupled: only the bridge may know
plugin manifests, method names, host-function behavior, or SDK-specific event
payloads. This coupling is more version-sensitive than the public documented
API, so an incompatible or unknown plugin surface disables the bridge rather
than blocking the worker.

Supported Immich versions and capability probes will be recorded when the API
adapter is implemented. Until then, documentation must not claim that a
particular preview or plugin version is mandatory.

## Alternatives considered

### API-only integration

This is reliable for backfill and compatibility, but cannot use supported
event-driven entry points when a user explicitly enables them. It remains the
required fallback and standalone baseline.

### Plugin-first integration

This would couple discovery and processing to a Preview feature, external
plugin enablement, WASM/SDK details, and evolving workflow triggers. It would
also make the worker unavailable on installations that expose only the public
API, so it is rejected.

### In-process Python plugin

Immich's current plugin execution surface is WebAssembly/Extism, not an
in-process Python worker. Embedding the Python analyzer in Immich would add
runtime, packaging, and privilege coupling and is rejected.

## Consequences

The worker has one provider-neutral intake and processing boundary with two
transport sources: API backfill/polling and optional event hints. Deployments
without Workflows or external plugins remain supported. The bridge requires
additional capability detection, compatibility tests, and observability, and
must track upstream Preview/SDK changes separately from the API adapter.

The design intentionally favors missed event optimization over unsafe or
unverifiable processing: backfill and polling can recover from unavailable or
duplicate events, while no integration path can delete assets automatically.

## Follow-up work

- Define the application ports and normalized work-request schema.
- Implement API authentication, pagination, asset-content access, and
  capability probes with injectable transports.
- Implement the optional event adapter only after a supported Immich workflow
  and plugin surface is selected and tested.
- Add fakes for duplicate delivery, missing capabilities, permission denial,
  timeout, and unsupported-version fallback.

## References

- [Immich v3.0.0 announcement and Workflows preview](https://github.com/immich-app/immich/discussions/29439)
- [Immich July 2026 recap](https://immich.app/blog/2026-july-recap)
- [Immich API documentation](https://api.immich.app/)
- [Immich API documentation announcement](https://immich.app/blog/immich-api-documentation)
- [Immich workflow execution service](https://github.com/immich-app/immich/blob/main/server/src/services/workflow-execution.service.ts)
