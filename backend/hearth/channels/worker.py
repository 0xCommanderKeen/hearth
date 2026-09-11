"""One owned communications lifetime; short writers surround bounded transport I/O."""

import fcntl
import threading
from contextlib import ExitStack

from hearth.channels.chat.config import read
from hearth.channels.chat.reply import Replies
from hearth.channels.chat.service import Conversations, scope
from hearth.channels.delivery.model import Receipt
from hearth.channels.delivery.notifications import Forwarding
from hearth.channels.delivery.service import Delivery
from hearth.channels.polling import (
    PAGE_SIZE,
    REQUEST_SECONDS,
    RESPONSE_BYTES,
    Page,
    RateLimit,
    RetryLater,
    SendResult,
    cursor,
)
from hearth.management.authority import digest
from hearth.residents.models import Refused
from hearth.work.service import _audit


class Worker:
    def __init__(self, hearth, secrets=None, factories=None):
        self.hearth, self.secrets = hearth, secrets
        from hearth.channels.discord import Discord

        self.factories = {"discord": Discord} if factories is None else dict(factories)
        self.conversations = Conversations(hearth, {})
        self.replies = Replies(hearth, secrets) if secrets is not None else None
        self.delivery = Delivery(hearth, replies=self.replies)
        self._sessions = {}
        self._secret_keys = {}
        self._lock = None
        self._stop = threading.Event()
        self._thread = None
        self._guard = threading.Lock()
        self._io_guard = threading.RLock()
        self._health = {"communications": "stopped", "error": None}
        self._credentials = {}
        self._offset = 0
        self._reply_after = ""
        self._admit_after = ""
        self._history_after = ("", "")
        self.forwarding = Forwarding(self.delivery)
        self._forward_after = ""

    def health(self):
        with self._guard:
            result: dict = dict(self._health)
            result["credentials"] = {key: dict(value) for key, value in self._credentials.items()}
        result["connections"] = {
            key: adapter.health()
            for key, (_, _, _, adapter) in list(self._sessions.items())
            if callable(getattr(adapter, "health", None))
        }
        return result

    def _state(self, **values):
        with self._guard:
            self._health.update(values)

    def _writable(self):
        if self.hearth.database.restored():
            raise Refused("restored_copy_read_only")

    def __enter__(self):
        self._writable()
        if self._lock is not None:
            raise Refused("communications_already_started")
        lock = self.hearth.database.path.with_suffix(".communications.lock").open("a+b")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise Refused("communications_worker_owned") from None
        self._lock = lock
        self._state(communications="running", error=None)
        return self

    def __exit__(self, *exc):
        with self._io_guard:
            return self._exit(*exc)

    def _exit(self, *exc):
        failure = None
        try:
            for key in list(self._sessions):
                try:
                    self._drop(key)
                except Exception as error:
                    failure = error
        finally:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            self._state(communications="stopped")
        if failure is not None:
            raise failure

    def start(self):
        self.__enter__()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hearth-communications")
        try:
            self._thread.start()
        except BaseException:
            self.__exit__()
            raise

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    self.step()
                    self._state(error=None)
                except Exception as error:
                    self._state(error=type(error).__name__)
                self._stop.wait(0.5)
        finally:
            self.__exit__()

    def _drop(self, key):
        session = self._sessions.pop(key, None)
        self._secret_keys.pop(key, None)
        if session:
            try:
                session[3].close()
            except Exception:
                self._state(error="transport_close_failed")
            finally:
                session[0].close()

    def _eligible(self, kind, identity):
        with self.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT eligible_at FROM communications_schedule WHERE kind=? AND id=?",
                (kind, identity),
            ).fetchone()
            return row is None or row[0] <= int(self.hearth.clock())

    def _schedule(self, kind, identity, seconds, error=None):
        with self.hearth.database.transaction(write=True) as db:
            db.execute(
                "INSERT INTO communications_schedule VALUES (?,?,?,?) ON CONFLICT(kind,id) "
                "DO UPDATE SET eligible_at=MAX(communications_schedule.eligible_at,"
                "excluded.eligible_at),"
                "error=excluded.error",
                (kind, identity, int(self.hearth.clock()) + seconds, error),
            )

    def _flush_limits(self, identity, adapter):
        if not callable(getattr(adapter, "take_limits", None)):
            return
        for limit in adapter.take_limits():
            if type(limit) is not RateLimit or type(limit.seconds) is not int or limit.seconds < 0:
                raise ValueError("invalid adapter rate limit")
            if limit.channel_id is not None:
                kind, target = "channel", digest([identity, limit.channel_id])
            elif limit.guild_id is not None:
                kind, target = "guild", digest([identity, limit.guild_id])
            else:
                kind, target = "connection", identity
            self._schedule(kind, target, limit.seconds, "rate_limited")

    def _destination_waiting(self, identity, address):
        return not self._eligible(
            "guild", digest([identity, address["guild_id"]])
        ) or not self._eligible("channel", digest([identity, address["channel_id"]]))

    def _failure(self, kind, identity, connection_id, error):
        # Never persist exception messages: libraries can include URLs, tokens or bodies.
        if isinstance(error, RetryLater):
            if kind == "route" and not error.connection_wide:
                with self.hearth.database.transaction() as db:
                    route = read(db, "route", identity)
                if route is not None:
                    destination = digest(route["address"] | {"connection_id": connection_id})
                    self._schedule(
                        "destination",
                        destination,
                        error.seconds,
                        getattr(error, "code", "rate_limited"),
                    )
            self._schedule(
                "connection" if error.connection_wide else kind,
                connection_id if error.connection_wide else identity,
                error.seconds,
                getattr(error, "code", "rate_limited"),
            )
        elif isinstance(error, Refused):
            code = (
                error.code
                if error.code
                in {
                    "communications_transport_unavailable",
                    "communications_pending",
                    "communications_secret_invalid",
                    "communications_secret_reference_invalid",
                    "communications_secret_changed",
                    "delivery_installation_not_owned",
                    "delivery_worker_owned",
                    "communications_route_inactive",
                    "communications_scope_denied",
                    "communications_route_changed",
                }
                else "route_unavailable"
            )
            self._schedule(kind, identity, 5, code)
        else:
            self._schedule(kind, identity, 5, "route_unavailable")

    def _session(self, identity, connection, fingerprint):
        if self.secrets is None or connection["transport"] not in self.factories:
            self._drop(identity)
            raise Refused("communications_transport_unavailable")
        try:
            secret = self.secrets.resolve(connection["secret_ref"])
        except Refused:
            self._credential_state(identity, connection, "invalid")
            self._drop(identity)
            raise
        self._credential_state(identity, connection, "missing" if secret is None else "configured")
        if secret is None:
            self._drop(identity)
            raise Refused("communications_pending")
        key = digest([connection, fingerprint, secret])
        previous = self._sessions.get(identity)
        if previous and previous[2] == key:
            return previous
        # Retain delivery ownership when credentials/routes change. Releasing the
        # lock between reloads would let another worker take over this lifetime.
        if previous:
            try:
                previous[3].close()
            except Exception:
                self._state(error="transport_close_failed")
            stack, owner = previous[:2]
            del self._sessions[identity]
        else:
            stack = ExitStack()
            owner = stack.enter_context(self.delivery.worker(identity))
        try:
            # Factories only construct local clients; no I/O or permission probes.
            adapter = self.factories[connection["transport"]](connection, secret)
            session = (stack, owner, key, adapter)
            self._sessions[identity] = session
            self._secret_keys[identity] = digest(secret)
            return session
        except BaseException:
            stack.close()
            raise

    def _credential_state(self, identity, connection, state):
        with self._guard:
            self._credentials[identity] = {
                "state": state,
                "checked_at": int(self.hearth.clock()),
                "revision": connection["revision"],
            }

    def _check_secret(self, identity, connection):
        value = self.secrets.resolve(connection["secret_ref"]) if self.secrets is not None else None
        if value is None or digest(value) != self._secret_keys.get(identity):
            raise Refused("communications_secret_changed")

    def step(self):
        with self._io_guard:
            return self._step()

    def _step(self):
        self._writable()  # Before even loading a factory or probing a secret.
        if self._lock is None:
            raise Refused("communications_worker_not_owned")
        with self.hearth.database.transaction() as db:
            connections = {
                r[0]: read(db, "connection", r[0])
                for r in db.execute(
                    "SELECT id FROM communications_config WHERE kind='connection' ORDER BY id"
                )
            }
            routes = [
                (r[0], read(db, "route", r[0]))
                for r in db.execute(
                    "SELECT id FROM communications_config WHERE kind='route' ORDER BY id"
                )
            ]
            fingerprint = [
                tuple(r)
                for r in db.execute(
                    "SELECT kind,id,revision FROM communications_config ORDER BY kind,id"
                )
            ]
        for identity in list(self._sessions):
            connection = connections.get(identity)
            if connection is None or connection["state"] != "active":
                self._drop(identity)
        adapters = {}
        for identity, connection in connections.items():
            if connection is None or connection["state"] != "active":
                continue
            try:
                # Reload even while scheduled: revoked/changed secrets invalidate caches now.
                adapters[identity] = self._session(identity, connection, fingerprint)
            except Exception as error:
                self._drop(identity)
                self._failure("connection", identity, identity, error)
        self._forward_after = self.forwarding.step(self._forward_after)
        from hearth.channels.history import process

        process(self, adapters)
        self.conversations.expire()
        if self.replies is not None:
            with self.hearth.database.transaction() as db:
                pending = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM chat_turns WHERE state='reply_pending' AND id>? "
                        "ORDER BY id LIMIT 50",
                        (self._reply_after,),
                    )
                ]
            self._reply_after = pending[-1] if pending else ""
            for turn_id in pending:
                try:
                    if self.replies.prepare(turn_id) is not None:
                        with self.hearth.database.transaction(write=True) as db:
                            self.replies.handoff_in_transaction(
                                db, turn_id, self.delivery.enqueue_in_transaction
                            )
                except Exception:
                    continue
        # Rotate the route budget so a failing first route cannot starve later ones.
        if routes:
            ordered = routes[self._offset :] + routes[: self._offset]
            self._offset = (self._offset + 16) % len(routes)
            for route_id, route in ordered[:16]:
                if route is None:
                    continue
                connection_id = route["connection_id"]
                if (
                    route["state"] != "active"
                    or connection_id not in adapters
                    or not self._eligible("connection", connection_id)
                    or not self._eligible("route", route_id)
                    or self._destination_waiting(connection_id, route["address"])
                    or not self._eligible(
                        "destination", digest(route["address"] | {"connection_id": connection_id})
                    )
                ):
                    continue
                try:
                    try:
                        self._poll(route_id, adapters[connection_id][3])
                    finally:
                        self._flush_limits(connection_id, adapters[connection_id][3])
                    self._schedule("route", route_id, 1)
                except Exception as error:
                    self._failure("route", route_id, connection_id, error)
        with self.hearth.database.transaction() as db:
            queued = [
                tuple(r)
                for r in db.execute(
                    "SELECT t.task_id,c.connection_id FROM chat_turns t "
                    "JOIN tasks w ON w.id=t.task_id "
                    "JOIN chat_conversations c ON c.id=t.conversation_id "
                    "WHERE t.state='working' AND w.status='queued' AND t.task_id>? "
                    "ORDER BY t.task_id LIMIT 50",
                    (self._admit_after,),
                )
            ]
        self._admit_after = queued[-1][0] if queued else ""
        for task_id, connection_id in queued:
            if connection_id not in adapters:
                continue
            try:
                self._check_secret(connection_id, connections[connection_id])
                self.hearth.admit(task_id, reserve=10_000)
            except Refused:
                pass  # Ordinary budgets, pauses, concurrency and freshness remain authoritative.
        for identity, (_, owner, _, adapter) in adapters.items():
            if not self._eligible("connection", identity):
                continue
            try:
                self._writable()
                with self.hearth.database.transaction() as db:
                    connection = read(db, "connection", identity)
                if connection is None or connection["state"] != "active":
                    self._drop(identity)
                    continue
                _, owner, _, adapter = self._session(identity, connection, fingerprint)
                with self.hearth.database.transaction() as db:
                    deferred = frozenset(
                        r[0]
                        for r in db.execute(
                            "SELECT id FROM communications_schedule WHERE kind='destination' "
                            "AND eligible_at>?",
                            (int(self.hearth.clock()),),
                        )
                    )
                with self.hearth.database.transaction() as db:
                    import json

                    destinations = [
                        json.loads(r[0])["destination"]
                        for r in db.execute(
                            "SELECT intent FROM delivery_operations WHERE connection_id=? "
                            "AND state='queued'",
                            (identity,),
                        )
                    ]
                deferred |= frozenset(
                    digest(d)
                    for d in destinations
                    if "guild_id" in d and self._destination_waiting(identity, d)
                )
                permit = self.delivery.prepare(identity, owner, deferred_destinations=deferred)
                if permit is None:
                    continue
                try:
                    protected = self.secrets.known_values if self.secrets is not None else ()
                    if any(value and value in permit.intent.text for value in protected):
                        receipt = Receipt(
                            attempt_id=permit.attempt_id,
                            intent_sha256=permit.intent_sha256,
                            outcome="refused",
                            evidence="protected_text",
                        )
                    else:
                        receipt = adapter.send(
                            permit, max_bytes=RESPONSE_BYTES, timeout=REQUEST_SECONDS
                        )
                    self._flush_limits(identity, adapter)
                    connection_wide = False
                    if type(receipt) is SendResult:
                        connection_wide, receipt = receipt.connection_wide, receipt.receipt
                        if type(connection_wide) is not bool:
                            raise ValueError("invalid scheduling scope")
                    if type(receipt) is not Receipt:
                        raise ValueError("invalid receipt")
                    if receipt.retry_after:
                        self._schedule(
                            "connection" if connection_wide else "destination",
                            identity
                            if connection_wide
                            else digest(permit.intent.destination.model_dump()),
                            receipt.retry_after,
                            "rate_limited",
                        )
                    self.delivery.complete(permit, receipt)
                except Exception as error:
                    self._flush_limits(identity, adapter)
                    # A raised exception cannot prove that the external request did not escape.
                    if isinstance(error, RetryLater):
                        self._schedule(
                            "connection" if error.connection_wide else "destination",
                            identity
                            if error.connection_wide
                            else digest(permit.intent.destination.model_dump()),
                            error.seconds,
                            "rate_limited",
                        )
                    self.delivery.complete(
                        permit,
                        Receipt(
                            attempt_id=permit.attempt_id,
                            intent_sha256=permit.intent_sha256,
                            outcome="unknown",
                            evidence="transport_exception",
                        ),
                    )
            except Exception as error:
                self._failure("connection", identity, identity, error)

    def probe(self, connection_id: str, route_id: str):
        """Explicit bounded permission check through this worker's owned client.

        This never polls, establishes cursors, admits tasks or dispatches messages.
        The same I/O guard serializes it with worker passes and shutdown.
        """
        with self._io_guard:
            self._writable()
            if self._lock is None:
                raise Refused("communications_worker_not_owned")

            def selected(db):
                route = read(db, "route", route_id)
                connection = read(db, "connection", connection_id)
                if (
                    route is None
                    or route["connection_id"] != connection_id
                    or route["state"] != "active"
                ):
                    raise Refused("communications_route_inactive")
                if connection is None or connection["state"] != "active":
                    raise Refused("communications_pending")
                grant = read(db, "grant", route["resident_id"])
                destination = route["address"] | {"connection_id": connection_id}
                powers = {
                    k: grant is not None and destination in grant[k]
                    for k in ("read", "listen", "reply", "post")
                }
                if not any(powers.values()):
                    raise Refused("communications_scope_denied")
                return route, connection, grant, powers

            with self.hearth.database.transaction() as db:
                pins = selected(db)
                fingerprint = [
                    tuple(r)
                    for r in db.execute(
                        "SELECT kind,id,revision FROM communications_config ORDER BY kind,id"
                    )
                ]
            route, connection, _, powers = pins
            if (
                not self._eligible("connection", connection_id)
                or not self._eligible("route", route_id)
                or self._destination_waiting(connection_id, route["address"])
            ):
                raise Refused("communications_retry_scheduled")
            _, owner, _, adapter = self._session(connection_id, connection, fingerprint)
            if not callable(getattr(adapter, "probe", None)):
                raise Refused("communications_probe_unavailable")
            self._check_secret(connection_id, connection)
            with self.hearth.database.transaction(write=True) as db:
                self.delivery._binding(db, connection_id, owner)
                if selected(db) != pins:
                    raise Refused("communications_route_changed")
                _audit(
                    db,
                    "communications.probe_requested",
                    route_id,
                    int(self.hearth.clock()),
                    {"connection_id": connection_id},
                )
            try:
                adapter.probe(
                    route["address"]["guild_id"],
                    route["address"]["channel_id"],
                    read=powers["read"] or powers["listen"],
                    send=powers["reply"] or powers["post"],
                    max_bytes=RESPONSE_BYTES,
                    timeout=REQUEST_SECONDS,
                )
            except Exception as error:
                self._failure("route", route_id, connection_id, error)
                raise Refused("communications_probe_failed") from None
            finally:
                self._flush_limits(connection_id, adapter)
            self._check_secret(connection_id, connection)
            with self.hearth.database.transaction(write=True) as db:
                self.delivery._binding(db, connection_id, owner)
                if selected(db) != pins:
                    raise Refused("communications_route_changed")
                _audit(
                    db,
                    "communications.probed",
                    route_id,
                    int(self.hearth.clock()),
                    {"connection_id": connection_id},
                )
            return {
                "connection_id": connection_id,
                "route_id": route_id,
                "health": adapter.health(),
            }

    def _poll(self, route_id, adapter):
        self._writable()
        with self.hearth.database.transaction() as db:
            route, connection, grant = scope(db, route_id)
            key = (
                route["connection_id"],
                route["address"]["guild_id"],
                route["address"]["channel_id"],
            )
            row = db.execute(
                "SELECT * FROM communications_cursors WHERE connection_id=? "
                "AND guild_id=? AND channel_id=?",
                key,
            ).fetchone()
        pins = (route, connection, grant)
        self._check_secret(key[0], connection)
        if row is None or row["through_id"] is None:
            latest = adapter.latest(*key[1:], max_bytes=RESPONSE_BYTES, timeout=REQUEST_SECONDS)
            cursor(latest)
            self._check_secret(key[0], connection)
            with self.hearth.database.transaction(write=True) as db:
                if scope(db, route_id) != pins:
                    raise Refused("communications_route_changed")
                if row is None:
                    db.execute(
                        "INSERT INTO communications_cursors VALUES (?,?,?,?,?,NULL,?,NULL)",
                        (*key, latest, latest, int(self.hearth.clock())),
                    )
                    _audit(
                        db,
                        "communications.baseline",
                        route_id,
                        int(self.hearth.clock()),
                        {"cursor": latest},
                    )
                    return
                if cursor(latest) < cursor(row["cursor"]):
                    latest = row["cursor"]
                db.execute(
                    "UPDATE communications_cursors SET through_id=? WHERE connection_id=? "
                    "AND guild_id=? AND channel_id=?",
                    (latest, *key),
                )
            through = latest
        else:
            through = row["through_id"]
        after = row["cursor"]
        scan = row["scan_before"]
        page = adapter.poll(
            *key[1:],
            after=after,
            through=through,
            limit=PAGE_SIZE,
            max_bytes=RESPONSE_BYTES,
            timeout=REQUEST_SECONDS,
            **({"scan_before": scan} if scan is not None else {}),
        )
        if (
            type(page) is not Page
            or type(page.complete) is not bool
            or len(page.messages) > PAGE_SIZE
        ):
            raise Refused("communications_page_invalid")
        if page.scan_before is not None:
            if (
                page.messages
                or page.complete
                or page.examined_through is not None
                or not (
                    cursor(after)
                    < cursor(page.scan_before)
                    < (cursor(scan) if scan is not None else cursor(through) + 1)
                )
            ):
                raise Refused("communications_scan_invalid")
            self._check_secret(key[0], connection)
            with self.hearth.database.transaction(write=True) as db:
                if scope(db, route_id) != pins:
                    raise Refused("communications_route_changed")
                db.execute(
                    "UPDATE communications_cursors SET scan_before=?,updated_at=? "
                    "WHERE connection_id=? AND guild_id=? AND channel_id=?",
                    (page.scan_before, int(self.hearth.clock()), *key),
                )
                _audit(
                    db,
                    "communications.scan",
                    route_id,
                    int(self.hearth.clock()),
                    {"before": page.scan_before, "through": through},
                )
            return
        if not page.messages and not page.complete and page.examined_through is None:
            raise Refused("communications_page_incomplete")
        previous, size, verified = cursor(after), 0, []
        for m in page.messages:
            identity = cursor(m.message_id)
            if not previous < identity <= cursor(through):
                raise Refused("communications_page_order")
            if (m.bot_id, m.guild_id, m.channel_id) != (connection["bot_id"], *key[1:]):
                raise Refused("communications_source_denied")
            size += len(m.text.encode())
            if size > RESPONSE_BYTES:
                raise Refused("communications_page_too_large")
            verified.append(self.conversations._verified(route_id, route, key[2], m.message_id, m))
            previous = identity
        progress = (
            through if page.complete else (page.examined_through or page.messages[-1].message_id)
        )
        if page.examined_through is not None and (
            not cursor(after) <= cursor(page.examined_through) <= cursor(through)
            or previous > cursor(page.examined_through)
            or (page.complete and page.examined_through != through)
            or (not page.complete and cursor(page.examined_through) == cursor(after))
        ):
            raise Refused("communications_examined_invalid")
        self._check_secret(key[0], connection)
        with self.hearth.database.transaction(write=True) as db:
            if scope(db, route_id) != pins:
                raise Refused("communications_route_changed")
            for turn in verified:
                try:
                    self.conversations.submit_turn_in_transaction(db, turn)
                except Refused as error:
                    if error.code != "communications_message_conflict":
                        raise
                    # An edited identity is a decided refusal, never another task.
                    # Keep the immutable original receipt and only body-free evidence.
                    from dataclasses import asdict

                    _audit(
                        db,
                        "communications.inbound_conflict",
                        turn.message.message_id,
                        int(self.hearth.clock()),
                        {
                            "route_id": route_id,
                            "connection_id": key[0],
                            "channel_id": key[2],
                            "payload_digest": digest(asdict(turn.message)),
                        },
                    )
            db.execute(
                "UPDATE communications_cursors SET cursor=?,through_id=?,updated_at=?,"
                "scan_before=NULL "
                "WHERE connection_id=? AND guild_id=? AND channel_id=? "
                "AND cursor=? AND through_id=?",
                (
                    progress,
                    None if page.complete else through,
                    int(self.hearth.clock()),
                    *key,
                    after,
                    through,
                ),
            )
            _audit(
                db,
                "communications.progress",
                route_id,
                int(self.hearth.clock()),
                {
                    "cursor": progress,
                    "through": through,
                    "complete": page.complete,
                    "count": len(verified),
                },
            )
