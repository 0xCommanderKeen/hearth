"""Durable two-case validation, advanced by the ordinary supervisor without model waits."""

import json
import uuid

from hearth.execution.context import CONTEXT_VERSION
from hearth.inputs.catalog import Inputs
from hearth.residents.lifecycle import check_ready
from hearth.residents.models import Refused, identifier
from hearth.skills.authoring import check_editor, digest, read_authoring
from hearth.skills.evaluation import (
    REQUEST_FIELDS,
    check_request_authority,
    checked_validation,
    request_context,
    result_for_case,
)
from hearth.storage.artifacts import Artifacts
from hearth.work.service import _audit, _queue_task


def read_validation(db, validation_id):
    validation, _, _ = checked_validation(db, validation_id)
    cases = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM skill_validation_cases WHERE validation_id=? ORDER BY position",
            (validation_id,),
        )
    ]
    for case in cases:
        case["kind"] = "normal" if case["position"] == 0 else "edge"
        case["result"] = json.loads(case["result"]) if case["result"] is not None else None
    return validation | {
        "validation_id": validation_id,
        "cases": cases,
        "assessment": "deterministic_assertions_on_model_runs",
        "link": "/#skills/" + validation["skill_id"],
        "checker": "output-assertions-v1",
    }


def _runner(db, actor, resident_id, reserve):
    """The resident whose examples these are: the one that asked, or the one named.

    A resident validates its own draft and nobody else's, so its declaration, memory,
    allowance and single run slot are what the examples actually cost. An operator has
    none of those and says whose they are.
    """
    if actor != "operator":
        if resident_id not in {None, actor}:
            raise Refused("skill_validation_resident_out_of_scope")
        resident_id = actor
    if resident_id is None:
        raise Refused("skill_validation_resident_required")
    identifier(resident_id)
    check_ready(db, resident_id)
    context = request_context(db, resident_id)
    if context["daily_limit"] < reserve:
        raise Refused("skill_validation_budget_insufficient")
    return resident_id, context


def request_validation(
    db, hearth, skill_id, revision, reserve, *, actor, resident_id=None, authority=None
):
    from hearth.skills.assignments import exact_skill

    identifier(skill_id)
    check_editor(db, skill_id, actor)
    if type(reserve) is not int or not 1 <= reserve <= 500_000:
        raise Refused("skill_evaluation_reservation_invalid")
    if authority is not None:
        if reserve > authority["grant"]["max_reserve"]:
            raise Refused("management_reservation_limit")
        # Admission rechecks this; refusing here says so before the request is durable.
        runtime = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]
        if runtime not in authority["grant"]["profiles"]:
            raise Refused("management_profile_not_permitted")
    candidate = exact_skill(db, skill_id, revision)
    authored = read_authoring(db, skill_id, revision)
    if candidate["status"] != "draft" or authored is None:
        raise Refused("skill_draft_required")
    previous = db.execute(
        "SELECT id FROM skill_validations WHERE skill_id=? AND candidate_revision=?",
        (skill_id, revision),
    ).fetchone()
    if previous:
        return read_validation(db, previous[0])
    current = db.execute("SELECT revision FROM skills WHERE id=?", (skill_id,)).fetchone()[0]
    if current != revision:
        raise Refused("revision_conflict")
    validation_id = str(uuid.uuid4())
    now = int(hearth.clock())
    passed = authored["structure"]["passed"]
    runner, context = _runner(db, actor, resident_id, reserve) if passed else (None, {})
    value = dict(
        id=validation_id,
        skill_id=skill_id,
        candidate_revision=revision,
        candidate_sha256=candidate["sha256"],
        manifest_sha256=authored["manifest_sha256"],
        resident_id=runner,
        resident_revision=context.get("resident_revision"),
        memory_revision=context.get("memory_revision"),
        context_version=CONTEXT_VERSION if runner else None,
        actor=actor,
        originating_run_id=authority["run_id"] if authority else None,
        grant_revision=authority["grant"]["revision"] if authority else None,
        reserve=reserve,
        created_at=now,
        # The cases wait for the resident's own run to end, so the window is a day
        # rather than the ten minutes a separate evaluator could always start within.
        expires_at=now + 86400,
    )
    db.execute(
        "INSERT INTO skill_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(value[key] for key in REQUEST_FIELDS)
        + (
            digest(value),
            "pending" if passed else "failed",
            None if passed else "skill_structure_failed",
        ),
    )
    if passed:
        assert runner is not None
        for position, example in enumerate(authored["examples"]):
            source = Inputs(hearth).save_in_transaction(
                db,
                "skill-case-input:" + validation_id + ":" + str(position),
                name="Synthetic " + example["kind"] + " example: " + candidate["name"][:75],
                notes=example["notes"],
                actor=actor,
            )
            from hearth.inputs.catalog import read_input

            source = read_input(db, source["input_set_id"], source["revision"])
            task_id = _queue_task(
                db,
                runner,
                example["instruction"],
                now,
                {
                    "skill_validation_id": validation_id,
                    "position": position,
                    "actor": actor,
                    "originating_run_id": value["originating_run_id"],
                },
            )
            db.execute(
                "INSERT INTO skill_validation_cases VALUES (?,?,?,NULL,?,?,?,NULL)",
                (
                    validation_id,
                    position,
                    task_id,
                    source["input_set_id"],
                    source["revision"],
                    source["sha256"],
                ),
            )
    _audit(db, "skill.validation_requested", skill_id, now, value | {"structure_passed": passed})
    return read_validation(db, validation_id)


def checked_publication(db, hearth, skill_id, expected_revision, validation_id):
    validation, candidate, _ = checked_validation(db, validation_id)
    if (
        validation["skill_id"] != skill_id
        or validation["candidate_revision"] != expected_revision
        or validation["status"] != "passed"
    ):
        raise Refused("skill_validation_not_passed")
    cases = list(
        db.execute(
            "SELECT * FROM skill_validation_cases WHERE validation_id=? ORDER BY position",
            (validation_id,),
        )
    )
    if len(cases) != 2 or [case["position"] for case in cases] != [0, 1]:
        raise Refused("skill_validation_cases_changed")
    artifacts = Artifacts(hearth.database.path.parent / "artifacts")
    for case in cases:
        result = result_for_case(db, validation, case, artifacts)
        if result is None or not result["passed"] or result != json.loads(case["result"]):
            raise Refused("skill_validation_evidence_changed")
    return candidate


class Validation:
    def __init__(self, hearth):
        self.hearth = hearth

    def read(self, validation_id):
        identifier(validation_id)
        with self.hearth.database.transaction() as db:
            return read_validation(db, validation_id)

    def request(self, skill_id, revision, reserve=100000, *, resident_id=None, actor="operator"):
        with self.hearth.database.transaction(write=True) as db:
            return request_validation(
                db, self.hearth, skill_id, revision, reserve, actor=actor, resident_id=resident_id
            )

    def step(self):
        with self.hearth.database.transaction() as db:
            pending = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM skill_validations WHERE status='pending' "
                    "ORDER BY created_at,id LIMIT 32"
                )
            ]
        for validation_id in pending:
            try:
                self._advance(validation_id)
            except Refused as error:
                with self.hearth.database.transaction(write=True) as db:
                    self._state(db, validation_id, "failed", error.code)

    def _state(self, db, validation_id, status, reason=None):
        row = db.execute(
            "SELECT status,reason FROM skill_validations WHERE id=?", (validation_id,)
        ).fetchone()
        if row is not None and tuple(row) != (status, reason):
            db.execute(
                "UPDATE skill_validations SET status=?,reason=? WHERE id=?",
                (status, reason, validation_id),
            )
            _audit(
                db,
                "skill.validation_progress",
                validation_id,
                int(self.hearth.clock()),
                {"status": status, "reason": reason},
            )

    def _advance(self, validation_id):
        with self.hearth.database.transaction(write=True) as db:
            validation, _, _ = checked_validation(db, validation_id)
            if validation["status"] != "pending":
                return
            cases = list(
                db.execute(
                    "SELECT * FROM skill_validation_cases WHERE validation_id=? ORDER BY position",
                    (validation_id,),
                )
            )
            if len(cases) != 2:
                raise Refused("skill_validation_cases_changed")
            for case in cases:
                if case["result"] is not None:
                    if not json.loads(case["result"])["passed"]:
                        self._state(db, validation_id, "failed", "skill_example_failed")
                        return
                    continue
                if case["run_id"] is not None:
                    try:
                        result = result_for_case(
                            db,
                            validation,
                            case,
                            Artifacts(self.hearth.database.path.parent / "artifacts"),
                        )
                    except Refused as error:
                        if error.code == "skill_evaluation_usage_unknown":
                            self._state(db, validation_id, "pending", error.code)
                            return
                        raise
                    if result is None:
                        self._state(db, validation_id, "pending", "skill_evaluation_running")
                        return
                    db.execute(
                        "UPDATE skill_validation_cases SET result=? "
                        "WHERE validation_id=? AND position=?",
                        (json.dumps(result, sort_keys=True), validation_id, case["position"]),
                    )
                    _audit(
                        db, "skill.example_checked", validation_id, int(self.hearth.clock()), result
                    )
                    if not result["passed"]:
                        self._state(db, validation_id, "failed", "skill_example_failed")
                        return
                    continue
                try:
                    check_request_authority(db, validation, int(self.hearth.clock()))
                except Refused as error:
                    # An edited declaration is repairable and the request outlives one
                    # working day, so the queued case identities wait for the resident
                    # they were promised rather than failing the draft outright.
                    if error.code == "skill_validation_resident_changed":
                        self._state(db, validation_id, "pending", error.code)
                        return
                    raise
                db.execute("SAVEPOINT validation_admission")
                try:
                    run = self.hearth.admit_in_transaction(
                        db, case["task_id"], reserve=validation["reserve"]
                    )
                except Refused as error:
                    db.execute("ROLLBACK TO validation_admission")
                    if error.code in {
                        "resident_busy",
                        "resident_paused",
                        "resident_archived",
                        "capacity_exhausted",
                        "budget_exhausted",
                        "household_budget_exhausted",
                        "household_concurrency_limit",
                    }:
                        self._state(db, validation_id, "pending", error.code)
                        return
                    raise
                finally:
                    db.execute("RELEASE validation_admission")
                db.execute(
                    "UPDATE skill_validation_cases SET run_id=? "
                    "WHERE validation_id=? AND position=?",
                    (run.id, validation_id, case["position"]),
                )
                _audit(
                    db,
                    "skill.example_admitted",
                    validation_id,
                    int(self.hearth.clock()),
                    {"position": case["position"], "run_id": run.id, "task_id": case["task_id"]},
                )
                self._state(db, validation_id, "pending", "skill_evaluation_running")
                return
            self._state(db, validation_id, "passed")
