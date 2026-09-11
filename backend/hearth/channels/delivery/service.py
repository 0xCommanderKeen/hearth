"""Transactional intent/permit/result owner. Call network only after prepare returns."""

import fcntl
import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict

from hearth.channels.chat.authority import check_scope
from hearth.channels.chat.config import read
from hearth.channels.chat.model import Destination
from hearth.channels.delivery.authority import current, validate_intent
from hearth.channels.delivery.model import Intent, Permit, Receipt
from hearth.channels.interface import ReplyIntent
from hearth.management.authority import digest
from hearth.management.bridge import BoundRun, authorize
from hearth.residents.models import Refused, bounded_text
from hearth.work.service import _audit


def epoch(db):
    return db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]


class Delivery:
    def __init__(self, hearth, *, replies=None):
        self.hearth, self.replies = hearth, replies

    def now(self):
        return int(self.hearth.clock())

    def enqueue_in_transaction(self, db, intent: ReplyIntent) -> str:
        """Typed callback for Replies.handoff_in_transaction."""
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (intent.turn_id,)).fetchone()
        if turn is None or json.loads(turn["reply_intent"] or "{}") != asdict(intent):
            raise Refused("delivery_reply_mismatch")
        run = db.execute("SELECT * FROM runs WHERE id=?", (intent.run_id,)).fetchone()
        connection = read(db, "connection", intent.connection_id, turn["connection_revision"])
        assert connection is not None
        value = Intent(
            kind="reply",
            source_id=intent.turn_id,
            connection_revision=turn["connection_revision"],
            bot_id=intent.bot_id,
            transport=connection["transport"],
            destination=Destination(
                connection_id=intent.connection_id,
                guild_id=intent.guild_id,
                channel_id=intent.channel_id,
            ),
            text=intent.text,
            run_id=intent.run_id,
            task_id=run["task_id"],
            resident_id=run["resident_id"],
            grant_revision=intent.grant_revision,
            route_id=intent.route_id,
            route_revision=intent.route_revision,
            input_digest=run["input_digest"],
        )
        current(db, value, self.now())
        return self._enqueue(db, value, f"reply:{intent.turn_id}")

    def announce_in_transaction(
        self,
        db,
        bound: BoundRun,
        operation_key: str,
        destination: Destination,
        text: str,
        *,
        thread_id: str,
        turn_id: str,
    ) -> str:
        """#242 calls after redaction. Exact live authority is checked before replay."""
        bounded_text(operation_key, 128, "delivery_operation_key_invalid")
        authority = authorize(db, bound, self.now(), thread_id=thread_id, turn_id=turn_id)
        grant = check_scope(db, authority, "post", destination.model_dump(), self.now())
        run = db.execute("SELECT * FROM runs WHERE id=?", (bound.run_id,)).fetchone()
        connection = read(db, "connection", destination.connection_id)
        assert connection is not None
        value = Intent(
            kind="announcement",
            source_id=operation_key,
            connection_revision=connection["revision"],
            bot_id=connection["bot_id"],
            transport=connection["transport"],
            destination=destination,
            text=text,
            run_id=bound.run_id,
            task_id=run["task_id"],
            resident_id=authority["actor"],
            grant_revision=grant["revision"],
            input_digest=bound.input_digest,
            run_epoch=bound.epoch,
            thread_id=thread_id,
            turn_id=turn_id,
        )
        return self._enqueue(db, value, f"announcement:{bound.run_id}:{operation_key}")

    def _enqueue(self, db, intent: Intent, key: str, *, parent_id=None) -> str:
        validate_intent(db, intent)
        sha = digest(intent.model_dump())
        old = db.execute(
            "SELECT id,sha256 FROM delivery_operations WHERE source_key=?", (key,)
        ).fetchone()
        if old:
            if old["sha256"] != sha:
                raise Refused("delivery_operation_conflict")
            return old["id"]
        if (
            db.execute(
                "SELECT COUNT(*) FROM delivery_operations WHERE connection_id=? AND state IN "
                "('queued','dispatching','unknown')",
                (intent.destination.connection_id,),
            ).fetchone()[0]
            >= 1000
        ):
            raise Refused("delivery_pending_limit")
        identity = str(uuid.uuid4())
        now = self.now()
        db.execute(
            "INSERT INTO delivery_operations VALUES (?,?,?,?,?,?,1,?,?,?,?)",
            (
                identity,
                key,
                intent.model_dump_json(),
                sha,
                intent.destination.connection_id,
                "queued",
                now,
                now,
                now,
                parent_id,
            ),
        )
        _audit(db, "delivery.enqueued", identity, now, {"kind": intent.kind, "sha256": sha})
        return identity

    def activate(
        self,
        connection_id: str,
        *,
        expected_revision: int,
        operator_id: str,
        old_consumer_stopped: bool,
    ) -> int:
        """Explicit stopped-consumer handoff; restoring a copy grants no ownership."""
        bounded_text(operator_id, 128, "delivery_operator_invalid")
        if old_consumer_stopped is not True:
            raise Refused("delivery_consumer_stop_required")
        with self._local_lock(connection_id), self.hearth.database.transaction(write=True) as db:
            connection = read(db, "connection", connection_id)
            if connection is None or connection["state"] != "active":
                raise Refused("delivery_connection_inactive")
            old = db.execute(
                "SELECT * FROM delivery_bindings WHERE connection_id=?", (connection_id,)
            ).fetchone()
            if (old["revision"] if old else 0) != expected_revision:
                raise Refused("revision_conflict")
            revision = expected_revision + 1
            db.execute(
                "INSERT INTO delivery_bindings VALUES (?,?,?,?,?,NULL,?,?) ON "
                "CONFLICT(connection_id) DO UPDATE SET "
                "owner=NULL,epoch=excluded.epoch,store_path=excluded.store_path,revision=excluded.revision,operator_id=excluded.operator_id,activated_at=excluded.activated_at",
                (
                    connection_id,
                    connection["bot_id"],
                    epoch(db),
                    str(self.hearth.database.path.resolve()),
                    revision,
                    operator_id,
                    self.now(),
                ),
            )
            self._recover(db, connection_id)
            _audit(
                db,
                "delivery.activated",
                connection_id,
                self.now(),
                {"revision": revision, "operator_id": operator_id, "old_consumer_stopped": True},
            )
            return revision

    def _binding(self, db, connection_id, owner=None):
        row = db.execute(
            "SELECT * FROM delivery_bindings WHERE connection_id=?", (connection_id,)
        ).fetchone()
        if (
            row is None
            or row["epoch"] != epoch(db)
            or row["store_path"] != str(self.hearth.database.path.resolve())
            or (owner is not None and row["owner"] != owner)
        ):
            raise Refused("delivery_installation_not_owned")
        return row

    @contextmanager
    def _local_lock(self, connection_id):
        lock = self.hearth.database.path.parent / (
            "delivery-" + digest({"connection": connection_id}) + ".lock"
        )
        with lock.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("delivery_worker_owned") from None
            yield

    @contextmanager
    def worker(self, connection_id: str):
        """#241 holds this local lock across I/O; crash recovery never repeats permits."""
        with self._local_lock(connection_id):
            owner = str(uuid.uuid4())
            claimed = False
            try:
                with self.hearth.database.transaction(write=True) as db:
                    self._binding(db, connection_id)
                    self._recover(db, connection_id)
                    db.execute(
                        "UPDATE delivery_bindings SET owner=? WHERE connection_id=?",
                        (owner, connection_id),
                    )
                    _audit(
                        db, "delivery.worker_claimed", connection_id, self.now(), {"owner": owner}
                    )
                claimed = True
                yield owner
            finally:
                if claimed:
                    with self.hearth.database.transaction(write=True) as db:
                        changed = db.execute(
                            "UPDATE delivery_bindings SET owner=NULL WHERE "
                            "connection_id=? AND owner=?",
                            (connection_id, owner),
                        ).rowcount
                        if changed:
                            self._recover(db, connection_id)
                            _audit(
                                db,
                                "delivery.worker_released",
                                connection_id,
                                self.now(),
                                {"owner": owner},
                            )

    def _recover(self, db, connection_id):
        for row in db.execute(
            "SELECT o.* FROM delivery_operations o WHERE connection_id=? AND state='dispatching'",
            (connection_id,),
        ).fetchall():
            db.execute(
                "UPDATE delivery_attempts SET state='unknown',completed_at=? WHERE "
                "operation_id=? AND state='dispatching'",
                (self.now(), row["id"]),
            )
            self._state(db, row, "unknown", "recovered_permit")

    def _state(self, db, row, state, reason):
        db.execute(
            "UPDATE delivery_operations SET state=?,revision=revision+1,updated_at=? WHERE id=?",
            (state, self.now(), row["id"]),
        )
        _audit(db, "delivery." + state, row["id"], self.now(), {"reason": reason})
        intent = Intent.model_validate_json(row["intent"])
        if intent.kind == "reply":
            if state == "unknown":
                # Late contradictory evidence may arrive after a terminal receipt closed a turn.
                turn_row = db.execute(
                    "SELECT * FROM chat_turns WHERE id=?", (intent.source_id,)
                ).fetchone()
                if turn_row and turn_row["operation_id"] == row["id"]:
                    db.execute(
                        "UPDATE chat_turns SET reason='delivery_unknown' WHERE id=?",
                        (intent.source_id,),
                    )
            turn = db.execute(
                "SELECT state FROM chat_turns WHERE id=?", (intent.source_id,)
            ).fetchone()
            if (
                turn
                and turn[0] == "delivery"
                and db.execute(
                    "SELECT operation_id FROM chat_turns WHERE id=?", (intent.source_id,)
                ).fetchone()[0]
                == row["id"]
            ):
                if self.replies is None:
                    raise Refused("delivery_reply_owner_missing")
                outcome = {"confirmed": "sent", "cancelled": "refused"}.get(state, state)
                if outcome in {"sent", "refused", "failed", "abandoned", "unknown"}:
                    self.replies.delivery_in_transaction(db, intent.source_id, row["id"], outcome)

    def prepare(
        self, connection_id: str, owner: str, *, deferred_destinations: frozenset[str] = frozenset()
    ) -> Permit | None:
        """Commit one dispatch permit, return its immutable request. Never replay this permit."""
        with self.hearth.database.transaction(write=True) as db:
            binding = self._binding(db, connection_id, owner)
            rows = db.execute(
                "SELECT * FROM delivery_operations WHERE connection_id=? AND "
                "state='queued' AND eligible_at<=? ORDER BY created_at,id LIMIT 1000",
                (connection_id, self.now()),
            ).fetchall()
            for row in rows:
                intent = Intent.model_validate_json(row["intent"])
                if digest(intent.destination.model_dump()) in deferred_destinations:
                    continue
                try:
                    current(db, intent, self.now())
                except Refused as error:
                    self._state(db, row, "refused", error.code)
                    continue
                attempt = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO delivery_attempts VALUES (?,?,?,?,?,?,NULL,'dispatching',NULL)",
                    (attempt, row["id"], owner, epoch(db), binding["revision"], self.now()),
                )
                self._state(db, row, "dispatching", "permit_committed")
                _audit(
                    db,
                    "delivery.permit",
                    row["id"],
                    self.now(),
                    {
                        "attempt_id": attempt,
                        "owner": owner,
                        "epoch": epoch(db),
                        "sha256": row["sha256"],
                    },
                )
                return Permit(
                    operation_id=row["id"],
                    attempt_id=attempt,
                    owner=owner,
                    epoch=epoch(db),
                    intent_sha256=row["sha256"],
                    intent=intent,
                )
        return None

    def complete(self, permit: Permit, receipt: Receipt) -> None:
        """Persist exact-attempt evidence even after revocation; it cannot retract a send."""
        if receipt.attempt_id != permit.attempt_id or receipt.intent_sha256 != permit.intent_sha256:
            raise Refused("delivery_receipt_mismatch")
        if (receipt.outcome == "confirmed") != (receipt.external_id is not None):
            raise Refused("delivery_receipt_invalid")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM delivery_operations WHERE id=?", (permit.operation_id,)
            ).fetchone()
            attempt = db.execute(
                "SELECT * FROM delivery_attempts WHERE id=?", (permit.attempt_id,)
            ).fetchone()
            if (
                row is None
                or attempt is None
                or attempt["operation_id"] != permit.operation_id
                or attempt["owner"] != permit.owner
                or attempt["epoch"] != permit.epoch
                or row["sha256"] != permit.intent_sha256
                or digest(permit.intent.model_dump()) != row["sha256"]
            ):
                raise Refused("delivery_receipt_mismatch")
            self._binding(db, permit.intent.destination.connection_id)
            if epoch(db) != permit.epoch:
                raise Refused("delivery_receipt_epoch_changed")
            serialized = receipt.model_dump_json()
            if attempt["receipt"] == serialized:
                return
            conflict = (
                attempt["receipt"] is not None
                and json.loads(attempt["receipt"])["outcome"] != "unknown"
            )
            resolved = (
                db.execute(
                    "SELECT 1 FROM delivery_resolutions WHERE operation_id=?", (row["id"],)
                ).fetchone()
                is not None
            )
            if conflict or resolved:
                db.execute(
                    "INSERT INTO delivery_resolutions VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        row["id"],
                        row["revision"],
                        "late_receipt",
                        "transport",
                        self.now(),
                        "conflicting_or_resolved",
                        serialized,
                    ),
                )
                # A contradictory old acknowledgement invalidates later safe retry assumptions.
                for child in db.execute(
                    "SELECT * FROM delivery_operations WHERE parent_id=?", (row["id"],)
                ).fetchall():
                    if child["state"] == "queued":
                        # Restore the unresolved original as the
                        # conversation owner before cancelling its
                        # unsent child.
                        if permit.intent.kind == "reply":
                            db.execute(
                                "UPDATE chat_turns SET operation_id=? WHERE id=? AND "
                                "state='delivery'",
                                (row["id"], permit.intent.source_id),
                            )
                        self._state(db, child, "cancelled", "parent_late_receipt")
                    elif child["state"] == "dispatching":
                        db.execute(
                            "UPDATE delivery_attempts SET "
                            "state='unknown',completed_at=? WHERE operation_id=? AND "
                            "state='dispatching'",
                            (self.now(), child["id"]),
                        )
                        self._state(db, child, "unknown", "parent_late_receipt")
                db.execute(
                    "UPDATE delivery_attempts SET state='unknown',completed_at=? "
                    "WHERE operation_id=? AND state='dispatching'",
                    (self.now(), row["id"]),
                )
                self._state(db, row, "unknown", "late_receipt_requires_reconciliation")
                return
            db.execute(
                "UPDATE delivery_attempts SET state=?,completed_at=?,receipt=? WHERE id=?",
                (receipt.outcome, self.now(), serialized, attempt["id"]),
            )
            state = receipt.outcome
            if state == "safe_failure":
                count = db.execute(
                    "SELECT COUNT(*) FROM delivery_attempts WHERE operation_id=?", (row["id"],)
                ).fetchone()[0]
                state = "failed"
                if count < 5:
                    try:
                        current(db, permit.intent, self.now())
                    except Refused:
                        state = "refused"
                    else:
                        state = "queued"
                        db.execute(
                            "UPDATE delivery_operations SET eligible_at=? WHERE id=?",
                            (self.now() + max(2**count, receipt.retry_after), row["id"]),
                        )
            self._state(db, row, state, "transport_" + receipt.outcome)
            _audit(
                db,
                "delivery.receipt",
                row["id"],
                self.now(),
                {
                    "attempt_id": attempt["id"],
                    "outcome": receipt.outcome,
                    "external_id": receipt.external_id,
                },
            )

    def inspect(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise Refused("delivery_page_invalid")
        with self.hearth.database.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,connection_id,state,revision,created_at,updated_at,eligible_at,"
                    "parent_id FROM delivery_operations ORDER BY created_at DESC,id "
                    "LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def detail(self, operation_id: str) -> dict:
        """Bounded operator evidence; no authored text, credentials or worker tokens."""
        with self.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM delivery_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise Refused("delivery_operation_missing")
            intent = Intent.model_validate_json(row["intent"])
            attempts = []
            for attempt in db.execute(
                "SELECT * FROM delivery_attempts WHERE operation_id=? ORDER BY rowid LIMIT 5",
                (operation_id,),
            ):
                receipt = (
                    Receipt.model_validate_json(attempt["receipt"]) if attempt["receipt"] else None
                )
                attempts.append(
                    {
                        "id": attempt["id"],
                        "state": attempt["state"],
                        "epoch": attempt["epoch"],
                        "dispatched_at": attempt["dispatched_at"],
                        "completed_at": attempt["completed_at"],
                        "external_id": receipt.external_id if receipt else None,
                        "evidence": receipt.evidence if receipt else None,
                    }
                )
            resolutions = [
                dict(r)
                for r in db.execute(
                    "SELECT revision,action,operator_id,at,reason,evidence "
                    "FROM delivery_resolutions "
                    "WHERE operation_id=? ORDER BY revision DESC LIMIT 100",
                    (operation_id,),
                )
            ]
            return {
                "id": row["id"],
                "state": row["state"],
                "revision": row["revision"],
                "kind": intent.kind,
                "source_id": intent.source_id,
                "run_id": intent.run_id,
                "task_id": intent.task_id,
                "destination": intent.destination.model_dump(),
                "sha256": row["sha256"],
                "attempts": attempts,
                "resolutions": resolutions,
                "resolution_count": db.execute(
                    "SELECT COUNT(*) FROM delivery_resolutions WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()[0],
            }

    def resolve(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        operator_id: str,
        action: str,
        reason: str,
        evidence: Receipt | list[Receipt] | None = None,
        duplicate_risk_acknowledged: bool = False,
    ) -> str | None:
        """Authenticated operator composition seam; absence/elapsed time is not evidence."""
        bounded_text(operator_id, 128, "delivery_operator_invalid")
        bounded_text(reason, 256, "delivery_reason_invalid")
        if action not in {"sent", "not_sent", "abandon", "reissue", "cancel"}:
            raise Refused("delivery_resolution_invalid")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM delivery_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None or row["revision"] != expected_revision:
                raise Refused("revision_conflict")
            if action == "cancel":
                if row["state"] != "queued":
                    raise Refused("delivery_cannot_cancel_permitted")
            elif row["state"] != "unknown":
                raise Refused("delivery_not_unknown")
            evidence_rows = (
                evidence if isinstance(evidence, list) else [evidence] if evidence else []
            )
            if action in {"sent", "not_sent"}:
                if not evidence_rows:
                    raise Refused("delivery_evidence_required")
                uncertain = {
                    a[0]
                    for a in db.execute(
                        "SELECT id FROM delivery_attempts WHERE operation_id=? AND "
                        "state IN ('unknown','dispatching')",
                        (operation_id,),
                    )
                }
                for conflict in db.execute(
                    "SELECT evidence FROM delivery_resolutions WHERE operation_id=? "
                    "AND action='late_receipt'",
                    (operation_id,),
                ):
                    uncertain.add(Receipt.model_validate_json(conflict[0]).attempt_id)
                if (
                    not uncertain
                    or {e.attempt_id for e in evidence_rows} != uncertain
                    or len(evidence_rows) != len(uncertain)
                ):
                    raise Refused("delivery_evidence_invalid")
                for item in evidence_rows:
                    if (
                        item.intent_sha256 != row["sha256"]
                        or item.outcome == "unknown"
                        or (item.outcome == "confirmed") != (item.external_id is not None)
                    ):
                        raise Refused("delivery_evidence_invalid")
                if (action == "sent") != any(e.outcome == "confirmed" for e in evidence_rows):
                    raise Refused("delivery_evidence_invalid")
            elif evidence is not None:
                raise Refused("delivery_evidence_invalid")
            if action == "reissue" and duplicate_risk_acknowledged is not True:
                raise Refused("delivery_duplicate_risk_required")
            db.execute(
                "INSERT INTO delivery_resolutions VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    operation_id,
                    row["revision"],
                    action,
                    operator_id,
                    self.now(),
                    reason,
                    json.dumps([e.model_dump() for e in evidence_rows]) if evidence_rows else None,
                ),
            )
            _audit(
                db,
                "delivery.resolved",
                operation_id,
                self.now(),
                {
                    "action": action,
                    "operator_id": operator_id,
                    "reason": reason,
                    "expected_revision": expected_revision,
                    "duplicate_risk_acknowledged": duplicate_risk_acknowledged,
                },
            )
            if action == "reissue":
                intent = Intent.model_validate_json(row["intent"])
                current(db, intent, self.now())
                identity = self._enqueue(
                    db, intent, f"reissue:{operation_id}:{row['revision']}", parent_id=operation_id
                )
                # Original uncertainty and attempts stay intact. A reply keeps its one open turn.
                self._state(db, row, "unknown", "explicit_duplicate_risk_reissue")
                if intent.kind == "reply":
                    db.execute(
                        "UPDATE chat_turns SET operation_id=? WHERE id=?",
                        (identity, intent.source_id),
                    )
                return identity
            self._state(
                db,
                row,
                {
                    "sent": "confirmed",
                    "not_sent": "refused",
                    "abandon": "abandoned",
                    "cancel": "cancelled",
                }[action],
                reason,
            )
        return None
