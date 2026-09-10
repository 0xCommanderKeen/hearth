"""Epic #183's acceptance demo, against a Hearth deployed from `deploy/compose.yaml`.

Opt-in and never part of `make check`: it spends real subscription money. It talks to a
*running* deployment over the operator API exactly as Townhall does, and reads that
deployment's store on disk for the two things the API does not publish -- what each run
was admitted to reach, and what its own worker wrote down about the container it
started.

What it records is the demo the epic asks for, per runtime kind:

  * a resident on that kind, with a login of its own and two granted folders -- one
    read-only, one writable -- completing a **routine** run inside a per-run sandbox;
  * the run's own container id, the image digest it came from, the folders it was
    admitted to and the login it spent, beside Hearth's settlement and the usage the
    CLI itself reported inside that container;
  * the file the writable folder was written into, on the host;
  * the fence, as Hearth measured it from inside the sandbox network: its own address
    and a LAN address unreachable, the provider reachable.

A kind this deployment is not configured for is recorded as not run, with the reason,
and never as anything else.

**Nothing here reads, copies or prints a credential.** The logins are directories the
operator seeded before this ran; the script only asks Hearth which one each run spent.

    python scripts/sandbox-journey.py \\
        --base http://172.30.0.2:8000 --token "$HEARTH_OPERATOR_TOKEN" \\
        --store /var/lib/docker/volumes/hearth-store/_data \\
        --folders /var/lib/docker/volumes/hearth-folders/_data \\
        --out docs/evidence/sandbox-journey-<date>.json

It has to run somewhere that can reach Hearth *and* see the store, which on a burrow
whose filesystem is volumes means a container of Hearth's own image with the store
volume mounted at its own path. `deploy/README.md` and `docs/sandbox.md` have the
invocation the committed evidence was recorded with.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# The runtime kinds this demo knows how to stand a resident up on, and the folder each
# one's runs are worked in. Both are the adapter's own names.
KINDS = {
    "codex_subscription": "codex-live",
    "claude_subscription": "claude-live",
}
TIMEZONE = "Europe/Ljubljana"
# How far ahead the routine is scheduled. Far enough that writing the routine, the
# grant and the login has certainly finished before it is due; near enough that the
# demo is minutes rather than a day.
LEAD = timedelta(minutes=2)


class Instance:
    """The deployment, spoken to exactly as Townhall speaks to it."""

    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def call(self, path: str, body=None, *, key: str | None = None, method: str | None = None):
        headers = dict(self.headers) | ({"Idempotency-Key": key} if key else {})
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method or ("POST" if body is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as answer:
                raw = answer.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise SystemExit(f"{path} -> {error.code} {error.read().decode()}") from None

    def alive(self):
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=5) as answer:
                return json.loads(answer.read())
        except OSError, ValueError:
            return None


def wait_for(condition, what, timeout=900.0, every=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(every)
    raise SystemExit(f"timed out waiting for {what}")


def resident_of(command_id: str) -> str:
    """The id provisioning will give this command, before it has been asked.

    Provisioning derives the resident's id from the idempotency key, so an operator can
    seed that resident's own login *before* the resident exists -- which is the only
    order that works, because a login is resolved at admission and this demo's whole
    point is a run that spends one.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hearth:resident:" + command_id))


def provision(instance: Instance, command_id: str, kind: str, limit: int) -> dict:
    return instance.call(
        "/api/residents/provision",
        {
            "name": f"Fictional orchard reporter ({kind})",
            "purpose": "Summarize the fictional orchard notes each day.",
            "instructions": (
                "Answer in at most four sentences. The notes are fictional. This resident "
                "is also granted two folders: notes, read-only, and drafts, writable. If "
                "you can open them, read the notes and write your summary into drafts as "
                "summary.md; either way say plainly in your answer whether you could."
            ),
            "initial_memory": "The orchard notes are fictional.",
            "memory_writable": False,
            "skills": [],
            "execution_profile": kind,
            "input_sets": [],
            "daily_limit": limit,
            "budget_timezone": TIMEZONE,
            "creation_reason": "Epic #183's acceptance demo (hearth#189).",
            "manager": "operator",
            "routine": None,
            "first_assignment": None,
        },
        key=command_id,
    )


def grant_folders(instance: Instance, resident_id: str, notes: str, drafts: str) -> dict:
    """Two folders: one this resident may read, one it may write. Nothing else."""
    current = instance.call(f"/api/residents/{resident_id}/management")
    return instance.call(
        f"/api/residents/{resident_id}/management",
        {
            "enabled": False,
            "profiles": [],
            "input_set_ids": [],
            "capabilities": [],
            "letter_recipient_ids": [],
            "mounts": [
                {"name": "notes", "host_path": notes, "mode": "ro"},
                {"name": "drafts", "host_path": drafts, "mode": "rw"},
            ],
            "expected_revision": current["revision"],
        },
        method="PUT",
    )


def schedule(instance: Instance, resident_id: str, due: datetime) -> dict:
    routine_id = "sandbox-journey-" + resident_id[:8]
    return instance.call(
        f"/api/routines/{routine_id}",
        {
            "resident_id": resident_id,
            "instruction": (
                "Report today's fictional orchard: 12 pears harvested Monday and 3 trees "
                "planted Tuesday. Leave the summary in the drafts folder if you can "
                "reach it."
            ),
            "local_time": due.strftime("%H:%M"),
            "timezone": TIMEZONE,
            "enabled": True,
            "expected_revision": 0,
        },
    )


def settled(instance: Instance, resident_id: str):
    """A finished run of that resident, whatever it finished as."""

    def answer():
        for row in instance.call("/api/state")["runs"]:
            if row["resident_id"] == resident_id and row["finished_at"]:
                return row
        return None

    return answer


def store_facts(store: Path, run_id: str, kind: str) -> dict:
    """What this deployment's own store says about one run, beside what the API says.

    Two things live only here. `run_mounts` is what admission pinned -- the folders the
    run could reach, by the grant revision that granted them -- and is read from the
    store rather than from the receipt for the same reason settlement is: a receipt
    naming a folder the run was not granted names nothing. The receipt and `handle.json`
    are the worker's own account of the container it started.
    """
    with closing(sqlite3.connect(f"file:{store / 'hearth.db'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        mounts = [
            dict(row)
            for row in db.execute(
                "SELECT name,host_path,mode,grant_revision FROM run_mounts "
                "WHERE run_id=? ORDER BY position",
                (run_id,),
            )
        ]
        scope = db.execute("SELECT login_scope FROM runs WHERE id=?", (run_id,)).fetchone()
    folder = store / KINDS[kind] / run_id
    receipt_file, handle_file = folder / "receipt.json", folder / "handle.json"
    receipt = json.loads(receipt_file.read_text()) if receipt_file.is_file() else {}
    usage = None
    for line in receipt.get("stdout", "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        # Each provider's own end-of-turn accounting, whatever it calls it.
        if isinstance(event, dict) and event.get("type") in ("turn.completed", "result"):
            usage = event.get("usage")
    return {
        "run_mounts": mounts,
        "login_scope": scope["login_scope"] if scope else None,
        "sandbox": receipt.get("sandbox"),
        "handle": json.loads(handle_file.read_text()) if handle_file.is_file() else None,
        "receipt_login_scope": receipt.get("login_scope"),
        "receipt_mounts": (receipt.get("sandbox") or {}).get("mounts"),
        "exit_code": receipt.get("exit_code"),
        "cancelled": receipt.get("cancelled"),
        "launched": receipt.get("launched"),
        "binary_sha256": receipt.get("binary"),
        "cli_reported_usage": usage,
    }


def sandbox_audit(store: Path) -> list[dict]:
    """Everything this store recorded about the sandbox and the fence."""
    with closing(sqlite3.connect(f"file:{store / 'hearth.db'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT sequence,kind,resource_id,at,detail FROM audit "
                "WHERE kind LIKE 'sandbox.%' OR kind='run.mount_rw_used' "
                "OR kind='grant.mount_rw_granted' ORDER BY sequence"
            )
        ]


def container_state(identity: str | None) -> str | None:
    """What the daemon still holds of one container. A sandbox ends with its run.

    "Absent" is a claim about the world, so it is only made when the daemon really
    answered that it has no such container. A client that could not be run, a daemon
    that did not reply, a timeout -- each is reported as itself, because evidence
    saying a container was removed when nobody could ask is the one thing this file
    must not say. It is the same separation `launcher.hashed()` makes, for the same
    reason.
    """
    if identity is None:
        return None
    client = os.environ.get("HEARTH_SANDBOX_DOCKER", "docker")
    argv = [client]
    if os.environ.get("HEARTH_SANDBOX_DOCKER_HOST"):
        argv += ["--host", os.environ["HEARTH_SANDBOX_DOCKER_HOST"]]
    try:
        result = subprocess.run(
            [*argv, "inspect", "--type", "container", "--format", "{{.State.Status}}", identity],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError, subprocess.SubprocessError:
        return "the runtime did not answer"
    if result.returncode == 0:
        return result.stdout.strip()
    # The client's own words for "there is no such container". Anything else on
    # stderr is a daemon that refused or a client that never reached one.
    if "no such container" in result.stderr.lower() or "no such object" in result.stderr.lower():
        return "absent"
    return "the runtime did not answer"


def occurrence_of(store: Path, task_id: str) -> dict | None:
    """The routine occurrence this task came from, if a routine made it."""
    with closing(sqlite3.connect(f"file:{store / 'hearth.db'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM occurrences WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row is not None else None


def written(folder: Path) -> list[dict]:
    """What is in the writable folder afterwards -- names and sizes, never content."""
    if not folder.is_dir():
        return []
    return sorted(
        ({"name": item.name, "bytes": item.stat().st_size} for item in folder.iterdir()),
        key=lambda item: item["name"],
    )


def journey(instance: Instance, args, kind: str) -> dict:
    """One resident, one routine, one run, on one runtime kind."""
    command_id = f"sandbox-journey-{kind}"
    resident_id = resident_of(command_id)
    notes = str(args.folders / resident_id / "notes")
    drafts = str(args.folders / resident_id / "drafts")
    for path in (Path(notes), Path(drafts)):
        path.mkdir(parents=True, exist_ok=True)
    (Path(notes) / "orchard.md").write_text(
        "Fictional orchard notes.\n\n- Monday: 12 pears harvested.\n- Tuesday: 3 trees planted.\n"
    )
    provisioning = provision(instance, command_id, kind, args.daily_limit)
    if provisioning["resident_id"] != resident_id:
        raise SystemExit("provisioning did not give the id its login was seeded for")
    grant = grant_folders(instance, resident_id, notes, drafts)
    due = datetime.now(ZoneInfo(TIMEZONE)) + LEAD
    routine = schedule(instance, resident_id, due)
    print(kind, "resident", resident_id, "routine due", due.strftime("%H:%M"))
    run = wait_for(settled(instance, resident_id), f"the {kind} routine run", timeout=args.wait)
    print(kind, "run", run["id"], run["status"], run["actual_cost"])
    facts = store_facts(args.store, run["id"], kind)
    identity = (facts["sandbox"] or {}).get("container_id")
    return {
        "resident_id": resident_id,
        "provisioning": provisioning,
        "grant": grant,
        "routine": routine,
        "routine_due_local": due.strftime("%Y-%m-%d %H:%M ") + TIMEZONE,
        # The task really came from the routine and not from anybody's hand: the
        # occurrence the scheduler wrote is what names it.
        "occurrence": occurrence_of(args.store, run["task_id"]),
        "run": run,
        "accounting": instance.call(f"/api/runs/{run['id']}"),
        "answer": (
            instance.call(f"/api/artifacts/{run['artifact_id']}")["content"]
            if run["artifact_id"]
            else None
        ),
        "session": facts,
        "container_afterwards": container_state(identity),
        # What became of the two granted folders. `run_mounts` is what admission pinned
        # and the receipt is what the launcher placed; this is the host, afterwards.
        "folders": {
            "read_only_afterwards": written(Path(notes)),
            "writable_afterwards": written(Path(drafts)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="where the deployment answers")
    parser.add_argument("--token", default=os.environ.get("HEARTH_OPERATOR_TOKEN", ""))
    parser.add_argument("--store", type=Path, required=True, help="the data directory on disk")
    parser.add_argument("--folders", type=Path, required=True, help="where granted folders live")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--daily-limit", type=int, default=200_000)
    parser.add_argument("--wait", type=float, default=900.0)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("this demo needs the operator's token")

    instance = Instance(args.base, args.token)
    record: dict = {
        "journey": "epic-183-sandbox-per-run-acceptance",
        "synthetic_inputs": True,
        "real_model_called": True,
        "host": "a Linux Docker host; Hearth in its own container from deploy/compose.yaml",
        "data_directory": "a fresh store on a named volume, discarded after recording",
    }
    try:
        record["health"] = wait_for(instance.alive, "the deployment to open", timeout=300)
        if record["health"].get("sandbox", {}).get("launcher") != "container":
            raise SystemExit("this deployment is not on the sandbox: " + json.dumps(record))
        # The fence, as Hearth measures it from inside the sandbox network on this ask:
        # its own address and a LAN address unreachable, the provider reachable.
        record["operator_health"] = instance.call("/api/health")
        print("fence:", json.dumps(record["operator_health"]["sandbox"].get("fence")))
        configured = {row["kind"] for row in record["health"]["runtimes"]}
        record["kinds"] = {}
        for kind in KINDS:
            if kind not in configured:
                reason = next(
                    (
                        row["reason"]
                        for row in record["operator_health"]["unavailable"]
                        if row["kind"] == kind
                    ),
                    "runtime_not_configured",
                )
                # Not run, and said so. A demo file that quietly leaves a kind out is a
                # demo file that claims more than it did.
                record["kinds"][kind] = {"ran": False, "reason": reason}
                print(kind, "not run:", reason)
                continue
            # Recorded as started *before* the journey runs, so a failure inside it
            # cannot take the record of a paid run away with it. `ran` becomes true
            # only once that kind's run has settled and been read.
            record["kinds"][kind] = {"ran": False, "reason": "did not finish"}
            record["kinds"][kind] = {"ran": True} | journey(instance, args, kind)
    finally:
        # Whatever happened, this may already have spent real money, so what it did is
        # written down before anything else. A session Hearth is still working is ended
        # through the instance itself: the worker is detached and would go on billing
        # with nobody left to settle it.
        try:
            # This demo's own residents and nobody else's. The deployment is meant to
            # be a throwaway one, but a script that cancelled whatever it found would
            # be a bad thing to point at a household by mistake.
            mine = {resident_of(f"sandbox-journey-{kind}") for kind in KINDS}
            for row in instance.call("/api/state")["runs"]:
                if row["resident_id"] in mine and row["status"] in (
                    "starting",
                    "running",
                    "interrupted",
                ):
                    print("cancelling in-flight run", row["id"])
                    instance.call(f"/api/runs/{row['id']}/cancel", {})
                    wait_for(settled(instance, row["resident_id"]), "that run to settle", 300)
        except (SystemExit, OSError) as error:
            print("could not end an in-flight run:", error)
        # Anything that reached a settled run is written down, even when a later kind
        # failed and brought us here: that run spent real money and this file is the
        # only account of it. A file with no settled run in it would still say
        # `real_model_called`, which is what it must never fake.
        if any(entry.get("ran") for entry in record.get("kinds", {}).values()):
            write_evidence(record, args)
        else:
            print("no evidence written: no kind reached a settled run")


def write_evidence(record: dict, args) -> None:
    record["sandbox_audit"] = sandbox_audit(args.store)
    record["total_cost_microdollars"] = sum(
        (entry.get("run") or {}).get("actual_cost") or 0 for entry in record["kinds"].values()
    )
    record["limits"] = (
        "One routine run per configured runtime kind, on fictional notes. This records "
        "that a resident's session executed inside a per-run container from the pinned "
        "image, on its own login, reaching exactly the folders its grant named; that the "
        "receipt is the CLI's own stream out of that container and the run settled from "
        "the usage the CLI reported there; and that Hearth measured, from inside the "
        "sandbox network, that its own address and a LAN address are unreachable and the "
        "provider is. It is not evidence of model quality, of anything about real "
        "sources, or of a fence narrower than the one `deploy/fence.sh` installs."
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
