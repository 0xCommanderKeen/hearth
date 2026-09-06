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
uv run python -m hearth demo
```

Python 3.14, uv, Node 22.22+, and pnpm 11.22 are required. The backend uses FastAPI;
the browser uses React and TypeScript. `make check` builds one Python wheel that
contains the compiled browser, then installs it with locked runtime dependencies
in a temporary environment and exercises its CLI and loopback HTTP application.
See the [standalone installation steps](docs/release.md).
Money is represented as integer microdollars; reservations are admission policy,
not a provider-enforced billing ceiling.

The demo stores its local state in ignored `.hearth/demo`. Use `--data /some/fresh/path`
for a separate simulation. Incompatible prototype databases are refused; there is
no upgrade or data-import command. Existing data is never automatically rewritten.
Every output and cost is labeled simulated. The mock
returns a fixed synthetic summary; it does not interpret arbitrary instructions.
Runtime scenarios include success, held execution, failure, and unknown usage for
deterministic recovery tests.

## Open the local mock application

Build with `make check`, set `HEARTH_OPERATOR_TOKEN` to a local operator credential
of at least 16 characters, then start:

```sh
uv run uvicorn hearth.app:from_env --factory --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766` and enter that token. Set up mock Reader, assign a task,
and open its summary. Fresh Reader setup has a $10 daily allowance using
Europe/Ljubljana budget days. Repeating setup preserves existing settings.
The credential stays in browser memory for the session.
Use `HEARTH_DATA` to select a separate data directory; the default is `.hearth/local`.
Set `HEARTH_MOCK_SCENARIO=hold` before starting a separate demo to exercise cancellation.
Other scenarios are `success`, `failure`, and `unknown_usage`. These are simulations,
not runtime/provider selectors. For browser development, `pnpm --dir web dev`
proxies its `/api` requests to the same local backend.

For a fresh process-backed simulation, set `HEARTH_MOCK_RUNTIME=process_mock` and
`HEARTH_DATA` to a new directory before starting the browser application. Reopening
that store without the selector uses its recorded runtime; a conflicting selector
is refused. Both modes remain simulations. The current schema requires fresh data
from earlier prototype layouts, with no conversion. See [process recovery](docs/process-mock.md).

See the [implementation gates](docs/implementation.md),
[project plan](docs/rebuild-plan.md), and [domain glossary](CONTEXT.md).

For the opt-in contained synthetic worker on the selected Mac, see
[container setup and recovery](docs/container-worker.md).
