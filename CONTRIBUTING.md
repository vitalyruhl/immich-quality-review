# Contributing

Thank you for helping improve `immich-quality-review`.

## Getting started

1. Check existing issues before starting a larger change.
2. Fork the repository and create a focused branch.
3. Synchronize the locked development environment with `uv sync --frozen`.
4. Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and `uv build`.
5. Open a pull request using the provided template.

## Contribution expectations

- Keep pull requests narrow, documented, and covered by tests when behavior changes.
- Use Python type hints and follow the existing formatting and lint rules.
- Do not add model, dataset, or library dependencies without documenting their license and whether they are optional.
- Preserve the project's safety principle: proposed integrations must never delete Immich assets automatically.
- Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in public issues.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
