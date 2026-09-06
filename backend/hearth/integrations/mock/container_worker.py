"""Trusted asynchronous orchestration of the fixed offline container fixture."""

import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from hearth.execution.lifecycle import Execution
from hearth.integrations.interface import Evidence
from hearth.integrations.mock.container import ContainerRehearsal, Observation, container_lock
from hearth.integrations.mock.process import write_json
from hearth.residents.models import Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth


def container_evidence(observation: Observation, *, cancelled: bool) -> Evidence:
    """Synthetic accounting is fixture policy, never inferred provider usage."""
    if observation.status == "running":
        return Evidence("running")
    if observation.status != "exited" or observation.transcript is None:
        return Evidence("unknown")
    transcript = observation.transcript
    if transcript.status == "completed" and transcript.output:
        return Evidence(
            "succeeded",
            "Simulation — no model was called.\n\n" + transcript.output,
            cost=2_000,
        )
    if cancelled:
        return Evidence("cancelled", cost=1_000)
    return Evidence("failed", cost=None)


def _runtime(folder: Path, *, docker=None) -> ContainerRehearsal:
    database = Database(folder.parent.parent / "hearth.db")
    if database.restored():
        raise Refused("restored_copy_read_only")
    if database.runtime_kind() != "process_mock" or database.process_boundary() != "container":
        raise Refused("runtime_store_mismatch")
    return ContainerRehearsal(database, folder.parent.parent / "container-runs", docker=docker)


def reconcile(folder: Path, request: dict, *, docker=None) -> Evidence:
    """Only called while owning the process worker lock; never creates or starts."""
    root = folder.parent.parent / "container-runs"
    if not root.is_dir() or not (root / folder.name / "claim.json").is_file():
        return Evidence("unknown")
    runtime = _runtime(folder, docker=docker)
    claim = runtime._claim(folder.name)
    if (claim["digest"], claim["scenario"]) != (request["instruction_digest"], request["scenario"]):
        raise Refused("container_identity_conflict")
    cancelled = (folder / "cancel.json").exists()
    observation = runtime.inspect(folder.name)
    if (cancelled or time.time() >= request["deadline"]) and observation.status == "running":
        observation = runtime.stop(folder.name)
    result = container_evidence(observation, cancelled=cancelled)
    if result.status in {"succeeded", "failed", "cancelled"}:
        # A removed container is still proved terminal by its immutable receipt.
        # Cleanup failure must not erase that evidence or authorize another launch.
        try:
            runtime.remove(folder.name)
        except Refused:
            return Evidence("unknown")
        except OSError, subprocess.TimeoutExpired:
            pass
        with container_lock(runtime.root / folder.name / ".terminal.lock"):
            confirmed = runtime._receipt(runtime._claim(folder.name))
            if confirmed != observation:
                return Evidence("unknown")
            _publish_result(folder, request, result)
    return result


def _publish_result(folder: Path, request: dict, result: Evidence) -> None:
    write_json(
        folder / "result.json",
        {
            "simulated": True,
            "instruction_digest": request["instruction_digest"],
            "evidence": asdict(result),
        },
    )


def run_container(folder: Path, request: dict, *, docker=None) -> Evidence:
    runtime = _runtime(folder, docker=docker)
    execution = Execution(
        Hearth(runtime.database), Artifacts(runtime.database.path.parent / "artifacts")
    )
    authority = request["authority"]
    with runtime.database.transaction() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (folder.name,)).fetchone()
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        if (
            row is not None
            and row["owner_token"] == authority["owner_token"]
            and epoch == authority["epoch"]
            and row["finished_at"] is None
            and row["cancellation_requested"]
            and not (runtime.root / folder.name).exists()
        ):
            # This worker owns the first exclusive started marker and has not
            # created a container claim. Cancellation before dispatch costs zero.
            write_json(folder / "cancel.json", {"cancel": True})
            result = Evidence("cancelled", cost=0)
            _publish_result(folder, request, result)
            return result
    runtime.start(
        folder.name,
        scenario=request["scenario"],
        dispatch_guard=execution.dispatch_guard(
            folder.name,
            authority["owner_token"],
            epoch=authority["epoch"],
            input_digest=request["instruction_digest"],
        ),
    )
    while True:
        result = reconcile(folder, request, docker=docker)
        if result.status != "running":
            return result
        time.sleep(0.05)


def validate_result(folder: Path, request: dict, result: Evidence) -> None:
    database = Database(folder.parent.parent / "hearth.db")
    with database.transaction() as db:
        run = db.execute("SELECT * FROM runs WHERE id=?", (folder.name,)).fetchone()
        if run is None:
            raise Refused("run_not_found")
        verify_backup_run(folder.parent.parent, db, run, request, result)


def verify_backup_run(root: Path, db, run, request: dict, evidence: Evidence) -> None:
    """Validate copied evidence only. Original absolute claim paths are provenance."""
    import hashlib
    import json

    from hearth.execution.context import read_context
    from hearth.integrations.codex.events import MAX_STREAM
    from hearth.integrations.mock.container import FIXTURE, IMAGE, decode_receipt, read_document
    from hearth.residents.memory import MemoryFiles

    try:
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        held = db.execute("SELECT value FROM system_meta WHERE key='restore_hold'").fetchone()
        if held:
            provenance = json.loads(held[0])
            epoch = provenance.get("source_execution_epoch", provenance["source_epoch"])
        if request["authority"] != {"epoch": epoch, "owner_token": run["owner_token"]}:
            raise Refused("backup_runtime_invalid")
        folder = root / "container-runs" / run["id"]
        cancelled = (root / "process-mock" / run["id"] / "cancel.json").is_file()
        if not folder.exists():
            if evidence != Evidence("cancelled", cost=0) or not cancelled:
                raise Refused("backup_runtime_invalid")
            return
        claim = read_document(folder / "claim.json")
        context = root / "container-runs" / "inputs" / run["id"] / "context.json"
        expected = json.dumps(
            read_context(db, run["id"], MemoryFiles(root / "memory")),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if (
            claim["version"] != 1
            or claim["run_id"] != run["id"]
            or claim["image"] != IMAGE
            or claim["fixture_digest"] != hashlib.sha256(FIXTURE.encode()).hexdigest()
            or claim["digest"] != run["input_digest"]
            or claim["scenario"] != request["scenario"]
            or not Path(claim["input"]).is_absolute()
            or context.read_bytes() != expected
            or hashlib.sha256(expected).hexdigest() != run["input_digest"]
            or (folder / "terminal.conflict").exists()
        ):
            raise Refused("backup_runtime_invalid")
        observation = decode_receipt(
            claim,
            read_document(folder / "identity.json"),
            read_document(folder / "terminal.json", limit=MAX_STREAM),
        )
        if container_evidence(observation, cancelled=cancelled) != evidence:
            raise Refused("backup_runtime_invalid")
    except OSError, KeyError, ValueError, TypeError:
        raise Refused("backup_runtime_invalid") from None
