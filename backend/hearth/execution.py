"""One supervised lifecycle for mock execution, recovery, and terminal accounting."""

import fcntl
import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict

from hearth.artifacts import Artifact, Artifacts
from hearth.core import ACTIVE_RUNS, Hearth, _audit
from hearth.memory import Memory
from hearth.models import Refused, Run, microdollars
from hearth.notifications import enqueue
from hearth.run_context import read_context
from hearth.runtime import Evidence, Runtime


class Execution:
    """Operational transitions require the current run's ownership token."""

    def __init__(self, hearth: Hearth, artifacts: Artifacts):
        self.hearth = hearth
        self.artifacts = artifacts

    def active(self) -> list[Run]:
        with self.hearth.database.transaction() as db:
            return [
                Run(**dict(row))
                for row in db.execute(
                    f"SELECT * FROM runs WHERE status IN {ACTIVE_RUNS} ORDER BY created_at, id"
                )
            ]

    def cancel(self, run_id: str) -> Run:
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise Refused("run_not_found")
            run = Run(**dict(row))
            if run.finished_at is not None or run.cancellation_requested:
                return run
            now = int(self.hearth.clock())
            db.execute(
                "UPDATE runs SET status = 'stopping', cancellation_requested = 1 WHERE id = ?",
                (run_id,),
            )
            db.execute("UPDATE tasks SET status = 'stopping' WHERE id = ?", (run.task_id,))
            _audit(db, "run.cancel_requested", run_id, now, {"task_id": run.task_id})
        return self.hearth.run(run_id)

    def prepare_start(self, run_id: str, owner_token: str) -> bool:
        """Publish launch intent and recheck cancellation at the actual launch seam."""
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            if row["cancellation_requested"] or row["status"] != "starting":
                return False
            revision = db.execute(
                "SELECT revision FROM residents WHERE id=?", (row["resident_id"],)
            ).fetchone()[0]
            if revision != row["resident_revision"]:
                return False
            if not row["launch_attempted"]:
                db.execute("UPDATE runs SET launch_attempted = 1 WHERE id = ?", (run_id,))
                _audit(
                    db,
                    "run.launch_requested",
                    run_id,
                    int(self.hearth.clock()),
                    {"task_id": row["task_id"]},
                )
            return True

    def observe(self, run_id: str, owner_token: str, state: str) -> Run:
        if state not in {"running", "interrupted"}:
            raise Refused("invalid_observation")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            if row["finished_at"] is not None:
                raise Refused("run_already_finished")
            # Cancellation intent survives observation that the runtime is still running.
            next_state = (
                "stopping" if row["cancellation_requested"] and state == "running" else state
            )
            if row["status"] != next_state:
                db.execute("UPDATE runs SET status = ? WHERE id = ?", (next_state, run_id))
                db.execute("UPDATE tasks SET status = ? WHERE id = ?", (next_state, row["task_id"]))
                _audit(
                    db,
                    "run." + next_state,
                    run_id,
                    int(self.hearth.clock()),
                    {"task_id": row["task_id"]},
                )
        return self.hearth.run(run_id)

    @contextmanager
    def dispatch_guard(self, run_id: str, owner_token: str, *, epoch: str, input_digest: str):
        """Recheck a detached worker's authority and serialize it with actual dispatch.

        The caller holds this context only around the bounded start operation,
        after staging and container creation. SQLite writers cannot change policy
        or cancellation between the check and dispatch. A lost start reply still
        requires runtime reconciliation; this grants no retry authority.
        """
        with self.hearth.database.transaction(write=True) as db:
            current_epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if current_epoch is None or current_epoch[0] != epoch:
                raise Refused("run_epoch_changed")
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            if (
                row["runtime_kind"] != "process_mock"
                or row["runtime_version"] != 1
                or row["input_digest"] != input_digest
                or not row["launch_attempted"]
            ):
                raise Refused("run_dispatch_identity_invalid")
            if (
                row["finished_at"] is not None
                or row["cancellation_requested"]
                or row["status"] not in {"starting", "running", "interrupted"}
            ):
                raise Refused("run_dispatch_inactive")
            revision = db.execute(
                "SELECT revision FROM residents WHERE id=?", (row["resident_id"],)
            ).fetchone()
            if revision is None or revision[0] != row["resident_revision"]:
                raise Refused("run_declaration_changed")
            yield

    def finish(self, run_id: str, owner_token: str, evidence: Evidence) -> Run:
        if evidence.status not in {"succeeded", "failed", "cancelled"}:
            raise Refused("terminal_evidence_required")
        if evidence.cost is not None:
            microdollars(evidence.cost)
        if evidence.status == "succeeded" and (
            not isinstance(evidence.output, str) or not evidence.output.strip()
        ):
            raise Refused("successful_output_required")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            if row["finished_at"] is not None:
                raise Refused("run_already_finished")
            now = int(self.hearth.clock())
            artifact = None
            if evidence.status == "succeeded":
                assert evidence.output is not None
                artifact = self.artifacts.publish(run_id, evidence.output)
                db.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(asdict(artifact).values()),
                )
            db.execute(
                "UPDATE runs SET status = ?, actual_cost = ?, usage_known = ?, "
                "finished_at = ?, artifact_id = ? WHERE id = ?",
                (
                    evidence.status,
                    evidence.cost,
                    evidence.cost is not None,
                    now,
                    artifact.id if artifact else None,
                    run_id,
                ),
            )
            db.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (evidence.status, row["task_id"])
            )
            if evidence.cost is None:
                changed = db.execute(
                    "INSERT OR IGNORE INTO pauses VALUES (?, ?, ?, ?)",
                    (row["resident_id"], "usage_unknown", run_id, now),
                ).rowcount
                if changed:
                    _audit(
                        db,
                        "resident.paused",
                        row["resident_id"],
                        now,
                        {"reason": "usage_unknown", "run_id": run_id},
                    )
            _audit(
                db,
                "run." + evidence.status,
                run_id,
                now,
                {
                    "task_id": row["task_id"],
                    "actual_cost": evidence.cost,
                    "usage_known": evidence.cost is not None,
                    "artifact_id": artifact.id if artifact else None,
                    "simulated": True,
                },
            )
            enqueue(db, "run." + evidence.status, run_id, now)
        return self.hearth.run(run_id)

    def artifact(self, artifact_id: str) -> tuple[Artifact, str]:
        with self.hearth.database.transaction() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                raise Refused("artifact_not_found")
            artifact = Artifact(**dict(row))
        return artifact, self.artifacts.read(artifact)


class Executor:
    """A single active step owns start/inspect/stop; durable runtime IDs survive restart.

    Runtime adapters must make repeated start(run_id, same instruction) idempotent.
    Only confirmed terminal evidence releases resident ownership. This executor is
    wired exclusively to MockRuntime until real execution is explicitly enabled.
    """

    def __init__(self, execution: Execution, runtime: Runtime):
        self.execution = execution
        self.runtime = runtime
        self.lock_path = execution.hearth.database.path.resolve().with_suffix(".executor.lock")

    def step(self) -> list[Run]:
        if self.execution.hearth.database.restored():
            raise Refused("restored_copy_read_only")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("executor_busy") from None
            if self.execution.hearth.database.runtime_kind() != self.runtime.kind:
                raise Refused("runtime_store_mismatch")
            if self.runtime.kind == "process_mock" and (
                getattr(self.runtime, "boundary", None)
                != self.execution.hearth.database.process_boundary()
            ):
                raise Refused("runtime_store_mismatch")
            results = []
            for run in self.execution.active():
                if (run.runtime_kind, run.runtime_version) != (
                    self.runtime.kind,
                    self.runtime.version,
                ):
                    raise Refused("runtime_run_mismatch")
                evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
                if run.cancellation_requested:
                    if evidence.status == "running":
                        self.runtime.stop(run.id)
                        evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
                    elif evidence.status == "absent":
                        # A durable launch intent distinguishes never-started work from ambiguity.
                        evidence = (
                            Evidence("unknown")
                            if run.launch_attempted
                            else Evidence("cancelled", cost=0)
                        )
                elif run.status == "starting" and evidence.status == "absent":
                    try:
                        with self.execution.hearth.database.transaction() as db:
                            context = read_context(db, run.id, Memory(self.execution.hearth).files)
                    except Refused as error:
                        if not (
                            error.code.startswith("memory_")
                            or error.code.startswith("invalid_memory_")
                        ):
                            raise
                        results.append(
                            self.execution.observe(run.id, run.owner_token, "interrupted")
                        )
                        continue
                    if self.execution.prepare_start(run.id, run.owner_token):
                        instruction = json.dumps(context, sort_keys=True, separators=(",", ":"))
                        if hashlib.sha256(instruction.encode()).hexdigest() != run.input_digest:
                            results.append(
                                self.execution.observe(run.id, run.owner_token, "interrupted")
                            )
                            continue
                        self.runtime.start(run.id, instruction)
                    evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
                if evidence.status in {"succeeded", "failed", "cancelled"}:
                    results.append(self.execution.finish(run.id, run.owner_token, evidence))
                elif evidence.status == "running":
                    results.append(self.execution.observe(run.id, run.owner_token, "running"))
                else:
                    results.append(self.execution.observe(run.id, run.owner_token, "interrupted"))
            return results
