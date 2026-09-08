# Hearth

A home for agents that do useful work. Persistent residents have purpose, memory,
and enforced permissions. Hamlet shows their real activity; Townhall provides
operator controls in the same application.

Hearth is a standalone project, starting with a new read-only daily-summary
Reader and fresh data. Residents run on a real Codex subscription; the notes they
read are synthetic.

## Current implementation

The Python core persists resident revisions, deduplicated task commands, run
admission, and budget reservations. Each change commits with its audit record in
one SQLite transaction. One runtime executes work: a bounded, read-only Codex
subscription run whose provider receipt settles the run's cost and output. No live
source files are used. Hamlet and Townhall share one browser application, an
authenticated client, and the authoritative snapshot stream.

```sh
uv sync --frozen
make check
```

Python 3.14, uv, Node 22.22+, and pnpm 11.22 are required. The backend uses FastAPI;
the browser uses React and TypeScript. `make check` builds one Python wheel that
contains the compiled browser, then installs it with locked runtime dependencies
in a temporary environment and exercises its CLI and loopback HTTP application.
See the [standalone installation steps](docs/release.md).
Money is represented as integer microdollars; reservations are admission policy,
not a provider-enforced billing ceiling.

Hearth stores its local state in ignored `.hearth`. Use `HEARTH_DATA=/some/path`
for a separate instance. A store from an older Hearth release is upgraded on start
and the original is kept beside it as `hearth.db.before-v<N>`; a file that is not a
Hearth store is refused untouched. A store recorded against a runtime this release
no longer ships keeps those runs only in the preserved original.

## Open the application

Build with `make check`, set `HEARTH_OPERATOR_TOKEN` to a local operator credential
of at least 16 characters, point `HEARTH_CODEX_BINARY` at the pinned Codex CLI and
`HEARTH_CODEX_AUTH_HOME` at its logged-in `CODEX_HOME`, then start:

```sh
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766` and enter that token. Hearth starts empty and ships no
sample data: create a resident, assign it a task, and open its summary. The
credential stays in browser memory for the session.
Use `HEARTH_DATA` to select a separate data directory; the default is `.hearth/local`.
For browser development, `pnpm dev` from `web/` proxies its `/api` requests to the
same local backend. Continuous integration has no subscription, so the test suite
and the installed-wheel smoke inject the fake runtime in `tests/fake_runtime.py`;
it is never packaged.

See the [implementation gates](docs/implementation.md),
[project plan](docs/rebuild-plan.md), and [domain glossary](CONTEXT.md).
