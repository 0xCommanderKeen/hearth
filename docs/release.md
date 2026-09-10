# Standalone release installation

Build and verify from a checkout with Python 3.14, uv, Node 26.7+ (26.x) and pnpm 11.22:

```sh
make check
```

The browser tests disable Node's native Web Storage so jsdom supplies isolated
browser storage. This is part of `pnpm test`; no shell flags are needed.

This includes an installation into a temporary environment using the built wheel
and hashed runtime dependencies from `uv.lock`. The check runs the CLI and actual
loopback HTTP server outside the checkout, checks imported code belongs to the
installation, verifies browser assets and a synthetic Reader workflow, restarts
the application and checks a held backup restore. It removes its temporary files
and stops its servers, including on failure. No model or external service is called.

To retain a separate installation on macOS or Linux, choose a new installation
path and run these commands from the same checkout used to build the wheel:

```sh
hearth_install=/absolute/path/to/new/hearth-install
uv venv "$hearth_install/venv" --python 3.14
uv export --frozen --no-dev --no-emit-project --quiet \
  --output-file "$hearth_install/runtime.txt"
uv pip sync --python "$hearth_install/venv/bin/python" --require-hashes \
  "$hearth_install/runtime.txt"
uv pip install --python "$hearth_install/venv/bin/python" --no-deps \
  dist/hearth-0.1.0-py3-none-any.whl
cd "$hearth_install"
./venv/bin/python -I -m hearth verify-backup --help
```

Keep the exact wheel, lockfile and source commit together as release provenance.
The installed application needs Python and its runtime dependencies; Node and pnpm
are build tools. The release ships no seeded or sample data.

For the browser application, set `HEARTH_OPERATOR_TOKEN` to a local credential of
at least 16 characters and `HEARTH_DATA` to a fresh data directory, then run:

```sh
./venv/bin/python -I -m uvicorn hearth.app:from_env --factory \
  --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766`. Stop the process before removing the installation.
Keep data and backups separately if they should survive removal. A store from an
older release is upgraded on the first start, keeping the original beside it.

This verifies a standalone local artifact. Host deployment, real runtime isolation,
provider usage, retention policy and operational recovery still need acceptance
on the selected execution machine.
