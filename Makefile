.PHONY: check test web
check:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen ty check backend
	uv run --frozen pytest
	$(MAKE) web
	uv build --no-sources
	uv run --frozen python scripts/check-wheel.py

test:
	uv run --frozen pytest

web:
	pnpm --dir web install --frozen-lockfile
	pnpm --dir web exec prettier --check src package.json tsconfig.json vite.config.ts index.html
	pnpm --dir web test
	pnpm --dir web build
	uv run --frozen python scripts/build-web.py
