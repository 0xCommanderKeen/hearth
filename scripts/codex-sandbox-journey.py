"""The sandbox's acceptance journey: one Codex run, inside a container, for real.

Opt-in and never part of `make check`: it spends real subscription money. It stands up
a Hearth instance on its own data directory and its own port -- never `.hearth/live` --
configured for the `container` launcher, and drives one resident through one run. What
it records is the thing epic #183 has to be able to show: the run's own container id,
the digest of the image it came from, Hearth's settlement, and beside it the usage the
CLI itself reported inside that container.

It must run on a Linux Docker host, and it means it. The store's Codex pin is the
sha256 of the CLI Hearth was configured with, and the image's own copy of that CLI is
hashed against it before any resident is admitted, so the two have to be the same
bytes -- which a macOS build and a Linux build never are. On a Mac, Docker Desktop's
Linux VM is a Linux Docker host and Hearth can run *on* it in a container of its own
with the daemon's socket; `docs/sandbox.md` has the invocation this was recorded with.

    HEARTH_SANDBOX=container HEARTH_SANDBOX_IMAGE=<repo>@sha256:... \\
    HEARTH_SANDBOX_NETWORK=<name> \\
    python scripts/codex-sandbox-journey.py \\
        --codex-binary /path/to/codex --codex-auth-home /path/to/codex-home \\
        --data /tmp/hearth-sandbox-journey --out docs/evidence/sandbox-codex-journey-<date>.json

Nothing here reads, copies or prints a credential: the login directory is passed to the
server as environment and is never opened.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = "journey-operator-" + secrets.token_hex(16)


class Instance:
    """The throwaway instance, spoken to exactly as Townhall speaks to it."""

    def __init__(self, base: str):
        self.base = base
        self.headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}

    def call(self, path: str, body=None, *, key: str | None = None):
        headers = dict(self.headers) | ({"Idempotency-Key": key} if key else {})
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as answer:
                raw = answer.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise SystemExit(f"{path} -> {error.code} {error.read().decode()}") from None

    def health(self):
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=5) as answer:
                return json.loads(answer.read())
        except OSError, ValueError:
            return None


def wait_for(condition, what, timeout=900.0, every=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(every)
    raise SystemExit(f"timed out waiting for {what}")


def settled(instance: Instance, task_id: str):
    """A finished run of that task, whatever it finished as."""

    def answer():
        for row in instance.call("/api/state")["runs"]:
            if row["task_id"] == task_id and row["finished_at"]:
                return row
        return None

    return answer


def sessions(data: Path) -> dict:
    """What each run's own worker wrote down about where its session ran.

    The receipt is the CLI's own stream and the handle is what the launcher started;
    only the sandbox's own fields and the CLI's own reported usage are lifted out of
    them here, to sit beside Hearth's settlement in the evidence. No prompt, no answer
    and nothing of the login goes into this file.
    """
    reported = {}
    for folder in sorted((data / "codex-live").glob("*")):
        if not (folder / "receipt.json").exists():
            continue
        receipt = json.loads((folder / "receipt.json").read_text())
        usage = None
        for line in receipt.get("stdout", "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
        handle = folder / "handle.json"
        reported[folder.name] = {
            "sandbox": receipt.get("sandbox"),
            "handle": json.loads(handle.read_text()) if handle.is_file() else None,
            "exit_code": receipt.get("exit_code"),
            "cancelled": receipt.get("cancelled"),
            "launched": receipt.get("launched"),
            "binary_sha256": receipt.get("binary"),
            "cli_reported_usage": usage,
        }
    return reported


def containers(sandbox: dict) -> dict:
    """What the daemon still holds of this journey's own containers, by id.

    A sandbox is one container per run and it ends when its receipt is written, so the
    honest answer here is that none of them is left.
    """
    client = os.environ.get("HEARTH_SANDBOX_DOCKER", "docker")
    answers = {}
    for run, session in sandbox.items():
        identity = (session.get("sandbox") or {}).get("container_id")
        if identity is None:
            continue
        result = subprocess.run(
            [client, "inspect", "--type", "container", "--format", "{{.State.Status}}", identity],
            capture_output=True,
            text=True,
            check=False,
        )
        answers[run] = result.stdout.strip() if result.returncode == 0 else "absent"
    return answers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--codex-auth-home", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--daily-limit", type=int, default=200_000)
    args = parser.parse_args()
    if args.data.exists() and any(args.data.iterdir()):
        raise SystemExit("--data must be a fresh directory; this journey is discarded after")
    args.data.mkdir(parents=True, exist_ok=True)
    if os.environ.get("HEARTH_SANDBOX") != "container":
        raise SystemExit("this journey is the container launcher's; set HEARTH_SANDBOX=container")

    instance = Instance(f"http://127.0.0.1:{args.port}")
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
            str(args.port),
        ],
        env=os.environ
        | {
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "backend"),
            "HEARTH_DATA": str(args.data),
            "HEARTH_OPERATOR_TOKEN": TOKEN,
            "HEARTH_CODEX_BINARY": str(args.codex_binary),
            "HEARTH_CODEX_AUTH_HOME": str(args.codex_auth_home),
        },
        stdout=(args.data / "server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    record: dict = {
        "journey": "epic-183-sandbox-per-run-codex",
        "synthetic_inputs": True,
        "real_model_called": True,
        "runtime_kind": "codex_subscription",
        "launcher": "container",
        "host": "a Linux Docker host; Hearth itself in a container on it",
        "data_directory": "fresh, outside the repository, discarded after recording",
    }
    try:

        def alive():
            if server.poll() is not None:
                raise SystemExit(f"the server exited; see {args.data / 'server.log'}")
            return instance.health()

        record["health"] = wait_for(alive, "the instance to open", timeout=180)
        print("health:", json.dumps(record["health"]))
        if record["health"].get("sandbox", {}).get("launcher") != "container":
            record["health"] = instance.call("/api/health")
            raise SystemExit("this instance did not open on the sandbox: " + json.dumps(record))
        record["operator_health"] = instance.call("/api/health")

        record["provisioning"] = instance.call(
            "/api/residents/provision",
            {
                "name": "Fictional orchard reporter",
                "purpose": "Summarize the fictional orchard notes each day.",
                "instructions": "Answer in at most four sentences. The notes are fictional.",
                "initial_memory": "The orchard notes are fictional.",
                "memory_writable": False,
                "skills": [],
                "execution_profile": "codex_subscription",
                "input_sets": [],
                "daily_limit": args.daily_limit,
                "budget_timezone": "Europe/Ljubljana",
                "creation_reason": "The sandbox's acceptance journey (hearth#185).",
                "manager": "operator",
                "routine": None,
                "first_assignment": None,
            },
            key="sandbox-journey-provision",
        )
        resident = record["provisioning"]["resident_id"]
        print("resident:", resident, record["provisioning"]["status"])

        task = instance.call(
            "/api/tasks",
            {
                "resident_id": resident,
                "instruction": (
                    "Report today's fictional orchard: 12 pears harvested Monday and 3 trees "
                    "planted Tuesday."
                ),
                "expires_at": int(time.time()) + 3600,
            },
            key="sandbox-journey-run",
        )
        instance.call(f"/api/tasks/{task['task_id']}/start", {})
        run = wait_for(settled(instance, task["task_id"]), "the run")
        print("run:", run["id"], run["status"], run["actual_cost"])

        state = instance.call("/api/state")
        record["runtimes"] = state["runtimes"]
        record["runs"] = [row for row in state["runs"] if row["resident_id"] == resident]
        record["accounting"] = {
            row["id"]: instance.call(f"/api/runs/{row['id']}") for row in record["runs"]
        }
        record["answers"] = {
            row["id"]: instance.call(f"/api/artifacts/{row['artifact_id']}")["content"]
            for row in record["runs"]
            if row["artifact_id"]
        }
    finally:
        # Whatever happened, this journey may already have spent real money, so what it
        # did is written down before anything is torn down. A session Hearth is still
        # working is ended through the instance itself: the worker is detached and
        # would go on billing with nobody left to settle it.
        try:
            if server.poll() is None:
                for row in instance.call("/api/state")["runs"]:
                    if row["status"] in ("starting", "running", "interrupted"):
                        print("cancelling in-flight run", row["id"])
                        instance.call(f"/api/runs/{row['id']}/cancel", {})
                        wait_for(
                            settled(instance, row["task_id"]), "that run to settle", timeout=300
                        )
        except (SystemExit, OSError) as error:
            # Nothing here may hide the failure that brought us into `finally`, and
            # the operator still gets the record and the folder to look in.
            print("could not end an in-flight run:", error)
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
        if record.get("runs"):
            write_evidence(record, args)
        else:
            # The journey never reached a settled run. A record without one would still
            # say `real_model_called`, which is exactly what this file must not fake.
            print("no evidence written: the journey did not reach a settled run")


def sandbox_facts(data: Path) -> list[dict]:
    """What the store recorded about the sandbox itself, read after the server stops."""
    import sqlite3

    with sqlite3.connect(f"file:{data / 'hearth.db'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM audit WHERE kind LIKE 'sandbox.%'").fetchall()
    return [dict(row) for row in rows]


def write_evidence(record: dict, args) -> None:
    """The record, with each session's own container beside Hearth's settlement."""
    record["sandbox_audit"] = sandbox_facts(args.data)
    record["sessions"] = sessions(args.data)
    record["containers_afterwards"] = containers(record["sessions"])
    record["total_cost_microdollars"] = sum(row["actual_cost"] or 0 for row in record["runs"])
    record["limits"] = (
        "One bounded run on fictional notes. This records that a resident's session "
        "executed inside a per-run container from the pinned image, that the receipt is "
        "the CLI's own stream out of that container, and that the run settled from the "
        "usage the CLI reported there. It is not evidence of model quality, of the "
        "network fence (#189), of filesystem grants (#187) or of anything about real "
        "sources."
    )
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
