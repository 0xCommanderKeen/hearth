"""Half of a backup and restore between two burrows: capture here, or restore here.

ADR 0013 says a store moves between hosts by backup and restore, and ADR 0016 says the
same store runs on either launcher: a Mac on `process`, a server on `container`. The
claim is only worth anything if the two halves happen in different places, so this
script is one half at a time and the evidence file is written by both.

    # on the burrow the household is on
    python scripts/store-round-trip.py capture --label mac-process \\
        --source /path/to/a/store --backup /path/to/carry --out docs/evidence/<file>.json

    # on the burrow it is going to, with that directory carried across
    python scripts/store-round-trip.py restore --label docker-host \\
        --backup /path/to/carry --work /tmp/restored --out docs/evidence/<file>.json

**Never point `--source` at a household that is live.** Capture excludes a busy
executor and freezes writes while it copies, and a restored copy claims nothing -- but
the source is still somebody's household. Use a copy.

The restored copy is *opened* here, the way a host opens one (`hearth.app:from_env`
under uvicorn on a throwaway port), because "it restored" and "it opens here" are
different claims and the second one is the point. A restored store is held: a durable
`restore_hold`, a new observation epoch, every mutation refused and nothing supervised.
It pins no image and measures no fence, because it starts no session -- so the same
bytes answer `process` on a laptop and `container` on a server, which is the claim.
"""

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

TOKEN = "round-trip-operator-" + secrets.token_hex(16)


def opened(store: Path, port: int) -> dict:
    """Start a Hearth on this store with this host's own environment, and read `/health`."""
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "hearth.app:from_env",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=os.environ | {"HEARTH_DATA": str(store), "HEARTH_OPERATOR_TOKEN": TOKEN},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise SystemExit("the copy did not open:\n" + (server.stdout or "").read())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as answer:
                    return json.loads(answer.read())
            except OSError, ValueError:
                time.sleep(1.0)
        raise SystemExit("timed out opening the copy")
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()


def described(store: Path) -> dict:
    """What a store says about itself, without opening it for writing."""
    with closing(sqlite3.connect(f"file:{store / 'hearth.db'}?mode=ro", uri=True)) as db:
        rows = dict(db.execute("SELECT key,value FROM system_meta").fetchall())
        runs = db.execute("SELECT count(*) FROM runs").fetchone()[0]
        # The schema version is the database's own `user_version` pragma, not a row:
        # reading it out of `system_meta` answered `null` for every store there is.
        schema = db.execute("PRAGMA user_version").fetchone()[0]
    return {
        "restore_hold": "restore_hold" in rows,
        "epoch": rows.get("epoch"),
        "runtime_kind": rows.get("runtime_kind"),
        "sandbox_image": rows.get("sandbox_image"),
        "schema_version": schema,
        "runs": runs,
        # A login never travels: the format copies `hearth.db`, `artifacts/` and
        # `memory/` by name, never the tree the credentials live in.
        "credentials_present": (store / "credentials").exists(),
    }


def record_into(out: Path, label: str, record: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    whole = json.loads(out.read_text()) if out.is_file() else {}
    whole.setdefault(
        "round_trip",
        "One store captured on one burrow and opened on the other, in both directions "
        "(ADR 0013's forward upgrade; ADR 0016's 'the same store runs on either "
        "launcher'). Both sources are throwaway copies and no live household was moved.",
    )
    whole.setdefault("legs", {})[label] = record
    out.write_text(json.dumps(whole, indent=2, ensure_ascii=False) + "\n")
    print("wrote", out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["capture", "restore"])
    parser.add_argument("--label", required=True, help="which half of the round trip this is")
    parser.add_argument("--source", type=Path, help="capture: a COPY of a store, never live")
    parser.add_argument("--backup", type=Path, required=True, help="the directory carried across")
    parser.add_argument("--work", type=Path, help="restore: a fresh directory to restore into")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()

    from hearth.storage.backup import capture, restore, verify

    launcher = os.environ.get("HEARTH_SANDBOX", "process")
    if args.step == "capture":
        if args.source is None:
            parser.error("capture requires --source")
        record = {
            "host_launcher": launcher,
            "source": str(args.source),
            "source_store": described(args.source),
            "backup": capture(args.source, args.backup),
            "verified": verify(args.backup),
        }
    else:
        if args.work is None:
            parser.error("restore requires --work")
        if args.work.exists():
            shutil.rmtree(args.work)
        args.work.mkdir(parents=True)
        copy = args.work / "restored"
        record = {
            "host_launcher": launcher,
            "verified_on_arrival": verify(args.backup),
            "restored": restore(args.backup, copy),
        }
        record["health"] = opened(copy, args.port)
        record["copy"] = described(copy)
    print(args.label, json.dumps(record.get("health") or record["source_store"]))
    record_into(args.out, args.label, record)


if __name__ == "__main__":
    main()
