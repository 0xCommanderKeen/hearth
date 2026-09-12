"""Origin-specific scope composed with the existing exact-run bridge authorization."""

from hearth.channels.chat.config import read
from hearth.channels.chat.service import check_turn, origin
from hearth.residents.models import Refused


def pin_grant(db, run_id: str, resident_id: str, now: int) -> None:
    run = db.execute("SELECT task_id FROM runs WHERE id=?", (run_id,)).fetchone()
    if not db.execute(
        "SELECT 1 FROM commands WHERE task_id=? UNION ALL "
        "SELECT 1 FROM occurrences WHERE task_id=?",
        (run[0], run[0]),
    ).fetchone():
        return
    if db.execute("SELECT 1 FROM letters WHERE task_id=?", (run[0],)).fetchone():
        return
    grant = read(db, "grant", resident_id)
    if grant is None or not (grant["read"] or grant["post"]):
        return
    db.execute(
        "INSERT INTO run_communications VALUES (?,?,?)", (run_id, resident_id, grant["revision"])
    )
    db.execute(
        "INSERT OR IGNORE INTO run_management VALUES (?,?,NULL,NULL,?,NULL,NULL,NULL,NULL)",
        (run_id, resident_id, now + 600),
    )


def granted_at_admission(db, run_id: str) -> bool:
    return (
        db.execute("SELECT 1 FROM run_communications WHERE run_id=?", (run_id,)).fetchone()
        is not None
    )


def check_scope(db, authority: dict, capability: str, destination: dict, now: int) -> dict:
    """Call after bridge.authorize, before receipt replay/prepare and again at completion.

    #242 owns network tool prepare/perform/complete. This function performs no I/O.
    Automatic terminal replies use check_turn, never a forged live-run authority.
    """
    if capability not in {"read", "reply", "post"}:
        raise Refused("communications_capability_invalid")
    from hearth.residents.lifecycle import check_ready

    check_ready(db, authority["actor"])
    if db.execute("SELECT 1 FROM pauses WHERE resident_id=?", (authority["actor"],)).fetchone():
        raise Refused("resident_paused")
    conversation = origin(db, authority["run_id"])
    if conversation:
        if capability == "post":
            raise Refused("communications_origin_denied")
        turn = db.execute(
            "SELECT * FROM chat_turns WHERE id=?", (conversation["turn_id"],)
        ).fetchone()
        route, _, grant = check_turn(db, turn, now, freshness=False)
        if destination != route["address"] | {"connection_id": route["connection_id"]}:
            raise Refused("communications_source_denied")
    else:
        if capability == "reply":
            raise Refused("communications_origin_denied")
        pin = db.execute(
            "SELECT * FROM run_communications WHERE run_id=?", (authority["run_id"],)
        ).fetchone()
        if pin is None:
            raise Refused("communications_not_granted_at_admission")
        grant = read(db, "grant", authority["actor"])
        if grant is None or grant["revision"] != pin["grant_revision"]:
            raise Refused("communications_authority_changed")
    if destination not in grant[capability]:
        raise Refused("communications_scope_denied")
    connection = read(db, "connection", destination["connection_id"])
    if connection is None or connection["state"] != "active":
        raise Refused("communications_pending")
    return grant
