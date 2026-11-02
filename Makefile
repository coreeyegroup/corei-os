.PHONY: sync lint typecheck test schemas check-schemas hygiene check

sync:            ## install locked dependencies
	uv sync --frozen

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

schemas:         ## regenerate contracts/schemas/*.json from the Python contracts
	uv run python scripts/export_schemas.py

check-schemas:   ## fail if committed JSON Schemas differ from the contracts
	uv run python scripts/export_schemas.py --check

hygiene:         ## fail on committed secrets, vendored deps, runtime output
	uv run python scripts/repo_hygiene.py

check: lint typecheck test check-schemas hygiene
