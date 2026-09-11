"""Terminal reply preparation and a typed transactional delivery handoff, without I/O."""

import hashlib
import json
from dataclasses import asdict

from hearth.channels.chat.config import Secrets, read
from hearth.channels.chat.service import check_turn, close
from hearth.channels.interface import ReplyIntent
from hearth.residents.models import Refused
from hearth.storage.artifacts import Artifact, Artifacts
from hearth.work.service import _audit


def text_reply(text: str, *, name: str = "", protected: tuple[str, ...] = ()) -> str | None:
    if text.strip() == "HEARTH_QUIET" or not text.strip():
        return None
    for value in sorted(set(protected), key=len, reverse=True):
        if value:
            text = text.replace(value, "[redacted]")
            name = name.replace(value, "[redacted]")
    text = text.strip()
    prefix = f"{name}: " if name else ""
    if prefix and not text.startswith(prefix):
        text = prefix + text
    if len(text) > 2000 or len(text.encode()) > 8192:
        text = text[:1999].encode()[:8189].decode("utf-8", errors="ignore") + "…"
    return text


def settled(db, task_id: str, run_id: str, status: str, now: int) -> None:
    turn = db.execute("SELECT * FROM chat_turns WHERE task_id=?", (task_id,)).fetchone()
    if turn is None:
        return
    if turn["run_id"] != run_id:
        raise Refused("communications_run_mismatch")
    if status != "succeeded":
        close(db, turn["id"], now, status)
    else:
        db.execute("UPDATE chat_turns SET state='reply_pending' WHERE id=?", (turn["id"],))
        _audit(db, "communications.reply_pending", turn["id"], now, {"run_id": run_id})


class Replies:
    def __init__(self, hearth, secrets: Secrets, *, protected_values: tuple[str, ...] = ()):
        self.hearth, self.secrets = hearth, secrets
        self.protected_values = protected_values

    def prepare(self, turn_id: str) -> ReplyIntent | None:
        """Read exact terminal evidence; a caller cannot supply model text or destination."""
        with self.hearth.database.transaction(write=True) as db:
            now = int(self.hearth.clock())
            turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
            if turn is None:
                raise Refused("communications_turn_missing")
            if turn["state"] == "closed":
                return None
            if turn["state"] not in {"reply_pending", "delivery"}:
                raise Refused("communications_reply_not_terminal")
            try:
                route, connection, grant = check_turn(db, turn, now, freshness=False)
            except Refused as error:
                if turn["state"] == "reply_pending" and turn["operation_id"] is None:
                    close(db, turn_id, now, error.code)
                    return None
                raise
            run = db.execute("SELECT * FROM runs WHERE id=?", (turn["run_id"],)).fetchone()
            if (
                run is None
                or run["status"] != "succeeded"
                or run["finished_at"] is None
                or run["cancellation_requested"]
            ):
                raise Refused("communications_reply_not_terminal")
            if turn["reply_intent"] is not None:
                return ReplyIntent(**json.loads(turn["reply_intent"]))
            if run["artifact_id"] is None:
                close(db, turn_id, now, "no_output")
                return None
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (run["artifact_id"],)).fetchone()
            output = Artifacts(self.hearth.database.path.parent / "artifacts").read(
                Artifact(**dict(row))
            )
            if output.strip() in {"", "HEARTH_QUIET"}:
                close(db, turn_id, now, "quiet" if output.strip() else "no_output")
                return None
            if self.secrets.resolve(connection["secret_ref"]) is None:
                raise Refused("communications_pending")
            protected = [*self.protected_values, *self.secrets.known_values, run["owner_token"]]
            for configured in db.execute(
                "SELECT id FROM communications_config WHERE kind='connection'"
            ):
                current = read(db, "connection", configured[0])
                assert current is not None
                try:
                    value = self.secrets.resolve(current["secret_ref"])
                except Refused:
                    # An unrelated broken installation must not block this route. Known
                    # values (including previously loaded credentials) are still redacted.
                    continue
                if value:
                    protected.append(value)
            declaration = db.execute(
                "SELECT name FROM declarations WHERE resident_id=? AND revision=?",
                (run["resident_id"], run["resident_revision"]),
            ).fetchone()
            text = text_reply(
                output,
                name=declaration[0] if route["mode"] == "shared" else "",
                protected=tuple(protected),
            )
            if text is None:
                close(
                    db, turn_id, now, "quiet" if output.strip() == "HEARTH_QUIET" else "no_output"
                )
                return None
            conversation = db.execute(
                "SELECT * FROM chat_conversations WHERE id=?", (turn["conversation_id"],)
            ).fetchone()
            intent = ReplyIntent(
                turn_id,
                run["id"],
                route["connection_id"],
                connection["bot_id"],
                conversation["route_id"],
                turn["route_revision"],
                turn["grant_revision"],
                conversation["guild_id"],
                conversation["channel_id"],
                turn["message_id"],
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            )
            from hearth.channels.chat.transcripts import prune

            try:
                prune(db, conversation["route_id"], now, incoming=len(text.encode()))
            except Refused as error:
                close(db, turn_id, now, error.code)
                return None
            db.execute(
                "UPDATE chat_turns SET reply_intent=?,reply_sha256=? WHERE id=?",
                (json.dumps(asdict(intent), sort_keys=True), intent.sha256, turn_id),
            )
            _audit(
                db,
                "communications.reply_prepared",
                turn_id,
                now,
                {"run_id": run["id"], "sha256": intent.sha256},
            )
            return intent

    def handoff_in_transaction(self, db, turn_id: str, enqueue) -> str:
        """#120 supplies enqueue(db, ReplyIntent)->operation_id, with no network I/O."""
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
        if (
            turn is None
            or turn["state"] not in {"reply_pending", "delivery"}
            or not turn["reply_intent"]
        ):
            raise Refused("communications_reply_unprepared")
        check_turn(db, turn, int(self.hearth.clock()), freshness=False)
        if turn["operation_id"] is not None:
            return turn["operation_id"]
        operation_id = enqueue(db, ReplyIntent(**json.loads(turn["reply_intent"])))
        db.execute(
            "UPDATE chat_turns SET state='delivery',operation_id=? WHERE id=?",
            (operation_id, turn_id),
        )
        _audit(
            db,
            "communications.reply_handed_off",
            turn_id,
            int(self.hearth.clock()),
            {"operation_id": operation_id},
        )
        return operation_id

    def delivery_in_transaction(self, db, turn_id: str, operation_id: str, outcome: str) -> None:
        """Trusted delivery owner only. Unknown never closes a conversation."""
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
        if turn is None or turn["operation_id"] != operation_id or turn["state"] != "delivery":
            raise Refused("communications_delivery_mismatch")
        if outcome == "unknown":
            db.execute("UPDATE chat_turns SET reason='delivery_unknown' WHERE id=?", (turn_id,))
            _audit(
                db,
                "communications.reply_unknown",
                turn_id,
                int(self.hearth.clock()),
                {"operation_id": operation_id},
            )
        elif outcome in {"sent", "refused", "failed", "abandoned"}:
            close(db, turn_id, int(self.hearth.clock()), outcome)
        else:
            raise Refused("communications_delivery_invalid")
