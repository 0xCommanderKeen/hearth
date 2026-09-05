.PHONY: check test
check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen ty check backend
	uv run --frozen pytest
	uv build --no-sources

test:
	uv run --frozen pytest

