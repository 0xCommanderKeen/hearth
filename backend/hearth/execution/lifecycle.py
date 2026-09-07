"""One supervised lifecycle for mock execution, recovery, and terminal accounting."""

import fcntl
import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict

from hearth.execution import usage as usage_accounting
from hearth.execution.context import read_context
from hearth.inputs.selection import run_inputs
from hearth.integrations import interface
from hearth.integrations.interface import Evidence, Runtime
from hearth.observation.notifications import enqueue
from hearth.residents.lifecycle import check_not_archived, read_lifecycle
from hearth.residents.memory import Memory
from hearth.residents.models import Refused, Run, microdollars
from hearth.skills.assignments import run_skills
from hearth.storage.artifacts import Artifact, Artifacts
from hearth.work.service import ACTIVE_RUNS, Hearth, _audit


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
            if read_lifecycle(db, row["resident_id"])["state"] == "archived":
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
                not interface.supports_dispatch(row["runtime_kind"], row["runtime_version"])
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
            if usage_accounting.pricing(db, run_id) is not None and not interface.uses_receipts(
                row["runtime_kind"]
            ):
                raise Refused("run_requires_codex_worker")
            revision = db.execute(
                "SELECT revision FROM residents WHERE id=?", (row["resident_id"],)
            ).fetchone()
            # Inline execution already pinned its declaration at prepare_start;
            # detached workers retain their stricter prelaunch revision recheck.
            if row["runtime_kind"] != "inline_mock" and (
                revision is None or revision[0] != row["resident_revision"]
            ):
                raise Refused("run_declaration_changed")
            check_not_archived(db, row["resident_id"])
            yield db

    def finish_from_usage(self, run_id: str, owner_token: str, journal) -> Run:
        with self.hearth.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            expected = usage_accounting.binding(db, row)
        if journal.binding != expected:
            raise Refused("run_usage_binding_mismatch")
        try:
            with journal.snapshot() as receipt:
                _, _, evidence = usage_accounting.encode_receipt(receipt, expected)
                return self.finish(run_id, owner_token, evidence, _usage_receipt=receipt)
        except Refused:
            raise
        except ValueError, OSError, TypeError, KeyError:
            raise Refused("run_usage_invalid") from None

    def finish(
        self, run_id: str, owner_token: str, evidence: Evidence, *, _usage_receipt=None
    ) -> Run:
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
            pricing = usage_accounting.pricing(db, run_id)
            receipt_digest = None
            if pricing is not None:
                if _usage_receipt is None:
                    raise Refused("run_usage_required")
                before_launch = interface.validate_receipt_pins(
                    row["runtime_kind"],
                    _usage_receipt,
                    usage_accounting.runtime_pins(db, run_id),
                    cancelled=bool(row["cancellation_requested"]),
                )
                if not row["launch_attempted"] and not before_launch:
                    raise Refused("run_launch_intent_required")
                raw, receipt_digest, evidence = usage_accounting.encode_receipt(
                    _usage_receipt, usage_accounting.binding(db, row)
                )
                db.execute("INSERT INTO run_usage VALUES (?,?,?)", (run_id, raw, receipt_digest))
            elif _usage_receipt is not None:
                raise Refused("run_pricing_required")
            now = int(self.hearth.clock())
            artifact = None
            if evidence.status == "succeeded":
                assert evidence.output is not None
                artifact = self.artifacts.publish(
                    run_id, evidence.output, simulated=interface.simulated(row["runtime_kind"])
                )
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
                    "simulated": interface.simulated(row["runtime_kind"]),
                }
                | ({"accounting": pricing | {"receipt_sha256": receipt_digest}} if pricing else {}),
            )
            enqueue(db, "run." + evidence.status, run_id, now)
        return self.hearth.run(run_id)

    def artifact(self, artifact_id: str) -> tuple[Artifact, str]:
        with self.hearth.database.transaction() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                raise Refused("artifact_not_found")
            values = dict(row)
            values["simulated"] = bool(row["simulated"])
            artifact = Artifact(**values)
        return artifact, self.artifacts.read(artifact)


class Executor:
    """A single active step owns start/inspect/stop; durable runtime IDs survive restart.

    Runtime adapters must make repeated start(run_id, same instruction) idempotent.
    Only confirmed terminal evidence releases resident ownership.
    """

    def __init__(self, execution: Execution, runtime: Runtime):
        self.execution = execution
        self.runtime = runtime
        self.lock_path = execution.hearth.database.path.resolve().with_suffix(".executor.lock")

    def _step_receipted(self, run):
        if run.cancellation_requested and not run.launch_attempted:
            with self.execution.hearth.database.transaction() as db:
                row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
                receipt = interface.cancellation_receipt(
                    self.runtime.kind,
                    usage_accounting.binding(db, row),
                    usage_accounting.runtime_pins(db, run.id),
                )
            return self.execution.finish(
                run.id, run.owner_token, Evidence("cancelled", cost=0), _usage_receipt=receipt
            )
        if run.cancellation_requested:
            self.runtime.stop(run.id)
        evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
        if evidence.status == "absent" and interface.may_start(
            self.runtime.kind, status=run.status, launch_attempted=bool(run.launch_attempted)
        ):
            if self.execution.prepare_start(run.id, run.owner_token):
                with self.execution.hearth.database.transaction() as db:
                    context = read_context(db, run.id, Memory(self.execution.hearth).files)
                self.runtime.start(
                    run.id, json.dumps(context, sort_keys=True, separators=(",", ":"))
                )
                evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
        if evidence.status in {"succeeded", "failed", "cancelled"}:
            return self.execution.finish(
                run.id,
                run.owner_token,
                evidence,
                _usage_receipt=interface.runtime_receipt(self.runtime, run.id),
            )
        return self.execution.observe(
            run.id, run.owner_token, "running" if evidence.status == "running" else "interrupted"
        )

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
                if (
                    run.status == "starting"
                    and not run.launch_attempted
                    and not run.cancellation_requested
                ):
                    try:
                        with self.execution.hearth.database.transaction() as db:
                            run_skills(db, run.id)
                            run_inputs(db, run.id)
                    except Refused:
                        results.append(
                            self.execution.observe(run.id, run.owner_token, "interrupted")
                        )
                        continue
                if interface.uses_receipts(self.runtime.kind):
                    results.append(self._step_receipted(run))
                    continue
                with self.execution.hearth.database.transaction() as db:
                    priced = usage_accounting.pricing(db, run.id) is not None
                if priced:
                    results.append(self.execution.observe(run.id, run.owner_token, "interrupted"))
                    continue
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
                            or error.code.startswith("skill_")
                            or error.code.startswith("input_")
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
                        if self.runtime.kind == "inline_mock":
                            with self.execution.hearth.database.transaction() as db:
                                epoch = db.execute(
                                    "SELECT value FROM system_meta WHERE key='epoch'"
                                ).fetchone()[0]
                            try:
                                # The inline simulation has no detached worker; serialize
                                # its actual bounded start with archive here.
                                with self.execution.dispatch_guard(
                                    run.id,
                                    run.owner_token,
                                    epoch=epoch,
                                    input_digest=run.input_digest,
                                ):
                                    self.runtime.start(run.id, instruction)
                            except Refused as error:
                                if error.code != "resident_archived":
                                    raise
                        else:
                            self.runtime.start(run.id, instruction)
                    evidence = self.runtime.inspect(run.id, expected_digest=run.input_digest)
                if evidence.status in {"succeeded", "failed", "cancelled"}:
                    results.append(self.execution.finish(run.id, run.owner_token, evidence))
                elif evidence.status == "running":
                    results.append(self.execution.observe(run.id, run.owner_token, "running"))
                else:
                    results.append(self.execution.observe(run.id, run.owner_token, "interrupted"))
            return results
