"""The Claude runtime's acceptance journey, on a throwaway instance the operator names.

Opt-in and never part of `make check`: it spends real subscription money. It stands up a
Hearth instance on its own data directory and its own port -- never `.hearth/live` -- and
drives one resident that declares `claude_subscription` through three runs:

1. a run that reports the fictional notes and writes its own journal entry over the bridge,
2. a run that opens with that entry and quotes it, so the first run's work is provably read,
3. a run cancelled after it is launched, which settles with what it really spent.

It then records what happened, Hearth's own settlement beside the CLI's own
`total_cost_usd`, as the evidence file named on the command line.

The store's default runtime is Codex on every fresh store and no supported path changes
that, so both providers are configured here: the default opens for the household and the
journeying resident declares Claude for itself (`docs/adr/0015-runtime-per-resident.md`).

Nothing here reads, copies or prints a credential: the two configuration directories are
passed to the server as environment and are never opened. The private Claude login they
need is the operator's own one-off step -- see `docs/claude-runtime.md`.

    uv run python scripts/claude-journey.py --claude-config-dir ~/private-claude-config \\
        --codex-binary ... --codex-auth-home ... --data /tmp/hearth-journey \\
        --out docs/evidence/claude-journey-<date>.json
"""

import argparse
import json
import os
import secrets
import subprocess
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


def assign(instance: Instance, resident: str, key: str, instruction: str) -> str:
    task = instance.call(
        "/api/tasks",
        {
            "resident_id": resident,
            "instruction": instruction,
            "expires_at": int(time.time()) + 3600,
        },
        key=key,
    )
    instance.call(f"/api/tasks/{task['task_id']}/start", {})
    return task["task_id"]


def cli_numbers(data: Path) -> dict:
    """What the CLI itself reported, read from the receipts its workers published.

    The receipt is the CLI's own stream; only the one `result` event's own numbers are
    lifted out of it here, to sit beside Hearth's settlement in the evidence.
    """
    reported = {}
    for folder in sorted((data / "claude-live").glob("*")):
        if not (folder / "receipt.json").exists():
            continue
        receipt = json.loads((folder / "receipt.json").read_text())
        result = None
        for line in receipt.get("stdout", "").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                result = event
        reported[folder.name] = {
            "exit_code": receipt.get("exit_code"),
            "cancelled": receipt.get("cancelled"),
            "launched": receipt.get("launched"),
            "management": receipt.get("management"),
            "result": result
            and {
                key: result.get(key)
                for key in (
                    "subtype",
                    "is_error",
                    "total_cost_usd",
                    "modelUsage",
                    "num_turns",
                    "terminal_reason",
                )
            },
        }
    return reported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-config-dir", type=Path, required=True)
    parser.add_argument("--claude-binary", type=Path, required=True)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--codex-auth-home", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--daily-limit", type=int, default=400_000)
    args = parser.parse_args()
    if args.data.exists() and any(args.data.iterdir()):
        raise SystemExit("--data must be a fresh directory; this journey is discarded after")
    args.data.mkdir(parents=True, exist_ok=True)

    instance = Instance(f"http://127.0.0.1:{args.port}")
    server = subprocess.Popen(
        [
            "uv",
            "run",
            "--frozen",
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
            "HEARTH_DATA": str(args.data),
            "HEARTH_OPERATOR_TOKEN": TOKEN,
            "HEARTH_CODEX_BINARY": str(args.codex_binary),
            "HEARTH_CODEX_AUTH_HOME": str(args.codex_auth_home),
            "HEARTH_CLAUDE_BINARY": str(args.claude_binary),
            "HEARTH_CLAUDE_CONFIG_DIR": str(args.claude_config_dir),
        },
        stdout=(args.data / "server.log").open("w"),
        stderr=subprocess.STDOUT,
    )
    record: dict = {
        "journey": "epic-144-claude-runtime",
        "synthetic_inputs": True,
        "real_model_called": True,
        "runtime_kind": "claude_subscription",
        "data_directory": "fresh, outside the repository, discarded after recording",
    }
    try:
        record["health"] = wait_for(instance.health, "the instance to open", timeout=180)
        print("health:", json.dumps(record["health"]))
        if "claude_subscription" not in {row["kind"] for row in record["health"]["runtimes"]}:
            raise SystemExit("the Claude runtime did not open here; the health answer says why")

        record["provisioning"] = instance.call(
            "/api/residents/provision",
            {
                "name": "Fictional orchard reporter",
                "purpose": "Summarize the fictional orchard notes each day.",
                "instructions": "Answer in at most four sentences. The notes are fictional.",
                "initial_memory": "The orchard notes are fictional.",
                "memory_writable": True,
                "skills": [],
                "execution_profile": "claude_subscription",
                "input_sets": [],
                "daily_limit": args.daily_limit,
                "budget_timezone": "Europe/Ljubljana",
                "creation_reason": "The Claude runtime's acceptance journey (hearth#149).",
                "manager": "operator",
                "routine": None,
                "first_assignment": None,
            },
            key="claude-journey-provision",
        )
        resident = record["provisioning"]["resident_id"]
        print("resident:", resident, record["provisioning"]["status"])

        first = wait_for(
            settled(
                instance,
                assign(
                    instance,
                    resident,
                    "claude-journey-day-one",
                    "Report today's fictional orchard: 12 pears harvested Monday and 3 trees "
                    "planted Tuesday. Then write one short journal entry recording what you "
                    "reported.",
                ),
            ),
            "the first run",
        )
        print("run 1:", first["id"], first["status"], first["actual_cost"])
        second = wait_for(
            settled(
                instance,
                assign(
                    instance,
                    resident,
                    "claude-journey-day-two",
                    "What did you write in your own journal last time? Quote it, then report "
                    "today's fictional orchard: 12 pears harvested Monday.",
                ),
            ),
            "the second run",
        )
        print("run 2:", second["id"], second["status"], second["actual_cost"])

        # A launched run, cancelled: it never claims zero usage, because it really spent.
        task_id = assign(
            instance,
            resident,
            "claude-journey-day-three",
            "Write a long report about the fictional orchard.",
        )

        def launched():
            for row in instance.call("/api/state")["runs"]:
                if row["task_id"] == task_id and row["status"] == "running":
                    return row
            return None

        third = wait_for(launched, "the third run to launch", timeout=300, every=1)
        time.sleep(8)  # let the session really begin before it is cancelled
        instance.call(f"/api/runs/{third['id']}/cancel", {})
        third = wait_for(settled(instance, task_id), "the third run to settle")
        print("run 3:", third["id"], third["status"], third["actual_cost"])

        state = instance.call("/api/state")
        record["runtimes"] = state["runtimes"]
        record["runs"] = [row for row in state["runs"] if row["resident_id"] == resident]
        record["accounting"] = {
            row["id"]: instance.call(f"/api/runs/{row['id']}") for row in record["runs"]
        }
        record["journal"] = instance.call(f"/api/residents/{resident}/journal")
        record["memory"] = instance.call(f"/api/residents/{resident}/memory")
        record["answers"] = {
            row["id"]: instance.call(f"/api/artifacts/{row['artifact_id']}")["content"]
            for row in record["runs"]
            if row["artifact_id"]
        }
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
    record["cli"] = cli_numbers(args.data)
    record["total_cost_microdollars"] = sum(
        row["actual_cost"] or 0 for row in record.get("runs", [])
    )
    record["limits"] = (
        "Three bounded runs on fictional notes. This records that a resident declared onto "
        "the Claude subscription completed real work, wrote its own journal over the bridge, "
        "was read back by its next run, and settled against the CLI's own reported cost. It "
        "is not evidence of model quality, daily adoption or anything about real sources."
    )
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
