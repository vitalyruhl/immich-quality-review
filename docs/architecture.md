# Architecture direction

The initial product boundary is a standalone worker, not an Immich server extension. It will authenticate to an Immich instance through its documented API, discover assets incrementally, obtain the data needed for analysis, and publish review-oriented results only when explicitly configured.

## Core flow

1. An Immich API adapter discovers assets and persists a resume cursor.
2. A worker obtains an image representation suitable for analysis.
3. A quality orchestrator asks enabled providers for metric observations.
4. A scoring layer turns observations into explainable review candidates.
5. An Immich output adapter creates or updates a review destination without deleting assets.

## Provider boundary

The `providers` package will define small, dependency-neutral interfaces. The first providers will implement conventional blur, brightness, exposure, contrast, and resolution metrics. Optional ML/IQA integrations must remain opt-in extras with their licensing documented separately; no non-commercial model library is part of the core dependency set.

## Future surfaces

FastAPI and a web UI are optional consumers of the same worker-facing domain model. They must not be required to run an analysis job.
