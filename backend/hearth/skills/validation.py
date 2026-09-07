"""Durable two-case validation, advanced by the ordinary supervisor without model waits."""

import json
import uuid

from hearth.inputs.catalog import Inputs
from hearth.management.authority import read_grant
from hearth.residents.models import Refused, identifier
from hearth.residents.provisioning import Provisioning
from hearth.skills.authoring import check_editor, digest, read_authoring
from hearth.skills.evaluation import (
    EVALUATOR_INSTRUCTIONS,
    EVALUATOR_PURPOSE,
    REQUEST_FIELDS,
    check_request_authority,
    checked_validation,
    evaluator_context,
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
    simulated = (
        db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]
        != "codex_subscription"
    )
    return validation | {
        "validation_id": validation_id,
        "cases": cases,
        "assessment": "deterministic_assertions_on_simulated_runs"
        if simulated
        else "deterministic_assertions_on_model_runs",
        "link": "/#skills/" + validation["skill_id"],
        "checker": "output-assertions-v1",
    }


def _evaluator(db, hearth, authority, reserve):
    runtime = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]
    if authority is not None and runtime not in authority["grant"]["profiles"]:
        raise Refused("management_profile_not_permitted")
    previous = db.execute("SELECT value FROM system_meta WHERE key='skill_evaluator'").fetchone()
    if previous is None:
        daily_limit = (
            min(2_000_000, authority["grant"]["max_daily_limit"]) if authority else 2_000_000
        )
        if daily_limit < reserve:
            raise Refused("skill_evaluator_budget_insufficient")
        # author_skills explicitly permits this one service-owned read-only resident.
        # Ordinary provisioning still enforces the household resident count.
        receipt = Provisioning(hearth).create_evaluator_in_transaction(
            db,
            dict(
                name="Skill evaluator",
                purpose=EVALUATOR_PURPOSE,
                instructions=EVALUATOR_INSTRUCTIONS,
                execution_profile=runtime,
                daily_limit=daily_limit,
                creation_reason=(
                    "One visible read-only evaluator permitted by skill authoring policy; "
                    "all runs count toward household limits."
                ),
            ),
            actor=authority["actor"] if authority else "operator",
            originating_run_id=authority["run_id"] if authority else None,
        )
        if receipt["status"] != "ready":
            raise Refused(receipt["reason"])
        evaluator_id = receipt["resident_id"]
        db.execute("INSERT INTO system_meta VALUES ('skill_evaluator',?)", (evaluator_id,))
    else:
        evaluator_id = previous[0]
    context = evaluator_context(db, evaluator_id)
    if read_grant(db, evaluator_id)["enabled"]:
        raise Refused("skill_evaluator_changed")
    if context["daily_limit"] < reserve:
        raise Refused("skill_evaluator_budget_insufficient")
    return evaluator_id


def request_validation(db, hearth, skill_id, revision, reserve, *, actor, authority=None):
    from hearth.skills.assignments import exact_skill

    identifier(skill_id)
    check_editor(db, skill_id, actor)
    if type(reserve) is not int or not 1 <= reserve <= 500_000:
        raise Refused("skill_evaluation_reservation_invalid")
    if authority is not None and reserve > authority["grant"]["max_reserve"]:
        raise Refused("management_reservation_limit")
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
    evaluator_id = _evaluator(db, hearth, authority, reserve) if passed else None
    value = dict(
        id=validation_id,
        skill_id=skill_id,
        candidate_revision=revision,
        candidate_sha256=candidate["sha256"],
        manifest_sha256=authored["manifest_sha256"],
        evaluator_id=evaluator_id,
        evaluator_revision=evaluator_context(db, evaluator_id)["resident_revision"]
        if evaluator_id
        else None,
        actor=actor,
        originating_run_id=authority["run_id"] if authority else None,
        grant_revision=authority["grant"]["revision"] if authority else None,
        reserve=reserve,
        created_at=now,
        expires_at=now + 600,
    )
    db.execute(
        "INSERT INTO skill_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(value[key] for key in REQUEST_FIELDS)
        + (
            digest(value),
            "pending" if passed else "failed",
            None if passed else "skill_structure_failed",
        ),
    )
    if passed:
        assert evaluator_id is not None
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
                evaluator_id,
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

    def request(self, skill_id, revision, reserve=100000, *, actor="operator"):
        with self.hearth.database.transaction(write=True) as db:
            return request_validation(db, self.hearth, skill_id, revision, reserve, actor=actor)

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
                    if error.code in {
                        "skill_evaluator_context_changed",
                        "skill_evaluator_memory_not_empty",
                        "skill_evaluator_must_be_read_only",
                    }:
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
