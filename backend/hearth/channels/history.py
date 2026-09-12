"""Worker-owned read permits and completion; no remote I/O under a SQLite writer."""

import json
from dataclasses import asdict

from hearth.channels.chat.config import read
from hearth.channels.polling import REQUEST_SECONDS, RESPONSE_BYTES
from hearth.channels.tools import checked
from hearth.management.authority import digest
from hearth.management.bridge import BoundRun, response
from hearth.residents.models import Refused
from hearth.work.service import _audit


def result_of(page, request, messages):
    return response(
        {
            "operation_id": request.operation_id,
            "state": "complete",
            "messages": messages,
            "before": messages[-1]["message_id"]
            if messages
            else (page.messages[0].message_id if page.messages else page.before),
            "omitted_count": page.omitted_count + (1 if page.messages and not messages else 0),
            "omission_reason": "message_too_large"
            if (page.omitted_count or (page.messages and not messages))
            else None,
            "truncated": page.truncated or len(messages) < len(page.messages),
            "complete": page.complete and len(messages) == len(page.messages),
            "content_access": page.content_access,
            "permission": page.permission,
            "trust": "external_untrusted",
        }
    )


def process(worker, adapters):
    hearth = worker.hearth
    with hearth.database.transaction() as db:
        pending = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM communications_requests WHERE "
                "tool='hearth_read_channel_history' AND state IN ('queued','reading') "
                "AND (run_id,operation_id)>(?,?) ORDER BY run_id,operation_id LIMIT 2",
                worker._history_after,
            )
        ]
    worker._history_after = (
        (pending[-1]["run_id"], pending[-1]["operation_id"]) if pending else ("", "")
    )
    for row in pending:
        bound = BoundRun(**json.loads(row["binding"]))
        params = json.loads(row["params"])
        result = None
        adapter = None
        connection_id = params["arguments"]["destination"]["connection_id"]
        try:
            with hearth.database.transaction(write=True) as db:
                request = checked(db, hearth, bound, params)
                destination = request.destination.model_dump()
                connection = read(db, "connection", connection_id)
                assert connection is not None
                if (
                    connection_id not in adapters
                    or not worker._eligible("connection", connection_id)
                    or worker._destination_waiting(connection_id, destination)
                    or not worker._eligible("destination", digest(destination))
                ):
                    continue
                # A previous owner may have performed this read; do not silently fetch a
                # different page under the same caller operation after a crash.
                if row["state"] == "reading":
                    raise Refused("communications_read_interrupted")
                db.execute(
                    "UPDATE communications_requests SET state='reading' WHERE run_id=? "
                    "AND operation_id=?",
                    (bound.run_id, request.operation_id),
                )
                _audit(
                    db,
                    "communications.read_prepared",
                    bound.run_id,
                    int(hearth.clock()),
                    {"operation_id": request.operation_id},
                )
            worker._check_secret(connection_id, connection)
            adapter = adapters[connection_id][3]
            page = adapter.history(
                request.destination.guild_id,
                request.destination.channel_id,
                before=request.before,
                limit=request.limit,
                max_bytes=RESPONSE_BYTES,
                timeout=REQUEST_SECONDS,
            )
            worker._check_secret(connection_id, connection)
            if page.content_access != "available":
                raise Refused("communications_content_unavailable")
            messages = []
            for message in page.messages:
                if (message.bot_id, message.guild_id, message.channel_id) != (
                    connection["bot_id"],
                    request.destination.guild_id,
                    request.destination.channel_id,
                ):
                    raise Refused("communications_source_denied")
                value = asdict(message)
                for secret in (*worker.secrets.known_values, bound.owner_token):
                    if secret:
                        value["text"] = value["text"].replace(secret, "[redacted]")
                candidate = messages + [value]
                # Bound both native serializers, including the nested result envelope.
                # Whole-message truncation preserves the cursor/provenance relationship.
                if len(json.dumps(result_of(page, request, candidate)).encode()) > 32768:
                    break
                messages.append(value)
            if len(messages) > request.limit:
                raise Refused("communications_history_too_large")
            result = result_of(page, request, messages)
            with hearth.database.transaction() as db:
                checked(db, hearth, bound, params)
                if read(db, "connection", connection_id) != connection:
                    raise Refused("communications_connection_changed")
        except Refused as error:
            result = response({"error": error.code}, success=False)
        except Exception as error:
            worker._failure(
                "destination",
                digest(params["arguments"]["destination"]),
                connection_id,
                error,
            )
            result = response({"error": "communications_history_unavailable"}, success=False)
        finally:
            if adapter is not None:
                worker._flush_limits(connection_id, adapter)
        if result is not None:
            with hearth.database.transaction(write=True) as db:
                try:
                    checked(db, hearth, bound, params)
                    if result["success"] and read(db, "connection", connection_id) != connection:
                        raise Refused("communications_connection_changed")
                except Refused as error:
                    result = response({"error": error.code}, success=False)
                db.execute(
                    "UPDATE communications_requests SET "
                    "state='complete',result=?,completed_at=?,result_sha256=? "
                    "WHERE run_id=? AND operation_id=?",
                    (
                        json.dumps(result),
                        int(hearth.clock()),
                        digest(result),
                        bound.run_id,
                        row["operation_id"],
                    ),
                )
                _audit(
                    db,
                    "communications.read_completed",
                    bound.run_id,
                    int(hearth.clock()),
                    {"operation_id": row["operation_id"], "success": result["success"]},
                )
