# Development notes

Use Python 3.12 or newer and synchronize the locked development environment with `uv sync --frozen`.

The bootstrap intentionally has no runtime dependencies. `uv` manages the environment and lockfile, `hatchling` is the PEP 517 build backend, and `pytest` and `ruff` are development tools. Their permissive licenses are compatible with this project's AGPL-3.0-only license.

Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and `uv build` before opening a pull request.
