"""Cursor evidence remains meaningful in upgraded and held copies."""

from hearth.channels.chat.config import read
from hearth.channels.polling import cursor
from hearth.residents.models import Refused


def validate(db):
    try:
        for row in db.execute("SELECT * FROM communications_cursors"):
            connection = read(db, "connection", row["connection_id"])
            if connection is None or connection["bot_id"] is None:
                raise ValueError("missing poll connection")
            if cursor(row["baseline"]) > cursor(row["cursor"]):
                raise ValueError("cursor precedes baseline")
            if row["through_id"] is not None and cursor(row["through_id"]) < cursor(row["cursor"]):
                raise ValueError("invalid poll window")
            if row["scan_before"] is not None and (
                row["through_id"] is None
                or not cursor(row["cursor"])
                < cursor(row["scan_before"])
                <= cursor(row["through_id"]) + 1
            ):
                raise ValueError("invalid scan frontier")
        for row in db.execute("SELECT * FROM communications_schedule"):
            if row["kind"] in {"destination", "guild", "channel"}:
                if len(row["id"]) != 64 or any(c not in "0123456789abcdef" for c in row["id"]):
                    raise ValueError("invalid destination digest")
                continue
            if read(db, row["kind"], row["id"]) is None:
                raise ValueError("missing scheduled scope")
    except ValueError, TypeError:
        raise Refused("communications_cursors_corrupt") from None
