# Development notes

Use Python 3.12 or newer and install the development extra with `python -m pip install -e ".[dev]"`.

The bootstrap intentionally has no runtime dependencies. `hatchling` is used only as a PEP 517 build backend; `pytest` and `ruff` are development tools. Their permissive licenses are compatible with this project's AGPL-3.0-only license.

Run `pytest`, `ruff check .`, and `ruff format --check .` before opening a pull request.
