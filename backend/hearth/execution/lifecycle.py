"""One supervised lifecycle for execution, recovery, and terminal accounting."""

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict

from hearth.execution import usage as usage_accounting
from hearth.execution.context import read_context
from hearth.inputs.selection import run_inputs
from hearth.integrations import interface
from hearth.integrations.interface import Evidence, Runtime
from hearth.integrations.launcher import written_mounts
from hearth.integrations.logins import RESIDENT, Logins
from hearth.management.authority import run_mounts
from hearth.observation.notifications import record
from hearth.residents.journal import JournalFiles, run_journal
from hearth.residents.lifecycle import check_not_archived, read_lifecycle
from hearth.residents.memory import Memory
from hearth.residents.models import Refused, Run, microdollars
from hearth.skills.assignments import run_skills
from hearth.storage.artifacts import Artifact, Artifacts
from hearth.work.letters import settle_letter
from hearth.work.service import ACTIVE_RUNS, Hearth, _audit


def unreadable_context(error: Refused) -> bool:
    """A run whose own pinned context cannot be read is interrupted, not fatal to the pass."""
    return any(
        error.code.startswith(prefix)
        for prefix in ("memory_", "invalid_memory_", "journal_", "skill_", "input_")
    )


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
            revision = db.execute(
                "SELECT revision FROM residents WHERE id=?", (row["resident_id"],)
            ).fetchone()
            # A detached worker rechecks the declaration it pinned before launching.
            if revision is None or revision[0] != row["resident_revision"]:
                raise Refused("run_declaration_changed")
            check_not_archived(db, row["resident_id"])
            yield db

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
                artifact = self.artifacts.publish(run_id, evidence.output)
                db.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)",
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
            # If this run was working a letter, the letter ends here too, in this same
            # transaction: answered, worked and left unanswered, or failed with the run.
            settle_letter(
                db,
                row["task_id"],
                run_id=run_id,
                resident_id=row["resident_id"],
                status=evidence.status,
                artifact_id=artifact.id if artifact else None,
                now=now,
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
            # A folder this run was granted `rw` and is seen to have written into is a
            # change to the host, so it is recorded as its own fact, in this same
            # transaction, before the run's ending. What the receipt says is only a
            # name: which folder that name means, and whether this run really held it
            # writable, is read from what admission pinned
            # (`docs/adr/0016-sandbox-per-run.md`).
            written = set(written_mounts(_usage_receipt))
            for mount in run_mounts(db, run_id) if written else []:
                if mount["name"] in written and mount["mode"] == "rw":
                    _audit(
                        db,
                        "run.mount_rw_used",
                        run_id,
                        now,
                        {
                            "task_id": row["task_id"],
                            "resident_id": row["resident_id"],
                            "name": mount["name"],
                            "host_path": mount["host_path"],
                            "grant_revision": mount["grant_revision"],
                        },
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
                }
                | ({"accounting": pricing | {"receipt_sha256": receipt_digest}} if pricing else {}),
            )
            record(db, "run." + evidence.status, run_id, now)
        return self.hearth.run(run_id)

    def runtime_unavailable(
        self, run_id: str, owner_token: str, *, reason: str = "runtime_unavailable"
    ) -> Run:
        """A run this instance cannot launch, for a reason an operator can fix, waits.

        Two things get here. A run whose pinned runtime this instance is not configured
        for, and a run pinned to a resident's own provider login that has lapsed or been
        taken away (`docs/adr/0016-sandbox-per-run.md`). They are one wait because they
        are one situation: the work is admitted, the machine cannot do it yet, and
        nobody's money has been spent.

        The run named its runtime at admission and that pin is where its work happens;
        no other provider can be asked what it did, and launching it on one would be a
        different run against the same reservation. So the run is left exactly as
        unknown as it is -- never launched here, never retried, and never settled at a
        number nobody can produce evidence for. A priced run settles from its own
        provider's receipt, and there is no receipt for a session this instance cannot
        see.

        It is a wait and not an ending because the usual cause is a configuration this
        machine is missing rather than a provider the household has lost, and work is
        not thrown away over an environment variable: the run is worked as soon as the
        runtime is configured again. An operator who wants it gone cancels it, and a
        run that was never launched then settles at zero through the ordinary path, on
        a receipt the registry builds from the store's own pins rather than from the
        provider.

        A run that was never launched is therefore left exactly as it is -- still
        waiting to start, which is the truth about it -- rather than moved to a state
        it could never start from again. Why the runtime is absent, and which resident
        login has lapsed, are each recorded once where they are discovered:
        `app.configured_runtimes` and the login survey, both at start.
        """
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None or row["owner_token"] != owner_token:
                raise Refused("run_ownership_lost")
            if row["finished_at"] is not None:
                raise Refused("run_already_finished")
            if not row["launch_attempted"]:
                return self.hearth.run(run_id)
            # Cancellation intent survives a wait, exactly as it survives observation.
            state = "stopping" if row["cancellation_requested"] else "interrupted"
            if row["status"] != state:
                now = int(self.hearth.clock())
                db.execute("UPDATE runs SET status=? WHERE id=?", (state, run_id))
                db.execute("UPDATE tasks SET status=? WHERE id=?", (state, row["task_id"]))
                _audit(
                    db,
                    "run." + state,
                    run_id,
                    now,
                    {
                        "task_id": row["task_id"],
                        "reason": "runtime_unavailable",
                        "runtime_kind": row["runtime_kind"],
                    },
                )
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
    Only confirmed terminal evidence releases resident ownership.

    One store may be configured for several runtimes at once, so the executor holds
    them by kind and every run is worked by the one its own admission pinned
    (`docs/adr/0015-runtime-per-resident.md`). No run is ever handed to another
    provider's adapter, whatever the store's default becomes afterwards.
    """

    def __init__(self, execution: Execution, runtimes, logins: Logins | None = None):
        """`runtimes` is one runtime, or the several this instance is configured for.

        `logins` is what this instance knows about the residents holding a provider
        login of their own; without one no run is ever held for a login, which is what
        an instance with no resident logins on disk means anyway.
        """
        adapters = [runtimes] if hasattr(runtimes, "kind") else list(runtimes)
        self.execution = execution
        self.runtimes: dict[str, Runtime] = {adapter.kind: adapter for adapter in adapters}
        self.logins = logins
        self.lock_path = execution.hearth.database.path.resolve().with_suffix(".executor.lock")

    @property
    def runtime(self) -> Runtime:
        """The runtime, where this instance is configured for exactly one of them.

        Most are: one household, one provider. An instance configured for several has
        no single answer and says so rather than picking one, because every question
        worth asking of it is about a particular run's own pinned kind.
        """
        (only,) = self.runtimes.values()
        return only

    def held(self, run: Run) -> bool:
        """Is this run's own login a reason to wait rather than launch it?

        Only ever asked of a run pinned to a resident's own login: the household's is
        probed when its adapter opens, and a household login that does not work leaves
        no adapter for these runs to be handed to at all.
        """
        return (
            self.logins is not None
            and run.login_scope == RESIDENT
            and self.logins.holds(run.resident_id, run.runtime_kind)
        )

    def _step(self, run, runtime: Runtime | None):
        if run.cancellation_requested and not run.launch_attempted:
            # Nothing was launched, so this settles at zero from the run's own pin,
            # which the registry answers for whether or not that provider is
            # configured here -- as long as the store holds the pins that receipt has
            # to name. A store that never got them cannot prove this run's zero, and
            # one run nobody can settle waits alone rather than ending the pass for
            # every other resident (`docs/adr/0015-runtime-per-resident.md`). It
            # settles as soon as the runtime is configured and writes its pin.
            try:
                with self.execution.hearth.database.transaction() as db:
                    row = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
                    receipt = interface.cancellation_receipt(
                        run.runtime_kind,
                        usage_accounting.binding(db, row),
                        usage_accounting.runtime_pins(db, run.id),
                    )
            except Refused:
                return self.execution.runtime_unavailable(run.id, run.owner_token)
            return self.execution.finish(
                run.id, run.owner_token, Evidence("cancelled", cost=0), _usage_receipt=receipt
            )
        if runtime is None:
            # This instance is not configured for the runtime this run was pinned to,
            # so nobody here can observe it, stop it or launch it. It waits, visibly.
            return self.execution.runtime_unavailable(run.id, run.owner_token)
        if run.cancellation_requested:
            runtime.stop(run.id)
        evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
        # There is no safe detached-start replay after a lost launch reply.
        if evidence.status == "absent" and not run.launch_attempted:
            if self.held(run):
                # A run admitted to spend its resident's own provider login, whose
                # login has lapsed or been taken away. It waits for the operator; it
                # does not fall back to the household's, because that would spend a
                # subscription nobody chose for this resident
                # (`docs/adr/0016-sandbox-per-run.md`). This resident alone is held:
                # every other one is launched by the same pass.
                return self.execution.runtime_unavailable(
                    run.id, run.owner_token, reason="login_required"
                )
            if self.execution.prepare_start(run.id, run.owner_token):
                try:
                    with self.execution.hearth.database.transaction() as db:
                        context = read_context(db, run.id, Memory(self.execution.hearth).files)
                except Refused as error:
                    if not unreadable_context(error):
                        raise
                    # One run's unreadable pinned context never stops the whole pass.
                    return self.execution.observe(run.id, run.owner_token, "interrupted")
                runtime.start(run.id, json.dumps(context, sort_keys=True, separators=(",", ":")))
                evidence = runtime.inspect(run.id, expected_digest=run.input_digest)
        if evidence.status in {"succeeded", "failed", "cancelled"}:
            return self.execution.finish(
                run.id,
                run.owner_token,
                evidence,
                _usage_receipt=interface.runtime_receipt(runtime, run.id),
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
            # The store's own default has to be one of the runtimes configured here: an
            # instance that cannot work what this store admits next would queue work
            # nothing is ever going to start.
            if self.execution.hearth.database.runtime_kind() not in self.runtimes:
                raise Refused("runtime_store_mismatch")
            results = []
            for run in self.execution.active():
                runtime = self.runtimes.get(run.runtime_kind)
                if runtime is not None and run.runtime_version != runtime.version:
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
                            run_journal(
                                db,
                                JournalFiles(Memory(self.execution.hearth).files.root),
                                run.id,
                            )
                    except Refused:
                        results.append(
                            self.execution.observe(run.id, run.owner_token, "interrupted")
                        )
                        continue
                results.append(self._step(run, runtime))
            return results
