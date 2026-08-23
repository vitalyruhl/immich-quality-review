# immich-quality-review

`immich-quality-review` is an independent community project for finding technically questionable images in an [Immich](https://immich.app/) library. It is not affiliated with or endorsed by Immich.

> Status: early development / experimental. The project currently provides a package skeleton and quality-provider boundaries, not a working asset-review service.

## Motivation

Large photo libraries often contain blurred, poorly exposed, low-contrast, or low-resolution assets that deserve a second look. This project will analyze assets through the Immich API and surface review candidates for people to decide on manually.

**Safety principle: review candidates only; never delete assets automatically.**

## Planned core capabilities

- Blur and sharpness signals
- Brightness, underexposure, and overexposure signals
- Contrast and resolution checks
- Incremental asset discovery through the Immich API
- A review album or equivalent workflow for flagged candidates
- Optional ML/IQA providers behind a separate provider interface

The initial quality engine will use freely usable, conventional image-analysis techniques. Optional model-based providers remain separate: `pyiqa`, for example, is not a required dependency because of its non-commercial licensing terms.

## Architecture

The first deployment target is a separate Docker worker. It will read asset metadata and image data through the Immich API, pass images to provider-based quality metrics, aggregate the results into review candidates, and write only the intended review state back through the API. A FastAPI service or web UI may be added later, without coupling the core worker to it.

See [the architecture note](docs/architecture.md) for the intended boundaries.

## Development

Python 3.12 or newer is required.

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Docker is planned as the primary deployment method. The bootstrap image is intentionally minimal:

```powershell
docker build -t immich-quality-review .
docker run --rm immich-quality-review
```

## Contributing

Contributions and early design feedback are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before participating.

## License

This project is licensed under the [GNU Affero General Public License v3.0 only](LICENSE).
