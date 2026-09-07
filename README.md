# Hearth

A home for agents that do useful work. Persistent residents have purpose, memory,
and enforced permissions. Hamlet shows their real activity; Townhall provides
operator controls in the same application.

Hearth is a standalone project, starting with a new read-only daily-summary
Reader and fresh data. Development currently uses mocks and synthetic notes.

## Current implementation

The Python core persists resident revisions, deduplicated task commands, run
admission, and budget reservations. Each change commits with its audit record in
one SQLite transaction. A deterministic mock runtime produces a simulated summary
and exercises recovery, cancellation, and accounting. No real agents, model calls,
or live source files are used. Hamlet and Townhall share one browser application,
an authenticated client, and the authoritative snapshot stream.

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
Hearth store is refused untouched.
Under a mock runtime every output and cost is labeled simulated; the mock returns a
fixed synthetic summary and does not interpret arbitrary instructions.
Runtime scenarios include success, held execution, failure, and unknown usage for
deterministic recovery tests.

## Open the application

Build with `make check`, set `HEARTH_OPERATOR_TOKEN` to a local operator credential
of at least 16 characters, then start:

```sh
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766` and enter that token. Hearth starts empty and ships no
sample data: create a resident, assign it a task, and open its summary. The
credential is remembered in this browser until you sign out.
Use `HEARTH_DATA` to select a separate data directory; the default is `.hearth/local`.
Set `HEARTH_MOCK_SCENARIO=hold` before starting a separate mock instance to exercise cancellation.
Other scenarios are `success`, `failure`, and `unknown_usage`. These are simulations,
not runtime/provider selectors. For browser development, `pnpm dev` from `web/`
proxies its `/api` requests to the same local backend.

For a fresh process-backed simulation, set `HEARTH_MOCK_RUNTIME=process_mock` and
`HEARTH_DATA` to a new directory before starting the browser application. Reopening
that store without the selector uses its recorded runtime; a different selector
switches a quiet store and is refused while a run is unfinished. Both modes remain
simulations. See [process recovery](docs/process-mock.md).

See the [implementation gates](docs/implementation.md),
[project plan](docs/rebuild-plan.md), and [domain glossary](CONTEXT.md).

For the opt-in contained synthetic worker on the selected Mac, see
[container setup and recovery](docs/container-worker.md).
