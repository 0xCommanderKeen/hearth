"""Bounded operator projections. Reads never construct clients or resolve credentials."""

import json

from hearth.channels.chat.config import read
from hearth.residents.models import Refused

RETENTION = (
    "Closed transcripts are pruned to 20 turns, 30 days and 1 MiB per route when new turns arrive. "
    "Pinned task/run inputs, artifacts and delivery evidence have separate lifetimes."
)


def page(limit, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise Refused("communications_page_invalid")


class Inspection:
    def __init__(self, worker, *, protected_values=()):
        self.worker, self.hearth = worker, worker.hearth
        self.protected_values = protected_values

    def redact(self, db, value):
        # Do not read secret files on render. Redact values already known to this
        # process and internal run credentials even when quoted in retained text.
        protected = set(self.protected_values)
        if self.worker.secrets:
            protected.update(self.worker.secrets.known_values)
        # Only materialize tokens actually quoted in this bounded projection;
        # embedded prefixes/suffixes must not defeat the redaction.
        serialized = json.dumps(value, ensure_ascii=False)
        protected.update(
            r[0]
            for r in db.execute(
                "SELECT owner_token FROM runs WHERE instr(?,owner_token)>0", (serialized,)
            )
        )

        def clean(item):
            if isinstance(item, str):
                for secret in sorted(protected, key=len, reverse=True):
                    if secret:
                        item = item.replace(secret, "[redacted]")
                return item
            if isinstance(item, dict):
                return {k: clean(v) for k, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            return item

        return clean(value)

    def configuration(self, *, resident_id=None, after="", limit=100):
        page(limit)
        with self.hearth.database.transaction() as db:
            items = []
            rows = db.execute(
                "SELECT kind,id FROM communications_config WHERE kind||':'||id>? "
                "ORDER BY kind,id LIMIT ?",
                (after, limit + 1),
            ).fetchall()
            for row in rows[:limit]:
                value = read(db, row["kind"], row["id"])
                assert value is not None
                item = {"kind": row["kind"], "id": row["id"], "value": value}
                if len(json.dumps(item, ensure_ascii=True).encode()) > 32768:
                    item = {
                        "kind": row["kind"],
                        "id": row["id"],
                        "value": None,
                        "revision": value["revision"],
                        "omitted": "configuration_too_large",
                    }
                items.append(item)
            bindings = [
                dict(r)
                for r in db.execute(
                    "SELECT connection_id,revision,activated_at FROM delivery_bindings "
                    "ORDER BY connection_id LIMIT 16"
                )
            ]
            schedules = [
                dict(r)
                for r in db.execute(
                    "SELECT kind,id,eligible_at,error FROM communications_schedule "
                    "WHERE kind IN ('connection','route') ORDER BY kind,id LIMIT 528"
                )
            ]
            progress = [
                dict(r)
                for r in db.execute(
                    "SELECT connection_id,guild_id,channel_id,cursor,through_id,updated_at "
                    "FROM communications_cursors ORDER BY connection_id,guild_id,channel_id "
                    "LIMIT 512"
                )
            ]
            result = self.redact(
                db,
                {
                    "read_only": self.hearth.database.restored(),
                    "configuration": items,
                    "next_after": f"{rows[limit - 1]['kind']}:{rows[limit - 1]['id']}"
                    if len(rows) > limit
                    else None,
                    "bindings": bindings,
                    "schedule": schedules,
                    "poll_progress": progress,
                    "health": self.worker.health(),
                    "retention": RETENTION,
                },
            )
            while len(json.dumps(result, ensure_ascii=True).encode()) > 524288:
                if len(result["configuration"]) <= 1:
                    raise Refused("communications_detail_too_large")
                result["configuration"].pop()
                tail = result["configuration"][-1]
                result["next_after"] = f"{tail['kind']}:{tail['id']}"
            return result

    def conversations(self, *, resident_id=None, after="", limit=30):
        page(limit)
        with self.hearth.database.transaction() as db:
            rows = db.execute(
                "SELECT c.*,json_extract(h.content,'$.resident_id') AS resident_id,"
                "json_extract(h.content,'$.label') AS channel_label,"
                "(SELECT COUNT(*) FROM chat_turns t WHERE t.conversation_id=c.id) AS turns,"
                "(EXISTS(SELECT 1 FROM chat_turns t WHERE t.conversation_id=c.id AND "
                "t.state!='closed') OR EXISTS(SELECT 1 FROM chat_turns t "
                "JOIN delivery_operations o ON json_extract(o.intent,'$.source_id')=t.id "
                "WHERE t.conversation_id=c.id AND o.state='unknown' AND COALESCE("
                "(SELECT action FROM delivery_resolutions r WHERE r.operation_id=o.id "
                "ORDER BY revision DESC LIMIT 1),'')!='reissue')) AS busy,"
                "(SELECT MAX(created_at) FROM chat_turns t WHERE t.conversation_id=c.id) "
                "AS last_at "
                "FROM chat_conversations c JOIN communications_config f ON f.kind='route' "
                "AND f.id=c.route_id "
                "JOIN communications_revisions h ON h.kind=f.kind AND h.id=f.id AND "
                "h.revision=f.revision "
                "WHERE c.id>? AND (? IS NULL OR json_extract(h.content,'$.resident_id')=?) "
                "ORDER BY c.id LIMIT ?",
                (after, resident_id, resident_id, limit + 1),
            ).fetchall()
            return self.redact(
                db,
                {
                    "items": [dict(r) for r in rows[:limit]],
                    "next_after": rows[limit - 1]["id"] if len(rows) > limit else None,
                },
            )

    def conversation(self, identity, *, before=None, limit=20):
        page(limit)
        if limit > 50:
            raise Refused("communications_page_invalid")
        with self.hearth.database.transaction() as db:
            conversation = db.execute(
                "SELECT * FROM chat_conversations WHERE id=?", (identity,)
            ).fetchone()
            if conversation is None:
                raise Refused("conversation_not_found")
            boundary = None
            if before:
                boundary = db.execute(
                    "SELECT rowid FROM chat_turns WHERE conversation_id=? AND id=?",
                    (identity, before),
                ).fetchone()
                if boundary is None:
                    raise Refused("communications_cursor_invalid")
            rows = db.execute(
                "SELECT "
                "t.id,t.message_id,t.sender_id,t.created_at,t.text,t.task_id,t.run_id,t.state,t.reason,"
                "t.reply_intent,t.operation_id,r.status AS "
                "run_status,r.usage_known,r.actual_cost,r.finished_at,"
                "d.state AS delivery_state FROM chat_turns t LEFT JOIN runs r ON r.id=t.run_id "
                "LEFT JOIN delivery_operations d ON d.id=t.operation_id WHERE t.conversation_id=? "
                "AND (? IS NULL OR t.rowid<?) ORDER BY t.rowid DESC LIMIT ?",
                (
                    identity,
                    boundary[0] if boundary else None,
                    boundary[0] if boundary else None,
                    limit + 1,
                ),
            ).fetchall()
            turns, used = [], 0
            for row in rows[:limit]:
                item = dict(row)
                reply = item.pop("reply_intent")
                item["reply"] = json.loads(reply)["text"] if reply else None
                item["direction"] = "inbound"
                item = self.redact(db, item)
                size = len(json.dumps(item, ensure_ascii=True).encode())
                if used + size > 32000:
                    if turns:
                        break
                    item["text"] = item["reply"] = None
                    item["omission"] = "page_byte_limit"
                    size = len(json.dumps(item, ensure_ascii=True).encode())
                turns.append(item)
                used += size
            route = read(db, "route", conversation["route_id"])
            if route:
                route = {
                    k: route[k]
                    for k in (
                        "connection_id",
                        "resident_id",
                        "address",
                        "state",
                        "label",
                        "revision",
                    )
                }
            # Drops are body-free route counts: sender identities are deliberately
            # not persisted for rejected turns, so never invent per-person counts.
            drops = [
                dict(r)
                for r in db.execute(
                    "SELECT json_extract(receipt,'$.reason') AS reason,COUNT(*) AS count "
                    "FROM chat_inbound "
                    "WHERE connection_id=? AND channel_id=? AND turn_id IS NULL "
                    "GROUP BY json_extract(receipt,'$.reason') LIMIT 50",
                    (conversation["connection_id"], conversation["channel_id"]),
                )
            ]
            result = self.redact(
                db,
                {
                    "conversation": dict(conversation),
                    "route": route,
                    "turns": turns,
                    "next_before": turns[-1]["id"] if len(rows) > len(turns) else None,
                    "dropped_in_channel": drops,
                    "retention": RETENTION,
                    "read_only": self.hearth.database.restored(),
                },
            )
            # Bound the complete escaped envelope, including metadata and cursor.
            while len(json.dumps(result, ensure_ascii=True).encode()) > 32768:
                if len(result["turns"]) > 1:
                    result["turns"].pop()
                    result["next_before"] = result["turns"][-1]["id"]
                elif result["turns"] and (
                    result["turns"][0]["text"] or result["turns"][0]["reply"]
                ):
                    result["turns"][0].update(text=None, reply=None, omission="page_byte_limit")
                else:
                    raise Refused("communications_detail_too_large")
            return result

    def deliveries(self, *, kind=None, resident_id=None, offset=0, limit=30):
        page(limit, offset)
        if kind is not None and kind not in {"reply", "announcement", "notification"}:
            raise Refused("communications_kind_invalid")
        with self.hearth.database.transaction() as db:
            rows = db.execute(
                "SELECT "
                "id,connection_id,state,revision,created_at,updated_at,eligible_at,parent_id,"
                "json_extract(intent,'$.kind') AS kind,json_extract(intent,'$.source_id') "
                "AS source_id,"
                "json_extract(intent,'$.resident_id') AS "
                "resident_id,json_extract(intent,'$.task_id') AS task_id,"
                "COALESCE(json_extract(intent,'$.run_id'),(SELECT r.id FROM notifications n "
                "JOIN runs r ON r.id=n.resource_id WHERE n.id=json_extract(intent,'$.source_id'))) "
                "AS run_id FROM delivery_operations "
                "WHERE (? IS NULL OR json_extract(intent,'$.kind')=?) "
                "AND (? IS NULL OR json_extract(intent,'$.resident_id')=? OR EXISTS("
                "SELECT 1 FROM notifications n JOIN runs r ON r.id=n.resource_id "
                "WHERE n.id=json_extract(intent,'$.source_id') AND r.resident_id=?)) "
                "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                (kind, kind, resident_id, resident_id, resident_id, limit + 1, offset),
            ).fetchall()
            return {
                "items": [dict(r) for r in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None,
            }

    def delivery(self, identity):
        with self.hearth.database.transaction() as db:
            result = self.worker.delivery.detail_in_transaction(db, identity)
            row = db.execute(
                "SELECT intent,created_at,updated_at,eligible_at,parent_id "
                "FROM delivery_operations WHERE id=?",
                (identity,),
            ).fetchone()
            intent = json.loads(row["intent"])
            result.update(
                {k: row[k] for k in ("created_at", "updated_at", "eligible_at", "parent_id")}
            )
            result.update(
                {
                    "text": intent["text"],
                    "resident_id": intent.get("resident_id"),
                    "read_only": self.hearth.database.restored(),
                    "direction": "outbound",
                }
            )
            notice = (
                db.execute(
                    "SELECT kind,resource_id,read_at FROM notifications WHERE id=?",
                    (intent["source_id"],),
                ).fetchone()
                if intent["kind"] == "notification"
                else None
            )
            result["notification"] = dict(notice) if notice else None
            source_run = notice["resource_id"] if notice else intent.get("run_id")
            run = db.execute(
                "SELECT id,task_id,status,usage_known,actual_cost,finished_at,"
                "cancellation_requested "
                "FROM runs WHERE id=?",
                (source_run,),
            ).fetchone()
            if run:
                result["run_id"], result["task_id"] = run["id"], run["task_id"]
            result["run"] = dict(run) if run else None
            occurrence = db.execute(
                "SELECT routine_id FROM occurrences WHERE task_id=?", (result.get("task_id"),)
            ).fetchone()
            result["routine_id"] = occurrence[0] if occurrence else None
            result["actions"] = (
                []
                if result["read_only"]
                else (
                    ["cancel"]
                    if result["state"] == "queued"
                    else ["sent", "not_sent", "abandon", "reissue"]
                    if result["state"] == "unknown"
                    else []
                )
            )
            from hearth.channels.delivery.authority import current
            from hearth.channels.delivery.model import Intent

            try:
                current(db, Intent.model_validate_json(row["intent"]), int(self.hearth.clock()))
                result["current_authority"] = {"allowed": True, "reason": None}
            except Refused as error:
                result["current_authority"] = {"allowed": False, "reason": error.code}
                if "reissue" in result["actions"]:
                    result["actions"].remove("reissue")
            reason = db.execute(
                "SELECT kind,at,json_extract(detail,'$.reason') AS reason FROM audit "
                "WHERE resource_id=? AND kind LIKE 'delivery.%' "
                "AND json_type(detail,'$.reason')='text' "
                "ORDER BY sequence DESC LIMIT 1",
                (identity,),
            ).fetchone()
            result["latest_reason"] = dict(reason) if reason else None
            # Evidence is stored as JSON text; expose its bounded typed fields only.
            for resolution in result["resolutions"]:
                evidence = json.loads(resolution["evidence"]) if resolution["evidence"] else None
                resolution["evidence"] = evidence
            return self.redact(db, result)

    def usage(self, *, limit=30, offset=0):
        from hearth.execution.usage import by_origin

        with self.hearth.database.transaction() as db:
            result = by_origin(db, limit=limit, offset=offset)
            for item in result["origins"]:
                item.pop("instruction", None)
                turn = db.execute(
                    "SELECT conversation_id FROM chat_turns WHERE task_id=?",
                    (item["root_task_id"],),
                ).fetchone()
                occurrence = db.execute(
                    "SELECT routine_id FROM occurrences WHERE task_id=?", (item["root_task_id"],)
                ).fetchone()
                command = db.execute(
                    "SELECT 1 FROM commands WHERE task_id=?", (item["root_task_id"],)
                ).fetchone()
                letter = db.execute(
                    "SELECT 1 FROM letters WHERE task_id=?", (item["root_task_id"],)
                ).fetchone()
                item["origin"] = (
                    "conversation"
                    if turn
                    else "routine"
                    if occurrence
                    else "letter"
                    if letter
                    else "operator"
                    if command
                    else "other"
                )
            return result
