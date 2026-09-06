"""Private transport calls authenticate, mutate and record receipts in one writer."""

import hmac
import json
from dataclasses import dataclass, field

from hearth.management.authority import digest, read_grant
from hearth.residents.models import Refused, bounded_text, identifier
from hearth.work.service import Hearth, _audit


@dataclass(frozen=True)
class BoundRun:
    run_id: str
    owner_token: str = field(repr=False)
    epoch: str
    input_digest: str


def authorize(db, bound: BoundRun, now: int, *, thread_id=None, turn_id=None) -> dict:
    epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()
    run = db.execute("SELECT * FROM runs WHERE id=?", (bound.run_id,)).fetchone()
    if run is None or not hmac.compare_digest(run["owner_token"], bound.owner_token):
        raise Refused("management_run_ownership_lost")
    if epoch is None or epoch[0] != bound.epoch:
        raise Refused("management_epoch_changed")
    if run["input_digest"] != bound.input_digest:
        raise Refused("management_input_changed")
    if (
        run["status"] not in {"starting", "running"}
        or run["finished_at"] is not None
        or run["cancellation_requested"]
        or not run["launch_attempted"]
    ):
        raise Refused("management_run_inactive")
    resident = db.execute(
        "SELECT revision FROM residents WHERE id=?", (run["resident_id"],)
    ).fetchone()
    if resident is None or resident[0] != run["resident_revision"]:
        raise Refused("management_declaration_changed")
    pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)).fetchone()
    if pin is None or pin["resident_id"] != run["resident_id"]:
        raise Refused("management_not_granted_at_admission")
    if now >= pin["expires_at"]:
        raise Refused("management_access_expired")
    grant = read_grant(db, run["resident_id"])
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    if (
        not grant["enabled"]
        or grant["revision"] != pin["grant_revision"]
        or digest(policy) != pin["grant_sha256"]
    ):
        raise Refused("management_grant_changed_or_revoked")
    if thread_id is not None and pin["thread_id"] != thread_id:
        raise Refused("management_thread_mismatch")
    if turn_id is not None and pin["turn_id"] != turn_id:
        raise Refused("management_turn_mismatch")
    return {"actor": run["resident_id"], "run_id": bound.run_id, "grant": grant}


def authorize_managed_resident(db, authority: dict, resident_id: str, action: str) -> dict:
    identifier(resident_id)
    if action not in authority["grant"]["capabilities"]:
        raise Refused("management_capability_not_permitted")
    profile = db.execute(
        "SELECT * FROM resident_profiles WHERE resident_id=?", (resident_id,)
    ).fetchone()
    if profile is None or profile["manager"] != authority["actor"]:
        raise Refused("management_resident_out_of_scope")
    if profile["execution_profile"] not in authority["grant"]["profiles"]:
        raise Refused("management_profile_not_permitted")
    from hearth.inputs.selection import read_selection

    if any(
        item["input_set_id"] not in authority["grant"]["input_set_ids"]
        for item in read_selection(db, resident_id)["input_sets"]
    ):
        raise Refused("management_input_not_permitted")
    return dict(profile)


def response(value: dict, *, success: bool = True) -> dict:
    return {
        "success": success,
        "contentItems": [{"type": "inputText", "text": json.dumps(value, sort_keys=True)}],
    }


class Bridge:
    def __init__(self, hearth: Hearth, bound: BoundRun):
        self.hearth, self.bound = hearth, bound

    def bind_thread(self, thread_id: str) -> None:
        bounded_text(thread_id, 256, "management_thread_invalid")
        with self.hearth.database.transaction(write=True) as db:
            authorize(db, self.bound, int(self.hearth.clock()))
            pin = db.execute(
                "SELECT thread_id FROM run_management WHERE run_id=?", (self.bound.run_id,)
            ).fetchone()
            if pin[0] not in {None, thread_id}:
                raise Refused("management_thread_mismatch")
            db.execute(
                "UPDATE run_management SET thread_id=? WHERE run_id=?",
                (thread_id, self.bound.run_id),
            )

    def bind_turn(self, thread_id: str, turn_id: str) -> None:
        bounded_text(turn_id, 256, "management_turn_invalid")
        with self.hearth.database.transaction(write=True) as db:
            authorize(db, self.bound, int(self.hearth.clock()), thread_id=thread_id)
            pin = db.execute(
                "SELECT turn_id FROM run_management WHERE run_id=?", (self.bound.run_id,)
            ).fetchone()
            if pin[0] not in {None, turn_id}:
                raise Refused("management_turn_mismatch")
            db.execute(
                "UPDATE run_management SET turn_id=? WHERE run_id=?", (turn_id, self.bound.run_id)
            )

    def call(self, params: dict) -> dict:
        try:
            if not isinstance(params, dict) or set(params) - {
                "threadId",
                "turnId",
                "callId",
                "namespace",
                "tool",
                "arguments",
            }:
                raise Refused("management_call_invalid")
            if params.get("namespace") is not None and params.get("namespace") != "functions":
                raise Refused("management_namespace_invalid")
            for key in ("threadId", "turnId", "callId", "tool"):
                value = params.get(key)
                if not isinstance(value, str):
                    raise Refused("management_call_invalid")
                bounded_text(value, 256, "management_call_invalid")
            if (
                not isinstance(params.get("arguments"), dict)
                or len(json.dumps(params).encode()) > 256 * 1024
            ):
                raise Refused("management_arguments_invalid")
            with self.hearth.database.transaction(write=True) as db:
                now = int(self.hearth.clock())
                authority = authorize(
                    db, self.bound, now, thread_id=params["threadId"], turn_id=params["turnId"]
                )
                payload = digest(params)
                previous = db.execute(
                    "SELECT * FROM management_calls WHERE run_id=? AND call_id=?",
                    (self.bound.run_id, params["callId"]),
                ).fetchone()
                if previous:
                    if previous["payload_digest"] != payload:
                        raise Refused("management_call_conflict")
                    return json.loads(previous["response"])
                count = db.execute(
                    "SELECT COUNT(*) FROM management_calls WHERE run_id=?", (self.bound.run_id,)
                ).fetchone()[0]
                if count >= authority["grant"]["max_calls"]:
                    raise Refused("management_call_limit")
                from hearth.management.tools import dispatch

                db.execute("SAVEPOINT management_operation")
                try:
                    result = response(
                        dispatch(db, self.hearth, authority, params["tool"], params["arguments"])
                    )
                except Refused as error:
                    db.execute("ROLLBACK TO management_operation")
                    result = response({"error": error.code}, success=False)
                finally:
                    db.execute("RELEASE management_operation")
                db.execute(
                    "INSERT INTO management_calls VALUES (?,?,?,?,?)",
                    (
                        self.bound.run_id,
                        params["callId"],
                        payload,
                        json.dumps(result, sort_keys=True),
                        now,
                    ),
                )
                _audit(
                    db,
                    "management.tool_completed",
                    self.bound.run_id,
                    now,
                    {
                        "actor": authority["actor"],
                        "call_id": params["callId"],
                        "tool": params["tool"],
                        "success": result["success"],
                    },
                )
                return result
        except Refused as error:
            return response({"error": error.code}, success=False)
