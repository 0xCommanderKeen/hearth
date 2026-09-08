"""Admission pins and immutable SQLite receipts for provider usage accounting.

Also what those receipts add up to when the operator asks a question rather than about a
run: `by_origin` gathers the runs of one chain under the task it rolls up to.
"""

import hashlib
import json

from hearth.integrations.interface import (
    encode_receipt,
    receipt_requests,
    usage_binding,
    validate_pricing,
    validate_receipt_pins,
)
from hearth.residents.models import Refused

# What one page of the origin report carries, so the operator's answer stays bounded.
MAX_ORIGINS = 100


def pricing(db, run_id: str) -> dict | None:
    row = db.execute(
        "SELECT model,mode,schedule FROM run_pricing WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    value = dict(row)
    validate_pricing(value)
    return value | {"basis": "api_equivalent_estimate"}


def details(db, run_id: str) -> dict | None:
    value = pricing(db, run_id)
    if value is None:
        return None
    receipt = db.execute(
        "SELECT receipt,sha256 FROM run_usage WHERE run_id=?", (run_id,)
    ).fetchone()
    if receipt is not None:
        value |= {
            "receipt_sha256": receipt["sha256"],
            "requests": receipt_requests(receipt["receipt"]),
        }
    return value


def binding(db, run):
    pin = pricing(db, run["id"])
    if pin is None:
        raise Refused("run_pricing_required")
    return usage_binding(run["id"], run["input_digest"], pin)


def verify_stored(db, run) -> None:
    """Backup verification uses SQLite evidence, never the former worker's files."""
    row = db.execute("SELECT receipt,sha256 FROM run_usage WHERE run_id=?", (run["id"],)).fetchone()
    if row is None or run["finished_at"] is None:
        raise Refused("backup_priced_run_unsettled")
    try:
        raw, digest, evidence = encode_receipt(json.loads(row["receipt"]), binding(db, run))
    except ValueError, TypeError:
        raise Refused("backup_runtime_invalid") from None
    if raw != row["receipt"] or digest != row["sha256"] or evidence.status != run["status"]:
        raise Refused("backup_runtime_invalid")
    try:
        before_launch = validate_receipt_pins(
            run["runtime_kind"],
            json.loads(raw),
            runtime_pins(db, run["id"]),
            cancelled=bool(run["cancellation_requested"]),
        )
    except Refused:
        raise Refused("backup_runtime_invalid") from None
    if not run["launch_attempted"] and not before_launch:
        raise Refused("backup_runtime_invalid")
    reconciliation = db.execute(
        "SELECT amount FROM usage_reconciliations WHERE run_id=?", (run["id"],)
    ).fetchone()
    cost = evidence.cost
    if reconciliation is not None:
        if cost is not None:
            raise Refused("backup_runtime_invalid")
        cost = reconciliation["amount"]
    if run["actual_cost"] != cost or bool(run["usage_known"]) != (cost is not None):
        raise Refused("backup_runtime_invalid")
    if evidence.output is not None:
        artifact = db.execute(
            "SELECT sha256 FROM artifacts WHERE id=? AND run_id=?", (run["artifact_id"], run["id"])
        ).fetchone()
        if (
            artifact is None
            or artifact["sha256"] != hashlib.sha256(evidence.output.encode()).hexdigest()
        ):
            raise Refused("backup_runtime_invalid")

    elif run["artifact_id"] is not None:
        raise Refused("backup_runtime_invalid")


def by_origin(db, *, limit: int = 30, offset: int = 0) -> dict:
    """What each question cost, gathered under the task the whole chain rolls up to.

    A letter is worked by the resident it reached, on that resident's own allowance, so
    the money one question spends is spread across as many runs as the chain has hops.
    The origin is the root task the letter carries — written from the sender's own
    admitted lineage, never from anything a caller said — and an ordinary task is its
    own origin, so every run appears under exactly one of them.

    Each run is counted once, at the one amount its own row records: settlement and an
    operator's later reconciliation both write `actual_cost` there, so a reconciled run
    is not counted twice and never as both known and unknown. A run whose usage is still
    unknown is reported as unknown rather than as nothing — its resident keeps the hold
    the unknown usage placed, and this report neither adds to nor releases it.
    """
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_ORIGINS
        or type(offset) is not int
        or offset < 0
    ):
        raise Refused("invalid_origin_page")
    rows = db.execute(
        "SELECT COALESCE(l.root_task_id,r.task_id) AS root_task_id,"
        "COUNT(*) AS runs,"
        "COALESCE(SUM(CASE WHEN r.usage_known=1 THEN r.actual_cost ELSE 0 END),0) AS known_cost,"
        "SUM(CASE WHEN r.usage_known=0 AND r.finished_at IS NOT NULL THEN 1 ELSE 0 END)"
        " AS unknown_runs,"
        "SUM(CASE WHEN r.finished_at IS NULL THEN 1 ELSE 0 END) AS active_runs,"
        "COALESCE(SUM(CASE WHEN r.finished_at IS NULL THEN r.reserved ELSE 0 END),0) AS reserved,"
        "MIN(r.created_at) AS started_at,MAX(r.created_at) AS last_at "
        "FROM runs r LEFT JOIN letters l ON l.task_id=r.task_id "
        # Grouped by the expression, never by the name it is given: `root_task_id` is
        # also a column of `letters`, and grouping by that name would gather every
        # ordinary task into one nameless origin.
        "GROUP BY COALESCE(l.root_task_id,r.task_id) "
        "ORDER BY last_at DESC,COALESCE(l.root_task_id,r.task_id) DESC LIMIT ? OFFSET ?",
        (limit + 1, offset),
    ).fetchall()
    origins = []
    for row in rows[:limit]:
        origin = db.execute(
            "SELECT resident_id,substr(instruction,1,200) AS instruction,created_at "
            "FROM tasks WHERE id=?",
            (row["root_task_id"],),
        ).fetchone()
        origins.append(
            dict(row)
            | {
                "resident_id": origin["resident_id"] if origin else None,
                "instruction": origin["instruction"] if origin else "",
                "created_at": origin["created_at"] if origin else row["started_at"],
                # Who spent time on this question, and how many letters it turned into —
                # including the ones nobody ever worked, which cost nothing and are still
                # part of what was asked.
                "residents_involved": [
                    name
                    for (name,) in db.execute(
                        "SELECT DISTINCT r.resident_id FROM runs r "
                        "LEFT JOIN letters l ON l.task_id=r.task_id "
                        "WHERE COALESCE(l.root_task_id,r.task_id)=? ORDER BY r.resident_id",
                        (row["root_task_id"],),
                    )
                ],
                "letters": db.execute(
                    "SELECT COUNT(*) FROM letters WHERE root_task_id=?", (row["root_task_id"],)
                ).fetchone()[0],
            }
        )
    return {
        "limit": limit,
        "offset": offset,
        "origins": origins,
        "truncated": len(rows) > limit,
    }


def runtime_pins(db, run_id: str | None = None) -> dict:
    result = dict(db.execute("SELECT key,value FROM system_meta"))
    if run_id is not None:
        row = db.execute("SELECT * FROM run_management WHERE run_id=?", (run_id,)).fetchone()
        result["management"] = dict(row) if row is not None else None
    return result
