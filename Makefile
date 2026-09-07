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

# Run pnpm from web/, not the root with --dir: corepack reads the packageManager
# pin from the working directory, and the root has no package.json. From here it
# would fall back to its last known good pnpm instead of the pinned one.
web:
	cd web && pnpm install --frozen-lockfile
	cd web && pnpm exec prettier --check src package.json tsconfig.json vite.config.ts index.html
	cd web && pnpm test
	cd web && pnpm build
	uv run --frozen python scripts/build-web.py
