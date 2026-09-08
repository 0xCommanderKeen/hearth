"""Letters: one resident's task addressed to another, with Hearth as the sole arbiter.

A letter is an ordinary task. What is new is the address — who sent it, from which run
and task, the root the chain rolls up to, how many hops in it is and when it goes stale
— and the two permissions that have to meet for it to exist at all: the sender's grant
capability `send_letters` and the receiver's declared `letters.accept` door. Neither
side can waive the other, and neither is written by instruction text.

Depth and lineage are read from the sender's own admitted run and task. Nothing a caller
says about a parent is trusted, so a forged or omitted parent cannot lengthen a chain,
revisit a resident or attribute cost to the wrong origin.
"""

from hearth.authority.household import _day_window, read_letter_policy
from hearth.residents.models import Refused, bounded_text, identifier
from hearth.work.service import ADMISSION_WAITS, _audit, _queue_task

MAX_TITLE = 200
MAX_DETAIL = 8_000
# What one letter may reserve, matching the scheduler's own occurrence reservation: a
# letter is an ordinary task and buys no more of the household's day than one does.
LETTER_RESERVATION = 10_000
# Refusals a delivery pass leaves for a later one. A letter that went stale between the
# read and the write is one of them: the sweep owns closing it, not this pass.
LETTER_WAITS = ADMISSION_WAITS | {"letter_expired"}
# A reply is an answer, not a second run report: the sender reads this, and the full
# artifact of the run that wrote it stays linked for the operator.
MAX_REPLY = 4_000
# What one call may carry back, so a busy inbox cannot outgrow the native response.
MAX_PAGE = 25
# How many answers one run opens with. A sender reads what its colleagues wrote since
# its last run, bounded like every other injected input: the rest wait for the next run
# or for the tool, which pages.
MAX_RUN_REPLIES = 5
# Hearth's own hand. An operator's letter has no resident and no run behind it.
OPERATOR = "operator"


def _chain(db, task_id: str) -> list[str]:
    """The residents a chain has already visited, oldest first, ending at this task's own.

    Read from the stored lineage, never from a caller's claim. A task that is not a
    letter is the start of a chain and names one resident: whoever is working it. The
    walk stops at a task it has already seen, so lineage edited in a copied file cannot
    hold the reader in a loop; the caller's own checks then refuse the row.
    """
    visited: list[str] = []
    seen: set[str] = set()
    current: str | None = task_id
    while current is not None and current not in seen:
        seen.add(current)
        task = db.execute("SELECT resident_id FROM tasks WHERE id=?", (current,)).fetchone()
        if task is None:
            raise Refused("task_not_found")
        visited.append(task["resident_id"])
        letter = db.execute(
            "SELECT sender_resident_id,parent_task_id FROM letters WHERE task_id=?", (current,)
        ).fetchone()
        if letter is None:
            break
        current = letter["parent_task_id"]
        if current is None:
            # An operator-sent letter has no parent task and no resident behind it; a
            # resident's letter without one still has a sender that walked here.
            if letter["sender_resident_id"] is not None:
                visited.append(letter["sender_resident_id"])
            break
    visited.reverse()
    return visited


def _lineage(db, task_id: str) -> tuple[str, int]:
    """The root task this chain rolls up to and the depth one more hop would reach."""
    letter = db.execute(
        "SELECT root_task_id,depth FROM letters WHERE task_id=?", (task_id,)
    ).fetchone()
    if letter is None:
        return task_id, 1
    return letter["root_task_id"], letter["depth"] + 1


def _sending_grant(db, run, now: int) -> dict | None:
    """The grant this run was admitted with, if it still permits sending; otherwise nothing.

    Authority is the grant the run was admitted with, not whatever the operator has
    granted since: a run admitted without `send_letters` never gains it mid-flight, and
    one whose grant was edited or revoked loses it. The caller reports every way of not
    holding it as one refusal, because the sender is told what it may do, not who
    changed it.
    """
    from hearth.management.authority import digest, read_grant

    pin = db.execute(
        "SELECT grant_revision,grant_sha256,expires_at FROM run_management WHERE run_id=?",
        (run["id"],),
    ).fetchone()
    if pin is None or pin["grant_revision"] is None or now >= pin["expires_at"]:
        return None
    grant = read_grant(db, run["resident_id"])
    policy = {key: value for key, value in grant.items() if key not in {"resident_id", "revision"}}
    if (
        not grant["enabled"]
        or grant["revision"] != pin["grant_revision"]
        or digest(policy) != pin["grant_sha256"]
        or "send_letters" not in grant["capabilities"]
    ):
        return None
    return grant


def _deadline(now: int, policy: dict, expires_at: int | None) -> int:
    """When this letter stops being worth answering.

    The household owns the shelf life. A sender may shorten it and never lengthen it,
    so a letter can be made urgent but never made to outlive the household's own rule.
    """
    deadline = now + policy["letter_ttl_seconds"]
    if expires_at is not None:
        if type(expires_at) is not int or not now < expires_at <= deadline:
            raise Refused("invalid_letter_deadline")
        deadline = expires_at
    return deadline


def _check_recipient(db, to: str) -> None:
    """The receiving end of every letter, whoever wrote it."""
    from hearth.residents.lifecycle import read_lifecycle

    if not db.execute("SELECT 1 FROM residents WHERE id=?", (to,)).fetchone():
        raise Refused("resident_not_found")
    if read_lifecycle(db, to)["state"] == "archived":
        raise Refused("recipient_archived")


def _check_daily_cap(db, to: str, now: int, policy: dict) -> None:
    """What one resident may be handed in a day, counted in that resident's own day.

    A letter is worked as an ordinary task, so every one of them spends the receiver's
    time and the household's money. The cap is the receiver's, not the sender's: it
    counts every letter that reached this resident today whoever wrote it, so no number
    of chatty colleagues — or of operator letters — adds up to a day nobody planned.
    A letter already sent counts whatever became of it; closing one does not buy another.
    """
    limit = policy["letter_daily_limit"]
    timezone = db.execute(
        "SELECT d.budget_timezone FROM declarations d JOIN residents r "
        "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
        (to,),
    ).fetchone()["budget_timezone"]
    window = _day_window(now, timezone)
    received = db.execute(
        "SELECT COUNT(*) FROM letters l JOIN tasks t ON t.id=l.task_id "
        "WHERE t.resident_id=? AND l.created_at>=? AND l.created_at<?",
        (to, window["starts_at"], window["ends_at"]),
    ).fetchone()[0]
    if received >= limit:
        raise Refused(
            "letter_daily_limit_reached",
            {"received_today": received, "letter_daily_limit": limit},
        )


def holds_post(db, run_id: str) -> bool:
    """Does this run's resident hold an end of a letter it already had when it was admitted?

    The reading half of `run_letter_scope`'s `post`, asked on its own so that the pin
    carrying the letter tools is decided by the same question the scope answers. A run
    offered a tool without a `run_management` row is offered no tool at all.
    """
    run = db.execute("SELECT resident_id,created_at FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        return False
    return (
        db.execute(
            "SELECT 1 FROM letters l JOIN tasks t ON t.id=l.task_id "
            "WHERE (t.resident_id=? OR l.sender_resident_id=?) AND l.created_at<=? LIMIT 1",
            (run["resident_id"], run["resident_id"], run["created_at"]),
        ).fetchone()
        is not None
    )


def run_letter_scope(db, run_id: str, now: int) -> dict:
    """Which letter tools one run is offered, and the letter a reply would answer.

    Sending is the grant this run was admitted with; replying is the letter this run is
    actually working; reading is having an end of a letter this run already had when it
    was admitted. A resident never sees a tool it may not use, and never loses sight of
    an answer to a question it already asked: an operator who narrows the grant stops the
    next letter, not the reply to the last one.

    The post a run may read is the post that existed when it was admitted. A letter
    arriving for the resident while one of its runs is starting is a write by somebody
    else, and this answer is compared against itself across a launch: without the bound,
    a colleague's letter landing in that window would change the tool set a starting run
    was pinned and end it as changed configuration. A run that gains its first letter
    mid-flight reads it on its next run, which is when it was offered the tool.
    """
    run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        return {"send": False, "reply": False, "post": False, "letter_id": None}
    letter = db.execute("SELECT task_id FROM letters WHERE task_id=?", (run["task_id"],)).fetchone()
    send = _sending_grant(db, run, now) is not None
    return {
        "send": send,
        "reply": letter is not None,
        "post": send or letter is not None or holds_post(db, run_id),
        "letter_id": letter["task_id"] if letter is not None else None,
    }


def send_letter(
    db,
    hearth,
    *,
    sender_run: str,
    to: str,
    title: str,
    detail: str,
    operation_id: str,
    expires_at: int | None = None,
) -> dict:
    """Queue a letter from the resident of `sender_run` to `to`, or refuse and write nothing.

    Idempotent on `operation_id` in the sending resident's own operation namespace: the
    same payload replays the original receipt, a changed one conflicts. The replay check
    comes before the guards, so a retry recovers the letter that was actually sent
    rather than being refused by a door that closed in between.
    """
    from hearth.management.authority import digest
    from hearth.management.tools import _existing_operation, _record_operation

    identifier(sender_run)
    identifier(to)
    identifier(operation_id)
    bounded_text(title, MAX_TITLE, "invalid_letter_title")
    bounded_text(detail, MAX_DETAIL, "invalid_letter_detail")
    now = int(hearth.clock())
    run = db.execute("SELECT * FROM runs WHERE id=?", (sender_run,)).fetchone()
    if run is None:
        raise Refused("run_not_found")
    if run["status"] not in {"starting", "running"} or run["finished_at"] is not None:
        raise Refused("run_not_active")
    sender = run["resident_id"]
    authority = {"actor": sender, "run_id": sender_run}
    payload = digest(["letter", to, title, detail, expires_at])
    previous = _existing_operation(db, authority, operation_id, payload)
    if previous:
        return previous

    grant = _sending_grant(db, run, now)
    if grant is None:
        raise Refused("letters_not_permitted")
    if grant["letter_recipient_ids"] and to not in grant["letter_recipient_ids"]:
        raise Refused("recipient_not_allowed")
    _check_recipient(db, to)
    if to == sender:
        raise Refused("self_letter")
    if not hearth.declared_letters_accept(db, to):
        raise Refused("letters_not_accepted")

    policy = read_letter_policy(db)
    parent_task_id = run["task_id"]
    root_task_id, depth = _lineage(db, parent_task_id)
    if depth > policy["max_letter_depth"]:
        raise Refused(
            "max_letter_depth_exceeded",
            {"depth": depth, "max_letter_depth": policy["max_letter_depth"]},
        )
    chain = _chain(db, parent_task_id)
    if to in chain:
        raise Refused("letter_cycle", {"chain": chain + [to]})
    _check_daily_cap(db, to, now, policy)

    deadline = _deadline(now, policy, expires_at)
    # The task instruction is the letter's own text and nothing else. Who wrote it and
    # what it is called are structured facts on the letter row, read back by the context
    # builder, so a sender cannot write a heading into its detail and have the receiver
    # read it as Hearth's own.
    task_id = _queue_task(
        db,
        to,
        detail,
        now,
        {"letter_operation": operation_id, "originating_run_id": sender_run},
    )
    db.execute(
        "INSERT INTO letters VALUES (?,?,?,?,?,?,?,?,?,'pending',NULL)",
        (
            task_id,
            sender,
            sender_run,
            parent_task_id,
            root_task_id,
            depth,
            title,
            now,
            deadline,
        ),
    )
    _audit(
        db,
        "letter.sent",
        task_id,
        now,
        {
            # Who wrote it, in one field a reader can trust for either hand.
            "sender": sender,
            "sender_resident_id": sender,
            "sender_run_id": sender_run,
            "recipient_resident_id": to,
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id,
            "depth": depth,
            "expires_at": deadline,
        },
    )
    return _record_operation(
        db,
        hearth,
        authority,
        operation_id,
        payload,
        {
            "resident_id": to,
            "task_id": task_id,
            "sender_resident_id": sender,
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id,
            "depth": depth,
            "expires_at": deadline,
            "status": "queued",
        },
    )


def send_operator_letter(
    db,
    hearth,
    *,
    command_id: str,
    to: str,
    title: str,
    detail: str,
    expires_at: int | None = None,
) -> dict:
    """Queue a letter the operator wrote, or refuse and write nothing.

    The operator holds no grant and needs none: there is no resident whose authority a
    grant check could bound, and the operator already owns every path that would
    configure one. What the operator cannot waive is the other side — the receiver's
    declared door, its archive state and the household's own reach all hold exactly as
    they do for a resident. The letter starts a chain of its own: no run wrote it and no
    hop came before it, so it is its own root at depth one.

    Idempotent on `command_id`, in the same namespace as an operator's submitted task:
    the same payload replays the original receipt and a changed one conflicts.
    """
    from hearth.management.authority import digest

    identifier(command_id)
    identifier(to)
    bounded_text(title, MAX_TITLE, "invalid_letter_title")
    bounded_text(detail, MAX_DETAIL, "invalid_letter_detail")
    now = int(hearth.clock())
    payload = digest(["operator_letter", to, title, detail, expires_at])
    previous = db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
    if previous:
        if previous["payload_digest"] != payload:
            raise Refused("command_conflict")
        return _receipt(db, previous["task_id"], command_id)
    _check_recipient(db, to)
    if not hearth.declared_letters_accept(db, to):
        raise Refused("letters_not_accepted")
    policy = read_letter_policy(db)
    # A household that closed the post closed it to the operator too: the setting is how
    # far a letter may travel, and the operator's own is already the first hop.
    if policy["max_letter_depth"] < 1:
        raise Refused(
            "max_letter_depth_exceeded",
            {"depth": 1, "max_letter_depth": policy["max_letter_depth"]},
        )
    _check_daily_cap(db, to, now, policy)
    deadline = _deadline(now, policy, expires_at)
    task_id = _queue_task(db, to, detail, now, {"letter_command": command_id, "sender": OPERATOR})
    db.execute(
        "INSERT INTO letters VALUES (?,?,?,?,?,?,?,?,?,'pending',NULL)",
        (task_id, None, None, None, task_id, 1, title, now, deadline),
    )
    # The command's recorded deadline is the letter's own shelf life: for a submitted
    # task that field is the deadline the caller retains for the effect it asked for,
    # and for a letter that is exactly when it stops being worth answering.
    db.execute(
        "INSERT INTO commands VALUES (?,?,?,?,?)", (command_id, payload, task_id, now, deadline)
    )
    _audit(
        db,
        "letter.sent",
        task_id,
        now,
        {
            "sender": OPERATOR,
            "sender_resident_id": None,
            "sender_run_id": None,
            "recipient_resident_id": to,
            "parent_task_id": None,
            "root_task_id": task_id,
            "depth": 1,
            "expires_at": deadline,
        },
    )
    return _receipt(db, task_id, command_id)


def _receipt(db, task_id: str, command_id: str) -> dict:
    """What the operator is told about one letter it wrote, replayed from the store."""
    row = db.execute(
        "SELECT l.*,t.resident_id,t.status FROM letters l JOIN tasks t ON t.id=l.task_id "
        "WHERE l.task_id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise Refused("letter_not_found")
    return {
        "command_id": command_id,
        "resident_id": row["resident_id"],
        "task_id": row["task_id"],
        "sender": OPERATOR,
        "parent_task_id": row["parent_task_id"],
        "root_task_id": row["root_task_id"],
        "depth": row["depth"],
        "expires_at": row["expires_at"],
        "status": row["status"],
    }


def reply_to_letter(
    db, hearth, *, run_id: str, letter_id: str, text: str, operation_id: str
) -> dict:
    """Record this run's one answer to the letter it is working, or refuse and write nothing.

    A run answers the letter it was admitted for and no other, and answers it once: the
    reply is what the sender reads, so a second one would leave two answers and no way
    to tell which was meant. A retry that keeps its `operation_id` replays the first
    receipt rather than being refused for the reply it already wrote.
    """
    from hearth.management.authority import digest
    from hearth.management.tools import _existing_operation, _record_operation

    identifier(run_id)
    identifier(letter_id)
    identifier(operation_id)
    bounded_text(text, MAX_REPLY, "invalid_letter_reply")
    now = int(hearth.clock())
    run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise Refused("run_not_found")
    if run["status"] not in {"starting", "running"} or run["finished_at"] is not None:
        raise Refused("run_not_active")
    authority = {"actor": run["resident_id"], "run_id": run_id}
    payload = digest(["letter_reply", letter_id, text])
    previous = _existing_operation(db, authority, operation_id, payload)
    if previous:
        return previous
    letter = db.execute("SELECT * FROM letters WHERE task_id=?", (letter_id,)).fetchone()
    if letter is None:
        raise Refused("letter_not_found")
    # The letter this run is working is the only one it can answer. Naming another
    # resident's letter buys nothing: the run is the address, not the argument.
    if letter["task_id"] != run["task_id"]:
        raise Refused("letter_not_addressed_here")
    if db.execute("SELECT 1 FROM letter_replies WHERE task_id=?", (letter_id,)).fetchone():
        raise Refused("letter_already_answered")
    db.execute(
        "INSERT INTO letter_replies VALUES (?,?,?,?,?)",
        (letter_id, run_id, run["resident_id"], text, now),
    )
    # The answer being written is one fact; what the letter finally came to is another,
    # recorded when its run ends. A reply written by a run that then fails still leaves
    # the sender an answer, so the two are never the same event.
    _audit(
        db,
        "letter.answered",
        letter_id,
        now,
        {
            "resident_id": run["resident_id"],
            "run_id": run_id,
            "sender_resident_id": letter["sender_resident_id"],
            "root_task_id": letter["root_task_id"],
            "depth": letter["depth"],
        },
    )
    return _record_operation(
        db,
        hearth,
        authority,
        operation_id,
        payload,
        {
            "resident_id": run["resident_id"],
            "letter_id": letter_id,
            "task_id": letter_id,
            "recipient_resident_id": letter["sender_resident_id"] or OPERATOR,
            "root_task_id": letter["root_task_id"],
            "written_at": now,
            "status": "answered",
        },
    )


def settle_letter(
    db,
    task_id: str,
    *,
    run_id: str,
    resident_id: str,
    status: str,
    artifact_id: str | None,
    now: int,
) -> str | None:
    """Say what became of the letter this run was working, or nothing if it was not one.

    A letter ends in exactly one state and says so out loud, because a question that
    goes quiet is worse than a question that is refused. The run answered it (`replied`),
    or it worked and never called the reply tool (`unanswered`), or it did not finish
    (`failed`). An answer already written survives its run failing afterwards: the sender
    has the answer, and the run's own status is recorded beside the state rather than
    hidden by it.

    Called inside the transaction that settles the run, so the state, the run's terminal
    status and the audit fact are one write. A letter already settled — by the expiry
    sweep, or by a retry of this settlement — keeps the state it has.
    """
    letter = db.execute("SELECT * FROM letters WHERE task_id=?", (task_id,)).fetchone()
    if letter is None or letter["state"] != "pending":
        return None
    reply = db.execute(
        "SELECT run_id,written_at FROM letter_replies WHERE task_id=?", (task_id,)
    ).fetchone()
    state = "replied" if reply else ("unanswered" if status == "succeeded" else "failed")
    db.execute("UPDATE letters SET state=?,settled_at=? WHERE task_id=?", (state, now, task_id))
    _audit(
        db,
        "letter." + state,
        task_id,
        now,
        {
            "state": state,
            "run_id": run_id,
            "run_status": status,
            "artifact_id": artifact_id,
            "sender_resident_id": letter["sender_resident_id"],
            "sender": letter["sender_resident_id"] or OPERATOR,
            "recipient_resident_id": resident_id,
            "root_task_id": letter["root_task_id"],
            "depth": letter["depth"],
            "reply_run_id": reply["run_id"] if reply else None,
        },
    )
    return state


def _view(row, *, excerpt: int = 500, answer_text: bool = True) -> dict:
    """One letter as a reader of a list of them sees it, with its answer if it has one.

    A reader that is already handed the answer in a list of its own — a sender reading
    its own post — is shown who answered and when, and not the body a second time. One
    call carries three bounded lists, and an answer is up to `MAX_REPLY` of them.
    """
    instruction = row["instruction"]
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "sender": row["sender_resident_id"] or OPERATOR,
        "sender_resident_id": row["sender_resident_id"],
        "sender_run_id": row["sender_run_id"],
        "recipient_resident_id": row["recipient"],
        "parent_task_id": row["parent_task_id"],
        "root_task_id": row["root_task_id"],
        "depth": row["depth"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "status": row["status"],
        "state": row["state"],
        "settled_at": row["settled_at"],
        "instruction": instruction[:excerpt],
        "instruction_truncated": len(instruction) > excerpt,
        "reply": None
        if row["reply_run"] is None
        else {
            "resident_id": row["reply_resident"],
            "run_id": row["reply_run"],
            "written_at": row["reply_at"],
        }
        | ({"text": row["reply_text"]} if answer_text else {}),
    }


# One letter, the task it is and the answer it has, if it has one.
_LETTERS = (
    "SELECT l.*,t.resident_id AS recipient,t.status,t.instruction,"
    "p.run_id AS reply_run,p.resident_id AS reply_resident,"
    "p.text AS reply_text,p.written_at AS reply_at "
    "FROM letters l JOIN tasks t ON t.id=l.task_id "
    "LEFT JOIN letter_replies p ON p.task_id=l.task_id "
)
# Only the answered ones, ordered by when the answer was written.
_ANSWERED = _LETTERS.replace("LEFT JOIN letter_replies", "JOIN letter_replies")


def read_letters(
    db, resident_id: str, *, since: int = 0, limit: int = MAX_PAGE, offset: int = 0
) -> dict:
    """What one resident has been sent, what its own letters came to, and their answers.

    Bounded on every side, newest first, filtered by time: a resident reads what is newer
    than the last time it looked, and a truncated page is reached by asking again one
    page further back. `since` is exclusive, so the row a cursor was taken from is not
    handed back on the next call and reading twice converges. A reply belongs to the
    resident that asked the question, never to the run that wrote it — that run already
    has an artifact of its own.
    """
    identifier(resident_id)
    if (
        type(since) is not int
        or since < 0
        or type(limit) is not int
        or not 1 <= limit <= MAX_PAGE
        or type(offset) is not int
        or offset < 0
    ):
        raise Refused("invalid_letter_page")
    received = db.execute(
        _LETTERS + "WHERE t.resident_id=? AND l.created_at>? "
        "ORDER BY l.created_at DESC,l.task_id DESC LIMIT ? OFFSET ?",
        (resident_id, since, limit + 1, offset),
    ).fetchall()
    replies = db.execute(
        _ANSWERED + "WHERE l.sender_resident_id=? AND p.written_at>? "
        "ORDER BY p.written_at DESC,l.task_id DESC LIMIT ? OFFSET ?",
        (resident_id, since, limit + 1, offset),
    ).fetchall()
    # A sender's own letters, newest movement first. A letter is new to this reader when
    # it is written and again when it ends, so `since` is measured against whichever of
    # those happened last: a letter that went unanswered or failed surfaces once more,
    # carrying the state, rather than leaving the resident that asked to guess.
    sent = db.execute(
        _LETTERS + "WHERE l.sender_resident_id=? AND COALESCE(l.settled_at,l.created_at)>? "
        "ORDER BY COALESCE(l.settled_at,l.created_at) DESC,l.task_id DESC LIMIT ? OFFSET ?",
        (resident_id, since, limit + 1, offset),
    ).fetchall()
    return {
        "resident_id": resident_id,
        "since": since,
        "limit": limit,
        "offset": offset,
        "received": [_view(row) for row in received[:limit]],
        "received_truncated": len(received) > limit,
        # The answers themselves are the next list; here a sent letter says who answered
        # it and when, so one call does not carry the same text twice.
        "sent": [_view(row, answer_text=False) for row in sent[:limit]],
        "sent_truncated": len(sent) > limit,
        "replies": [
            {
                "letter_id": row["task_id"],
                "title": row["title"],
                "resident_id": row["recipient"],
                "run_id": row["reply_run"],
                "root_task_id": row["root_task_id"],
                "depth": row["depth"],
                "written_at": row["reply_at"],
                "text": row["reply_text"],
            }
            for row in replies[:limit]
        ],
        "replies_truncated": len(replies) > limit,
    }


def _is_example(db, run_id: str) -> bool:
    """Is this run one case of a skill example rather than a working day of its own?"""
    return (
        db.execute("SELECT 1 FROM skill_validation_cases WHERE run_id=?", (run_id,)).fetchone()
        is not None
    )


def run_replies(db, run_id: str) -> list[dict]:
    """The answers this run opens with: replies to its resident's own letters, newest first.

    A sender is never woken by an answer — it reads one on its next run, and this is
    that reading. The window runs from the second the resident's previous opening run
    began, included, up to the second this one was admitted, excluded. Half-open the
    same way at both ends is what makes those seconds add up: an answer written in the
    same second a run was admitted may have arrived just after that run's context was
    built, so it belongs to the run after it, which takes that same second as its own
    lower edge, and nothing falls between two windows.

    Hearth keeps time in whole seconds, so the one thing this cannot separate is two
    runs of the same resident admitted within one second: they share a window and may
    open with the same answer. Repeating an answer to a question this resident asked is
    the safe direction; the alternative is losing it, and no ordering of two rows
    written in the same second is more honest than the second they share.

    A skill example is not one of these runs. It rehearses exactly what its request
    named — the memory revision it pinned, the input set it chose — so that the same
    candidate can be judged twice and compared; a colleague's answer to an unrelated
    question is not part of that request. It opens with none, and it is skipped when
    the next real run looks back for where to start, so an answer that arrived while an
    example ran still reaches the run that asked for it.

    Both edges are read from stored facts and neither moves afterwards. The far edge is
    this run's own admission, for the same reason the offered tool set is bounded there:
    a colleague answering while this run is starting is somebody else's write, and
    without the bound it would change a context that was already pinned and digested.
    The answering resident's name is read through the revision its replying run was
    admitted with, so renaming anybody cannot move a digest either.
    """
    run = db.execute("SELECT resident_id,created_at FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None or _is_example(db, run_id):
        return []
    previous = db.execute(
        "SELECT MAX(r.created_at) FROM runs r WHERE r.resident_id=? AND r.created_at<? "
        "AND NOT EXISTS(SELECT 1 FROM skill_validation_cases c WHERE c.run_id=r.id)",
        (run["resident_id"], run["created_at"]),
    ).fetchone()[0]
    return [
        {
            "letter_id": row["task_id"],
            "title": row["title"][:MAX_TITLE],
            "resident_id": row["resident_id"],
            "resident_name": row["name"] or row["resident_id"],
            "root_task_id": row["root_task_id"],
            "written_at": row["written_at"],
            "text": row["text"][:MAX_REPLY],
        }
        for row in db.execute(
            "SELECT p.*,l.title,l.root_task_id,d.name FROM letter_replies p "
            "JOIN letters l ON l.task_id=p.task_id JOIN runs r ON r.id=p.run_id "
            "LEFT JOIN declarations d ON d.resident_id=r.resident_id "
            "AND d.revision=r.resident_revision "
            "WHERE l.sender_resident_id=? AND p.written_at>=? AND p.written_at<? "
            "ORDER BY p.written_at DESC,p.task_id DESC LIMIT ?",
            (
                run["resident_id"],
                previous if previous is not None else 0,
                run["created_at"],
                MAX_RUN_REPLIES,
            ),
        )
    ]


def operator_letters(db, resident_id: str, *, limit: int = 30, offset: int = 0) -> dict:
    """Every letter one resident received and every letter it wrote, newest first."""
    identifier(resident_id)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise Refused("invalid_letter_page")
    if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
        raise Refused("resident_not_found")
    order = "ORDER BY l.created_at DESC,l.task_id DESC LIMIT ? OFFSET ?"
    return {
        "resident_id": resident_id,
        "limit": limit,
        "offset": offset,
        "inbox": [
            _view(row)
            for row in db.execute(
                _LETTERS + "WHERE t.resident_id=? " + order, (resident_id, limit, offset)
            )
        ],
        "sent": [
            _view(row)
            for row in db.execute(
                _LETTERS + "WHERE l.sender_resident_id=? " + order, (resident_id, limit, offset)
            )
        ],
    }


def expire_letters(hearth) -> list[str]:
    """Close every letter that went stale before anyone started it.

    An expired letter is a failed task with a reason, not silence: the sender asked a
    question nobody answered in time, and the household spends nothing answering it now.

    The sweep runs on every supervisor pass and almost always finds nothing, so it looks
    first in a read transaction and takes the writer only for the letters it found. The
    write pass re-checks each one: a letter admitted in between is no longer queued and
    is left to the run that started it.
    """
    with hearth.database.transaction() as db:
        pending = [
            row[0]
            for row in db.execute(
                "SELECT l.task_id FROM letters l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.status='queued' AND l.expires_at<=? LIMIT 100",
                (int(hearth.clock()),),
            )
        ]
    if not pending:
        return []
    expired = []
    with hearth.database.transaction(write=True) as db:
        now = int(hearth.clock())
        names = ",".join("?" * len(pending))
        for row in db.execute(
            "SELECT l.*,t.resident_id FROM letters l JOIN tasks t ON t.id=l.task_id "
            f"WHERE l.task_id IN ({names}) AND t.status='queued' AND l.expires_at<=?",
            (*pending, now),
        ).fetchall():
            db.execute("UPDATE tasks SET status='failed' WHERE id=?", (row["task_id"],))
            db.execute(
                "UPDATE letters SET state='expired',settled_at=? WHERE task_id=?",
                (now, row["task_id"]),
            )
            _audit(
                db,
                "letter.expired",
                row["task_id"],
                now,
                {
                    "state": "expired",
                    "reason": "letter_expired",
                    "sender_resident_id": row["sender_resident_id"],
                    "sender_run_id": row["sender_run_id"],
                    "recipient_resident_id": row["resident_id"],
                    "root_task_id": row["root_task_id"],
                    "depth": row["depth"],
                    "expires_at": row["expires_at"],
                },
            )
            expired.append(row["task_id"])
    return expired


def deliver_letters(hearth) -> list[str]:
    """Admit the letters waiting on a resident that can work one now, oldest first.

    A letter is delivered by being worked, and this is the whole of the delivery: no
    watcher, no poller, no inbox to drain. The supervision tick that admits routine
    occurrences admits letter tasks the same way and through the same operation, so a
    letter is bounded by the receiver's allocation, the shared household allowance and
    the receiver's own pause and archive state — never by anything its sender holds.

    A receiver that is paused, busy, archived or out of allowance keeps its letter
    queued for a later pass, exactly as a routine occurrence waits; the letter's own
    shelf life is what eventually closes it. Any other refusal is a real fault and is
    raised once the bounded pass is done, so one broken resident cannot starve the rest.
    """
    with hearth.database.transaction() as db:
        waiting = [
            row[0]
            for row in db.execute(
                "SELECT l.task_id FROM letters l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.status='queued' AND l.expires_at>? "
                "ORDER BY l.created_at,l.task_id LIMIT 100",
                (int(hearth.clock()),),
            )
        ]
    delivered: list[str] = []
    first_refusal = None
    for task_id in waiting:
        try:
            hearth.admit(task_id, reserve=LETTER_RESERVATION)
            delivered.append(task_id)
        except Refused as error:
            if error.code not in LETTER_WAITS and first_refusal is None:
                first_refusal = error
    if first_refusal is not None:
        raise first_refusal
    return delivered


def validate_letters(db) -> None:
    """Every letter's lineage must still hold after a copy.

    A backup that kept the rows but lost the chain would let a restored household
    attribute cost to the wrong origin or answer a letter it never received, so the
    depth, the root and both ends are checked against the tasks they name. A reply is
    checked the same way: it belongs to the run that worked the letter it answers.
    """
    for row in db.execute(
        "SELECT l.*,t.resident_id AS recipient,t.status,"
        "EXISTS(SELECT 1 FROM letter_replies p WHERE p.task_id=l.task_id) AS answered,"
        "EXISTS(SELECT 1 FROM runs r WHERE r.task_id=l.task_id) AS worked "
        "FROM letters l JOIN tasks t ON t.id=l.task_id"
    ):
        # What a letter came to is the one word its sender reads, so a copy cannot carry
        # a state its own rows contradict: an answer nobody wrote, an unanswered letter
        # whose run never succeeded, or one closed for going stale after being worked.
        state, terminal = row["state"], row["status"] in {"succeeded", "failed", "cancelled"}
        # A letter is open exactly while the task it is: one still open whose task has
        # ended, or one settled whose task has not, is a copy that no longer adds up.
        if (state == "pending") == terminal:
            raise Refused("backup_letters_invalid")
        if state != "pending" and (state == "replied") != bool(row["answered"]):
            raise Refused("backup_letters_invalid")
        if state == "unanswered" and row["status"] != "succeeded":
            raise Refused("backup_letters_invalid")
        if state == "expired" and (row["worked"] or row["status"] != "failed"):
            raise Refused("backup_letters_invalid")
        if row["sender_run_id"] is None:
            # The operator's own hand: no resident, no run, and the start of its chain.
            if (
                row["sender_resident_id"] is not None
                or row["parent_task_id"] is not None
                or row["depth"] != 1
                or row["root_task_id"] != row["task_id"]
            ):
                raise Refused("backup_letters_invalid")
            continue
        sender = db.execute(
            "SELECT resident_id,task_id FROM runs WHERE id=?", (row["sender_run_id"],)
        ).fetchone()
        if sender is None or sender["resident_id"] != row["sender_resident_id"]:
            raise Refused("backup_letters_invalid")
        if row["recipient"] == row["sender_resident_id"]:
            raise Refused("backup_letters_invalid")
        if row["parent_task_id"] is None:
            if row["depth"] != 1 or row["root_task_id"] != row["task_id"]:
                raise Refused("backup_letters_invalid")
            continue
        # The parent is the work the sender was actually doing. Without this, a copy
        # could repoint a letter at any task of the same depth and root, and the
        # restored household would attribute the chain to a hop that never happened.
        if row["parent_task_id"] != sender["task_id"]:
            raise Refused("backup_letters_invalid")
        root, depth = _lineage(db, row["parent_task_id"])
        if row["root_task_id"] != root or row["depth"] != depth:
            raise Refused("backup_letters_invalid")
        if row["recipient"] in _chain(db, row["parent_task_id"]):
            raise Refused("backup_letters_invalid")
    for row in db.execute(
        "SELECT p.*,t.resident_id AS recipient FROM letter_replies p JOIN tasks t ON t.id=p.task_id"
    ):
        # An answer nobody was asked for, or one moved onto another resident's letter,
        # would be read by the sender as this household's own reply. It is not.
        run = db.execute(
            "SELECT resident_id,task_id FROM runs WHERE id=?", (row["run_id"],)
        ).fetchone()
        if (
            run is None
            or run["resident_id"] != row["resident_id"]
            or run["task_id"] != row["task_id"]
            or row["recipient"] != row["resident_id"]
        ):
            raise Refused("backup_letters_invalid")
