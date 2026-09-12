"""Exact-run tools; durable reads are performed only by the communications worker."""

import json
from dataclasses import asdict

from pydantic import Field, ValidationError

from hearth.channels.chat.authority import check_scope, granted_at_admission
from hearth.channels.chat.model import Destination, Strict
from hearth.channels.chat.service import origin
from hearth.channels.delivery.service import Delivery
from hearth.management.authority import digest
from hearth.management.bridge import authorize, response
from hearth.residents.models import Refused
from hearth.work.service import _audit

MAX_CALLS = 32
MAX_PUBLICATIONS = 4


class HistoryRequest(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    destination: Destination
    before: str | None = Field(default=None, pattern=r"^[0-9]{1,128}$")
    limit: int = Field(default=20, ge=1, le=50)


class Announcement(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    destination: Destination
    text: str = Field(min_length=1, max_length=2000)


TOOLS = {
    "hearth_read_channel_history": (
        HistoryRequest,
        "Read bounded, least-trusted channel text with provenance. Retain operation_id "
        "and retry the same request while queued; no URLs or attachments are fetched.",
    ),
    "hearth_publish_announcement": (
        Announcement,
        "Queue a plain announcement to an explicitly granted destination. Retain "
        "operation_id; queued is not confirmed delivery.",
    ),
}


def availability(db, run_id):
    return bool(origin(db, run_id) or granted_at_admission(db, run_id))


def checked(db, hearth, bound, params):
    authority = authorize(
        db, bound, int(hearth.clock()), thread_id=params["threadId"], turn_id=params["turnId"]
    )
    try:
        request = TOOLS[params["tool"]][0].model_validate(params["arguments"])
    except ValidationError:
        raise Refused("communications_arguments_invalid") from None
    if isinstance(request, Announcement) and not request.text.strip():
        raise Refused("communications_arguments_invalid")
    capability = "read" if isinstance(request, HistoryRequest) else "post"
    check_scope(db, authority, capability, request.destination.model_dump(), int(hearth.clock()))
    return request


def call(hearth, bound, params):
    with hearth.database.transaction(write=True) as db:
        request = checked(db, hearth, bound, params)
        if db.execute(
            "SELECT 1 FROM management_calls WHERE run_id=? AND call_id=?",
            (bound.run_id, params["callId"]),
        ).fetchone():
            raise Refused("management_call_conflict")
        payload = digest(params)
        previous = db.execute(
            "SELECT * FROM communications_calls WHERE run_id=? AND call_id=?",
            (bound.run_id, params["callId"]),
        ).fetchone()
        if previous and previous["payload_digest"] != payload:
            raise Refused("management_call_conflict")
        operation = db.execute(
            "SELECT * FROM communications_requests WHERE run_id=? AND operation_id=?",
            (bound.run_id, request.operation_id),
        ).fetchone()
        request_digest = digest([params["tool"], request.model_dump(), asdict(bound)])
        if operation and operation["request_digest"] != request_digest:
            raise Refused("communications_operation_conflict")
        if previous is None:
            count = db.execute(
                "SELECT COUNT(*) FROM communications_calls WHERE run_id=?", (bound.run_id,)
            ).fetchone()[0]
            if count >= MAX_CALLS:
                raise Refused("communications_call_limit")
            db.execute(
                "INSERT INTO communications_calls VALUES (?,?,?)",
                (bound.run_id, params["callId"], payload),
            )
        if operation is None:
            if isinstance(request, Announcement):
                count = db.execute(
                    "SELECT COUNT(*) FROM communications_requests WHERE run_id=? AND "
                    "tool='hearth_publish_announcement'",
                    (bound.run_id,),
                ).fetchone()[0]
                if count >= MAX_PUBLICATIONS:
                    raise Refused("communications_publication_limit")
                identity = Delivery(hearth).announce_in_transaction(
                    db,
                    bound,
                    request.operation_id,
                    request.destination,
                    request.text.replace(bound.owner_token, "[redacted]"),
                    thread_id=params["threadId"],
                    turn_id=params["turnId"],
                )
                result = json.dumps({"delivery_id": identity})
            else:
                result = None
            db.execute(
                "INSERT INTO communications_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    bound.run_id,
                    request.operation_id,
                    request_digest,
                    params["tool"],
                    json.dumps(params),
                    json.dumps(asdict(bound)),
                    "queued",
                    result,
                    int(hearth.clock()),
                    None,
                    digest(json.loads(result)) if result else None,
                ),
            )
            _audit(
                db,
                "communications.tool_prepared",
                bound.run_id,
                int(hearth.clock()),
                {"operation_id": request.operation_id, "tool": params["tool"]},
            )
            operation = db.execute(
                "SELECT * FROM communications_requests WHERE run_id=? AND operation_id=?",
                (bound.run_id, request.operation_id),
            ).fetchone()
        if isinstance(request, Announcement):
            identity = json.loads(operation["result"])["delivery_id"]
            row = db.execute(
                "SELECT state FROM delivery_operations WHERE id=?", (identity,)
            ).fetchone()
            attempt = db.execute(
                "SELECT receipt FROM delivery_attempts WHERE operation_id=? ORDER BY "
                "dispatched_at DESC LIMIT 1",
                (identity,),
            ).fetchone()
            return response(
                {
                    "operation_id": request.operation_id,
                    "delivery_id": identity,
                    "state": row[0],
                    "receipt": json.loads(attempt[0]) if attempt and attempt[0] else None,
                }
            )
        if operation["result"]:
            return json.loads(operation["result"])
        return response({"operation_id": request.operation_id, "state": operation["state"]})


def validate(db):
    """Preserve request identity and exact bounded result evidence in held backups."""
    from hearth.management.bridge import BoundRun

    try:
        for row in db.execute("SELECT * FROM communications_requests"):
            params = json.loads(row["params"])
            bound = BoundRun(**json.loads(row["binding"]))
            model = TOOLS[row["tool"]][0]
            request = model.model_validate(params["arguments"])
            run = db.execute("SELECT * FROM runs WHERE id=?", (row["run_id"],)).fetchone()
            if (
                bound.run_id != row["run_id"]
                or bound.owner_token != run["owner_token"]
                or bound.input_digest != run["input_digest"]
                or params["tool"] != row["tool"]
                or request.operation_id != row["operation_id"]
                or digest([row["tool"], request.model_dump(), asdict(bound)])
                != row["request_digest"]
            ):
                raise ValueError
            call = db.execute(
                "SELECT payload_digest FROM communications_calls WHERE run_id=? AND call_id=?",
                (bound.run_id, params["callId"]),
            ).fetchone()
            if call is None or call[0] != digest(params):
                raise ValueError
            result = json.loads(row["result"]) if row["result"] else None
            if (digest(result) if result is not None else None) != row["result_sha256"]:
                raise ValueError
            if isinstance(request, Announcement):
                if result is None:
                    raise ValueError
                delivery = db.execute(
                    "SELECT source_key FROM delivery_operations WHERE id=?",
                    (result["delivery_id"],),
                ).fetchone()
                if (
                    delivery is None
                    or delivery[0] != f"announcement:{bound.run_id}:{request.operation_id}"
                ):
                    raise ValueError
            elif (row["state"] == "complete") != (
                result is not None and row["completed_at"] is not None
            ):
                raise ValueError
    except ValueError, TypeError, KeyError:
        raise Refused("communications_tools_corrupt") from None
