"""Fixed-origin REST v10 with bounded responses and no automatic send retries."""

import hashlib
import http.client
import json
import math
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from urllib.parse import urlsplit

from hearth.channels.chat.model import Destination
from hearth.channels.delivery.model import Permit, Receipt
from hearth.channels.interface import Message
from hearth.channels.polling import Page, RateLimit, RetryLater, SendResult, cursor

VIEW, SEND, HISTORY, ADMIN = 1 << 10, 1 << 11, 1 << 16, 1 << 3
ORIGIN = "https://discord.com/api/v10"
_RESOLVERS = threading.BoundedSemaphore(4)
USER_AGENT = "DiscordBot (https://github.com/0xCommanderKeen/hearth, 0.1.0)"


class Unavailable(RetryLater):
    def __init__(self, code, seconds=300, *, connection_wide=False):
        super().__init__(seconds, connection_wide=connection_wide)
        self.code = code


@dataclass(frozen=True)
class History:
    messages: tuple[Message, ...]
    complete: bool
    truncated: bool
    content_access: str
    permission: str = "allowed"
    before: str | None = None
    omitted_count: int = 0


def snowflake(value):
    number = cursor(value)
    if not 0 < number < 2**64:
        raise ValueError("invalid Discord ID")
    return value


def diagnostics(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Unavailable as error:
            self._health["state"] = error.code
            raise
        except Exception:
            self._health["state"] = "unavailable"
            raise

    return call


class Discord:
    def __init__(self, connection, secret, *, _test_origin=None, clock=time.monotonic):
        self.bot_id = snowflake(connection["bot_id"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,8192}", secret):
            raise ValueError("invalid credential")
        origin = urlsplit(_test_origin or ORIGIN)
        if _test_origin is not None and (
            origin.scheme != "http"
            or origin.hostname != "127.0.0.1"
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path != "/api/v10"
        ):
            raise ValueError("test endpoint must be loopback API v10")
        self._origin, self._secret, self._clock = origin, secret, clock
        self._delays = {}
        self._buckets = {}
        self._global = 0
        self._observed = []
        self._guilds = {}
        self._dns_pending = None
        self._dns_addresses = []
        self._dns_until = 0
        self._health = {"state": "pending", "content_access": "unknown"}

    def close(self):
        self._secret = ""
        self._delays.clear()
        self._buckets.clear()
        self._guilds.clear()
        self._observed.clear()
        self._dns_addresses.clear()
        self._dns_pending = None
        self._health = {"state": "disconnected", "content_access": "unknown"}

    def take_limits(self):
        result, self._observed = self._observed, []
        return result

    def health(self):
        return dict(self._health)

    def _resolve(self, deadline):
        if self._dns_addresses and self._dns_until > time.monotonic():
            return self._dns_addresses
        if self._dns_pending is None:
            if not _RESOLVERS.acquire(blocking=False):
                raise Unavailable("dns_busy", 5)
            result = queue.Queue(maxsize=1)
            host, port = self._origin.hostname, self._origin.port or 443

            def resolve():
                # Only DNS runs in this thread: no secret, socket connect or HTTP.
                try:
                    result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[:16])
                except OSError:
                    result.put(None)
                finally:
                    _RESOLVERS.release()

            thread = threading.Thread(target=resolve, name="hearth-discord-dns", daemon=True)
            self._dns_pending = result
            try:
                thread.start()
            except BaseException:
                self._dns_pending = None
                _RESOLVERS.release()
                raise
        try:
            addresses = self._dns_pending.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise Unavailable("dns_timeout", 5) from None
        self._dns_pending = None
        if not addresses:
            raise Unavailable("dns_unavailable", 5)
        self._dns_addresses = addresses
        self._dns_until = time.monotonic() + 60
        return addresses

    def _request(self, method, path, *, max_bytes, deadline, body=None):
        if not self._secret:
            raise Unavailable("disconnected", connection_wide=True)
        if not re.fullmatch(r"/[a-z0-9/@?=&]+", path):
            raise ValueError("invalid fixed API path")
        route = (method, path.split("?")[0])
        major = (
            (path.split("/")[1], path.split("/")[2])
            if path.startswith(("/channels/", "/guilds/"))
            else ("account", "account")
        )
        bucket = self._buckets.get(route, route)
        now = self._clock()
        delay = max(self._global, self._delays.get((major, bucket), 0)) - now
        if delay > 0:
            raise Unavailable("rate_limited", math.ceil(delay), connection_wide=self._global > now)
        addresses = self._resolve(deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Unavailable("deadline", 5)
        cls = (
            http.client.HTTPSConnection
            if self._origin.scheme == "https"
            else http.client.HTTPConnection
        )
        assert self._origin.hostname is not None
        conn = cls(self._origin.hostname, self._origin.port, timeout=remaining)

        def connect(address, timeout=None, source_address=None):
            # Keep HTTPSConnection's original hostname for TLS SNI/certificate checks;
            # replace only DNS/connect, with numeric addresses already resolved above.
            for family, socktype, proto, _, sockaddr in addresses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                sock = socket.socket(family, socktype, proto)
                try:
                    sock.settimeout(remaining)
                    sock.connect(sockaddr)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    sock.settimeout(remaining)  # TLS handshake gets only the remaining budget.
                    return sock
                except OSError:
                    sock.close()
            raise OSError("Discord connect failed")

        # CPython's socket factory hook is intentionally absent from typeshed.
        conn._create_connection = connect  # ty: ignore[unresolved-attribute]
        request_socket = [None]

        def interrupt():
            sock = request_socket[0] or conn.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = threading.Timer(remaining, interrupt)
        timer.daemon = True
        timer.start()
        try:
            conn.request(
                method,
                self._origin.path + path,
                body=None if body is None else json.dumps(body).encode(),
                headers={
                    "Authorization": "Bot " + self._secret,
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            request_socket[0] = conn.sock
            response = conn.getresponse()
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, max_bytes + 1 - len(raw)))
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise ValueError("response too large")
                if not chunk:
                    break
            try:
                value = json.loads(raw)
            except ValueError, RecursionError:
                value = None
            bucket = response.getheader("X-RateLimit-Bucket") or bucket
            self._buckets[route] = bucket
            delay = 0
            for item in (
                response.getheader("Retry-After"),
                response.getheader("X-RateLimit-Reset-After")
                if response.getheader("X-RateLimit-Remaining") == "0"
                else None,
                value.get("retry_after")
                if isinstance(value, dict) and response.status == 429
                else None,
            ):
                if item is not None:
                    seconds = float(item)
                    if not math.isfinite(seconds) or seconds < 0:
                        raise ValueError("invalid rate delay")
                    delay = max(delay, math.ceil(seconds))
            global_limit = response.getheader("X-RateLimit-Global") == "true" or (
                isinstance(value, dict) and value.get("global") is True
            )
            if delay:
                channel = major[1] if major[0] == "channels" and not global_limit else None
                guild = (
                    major[1]
                    if major[0] == "guilds" and not global_limit
                    else self._guilds.get(channel)
                )
                self._observed.append(RateLimit(delay, guild, channel))
                self._delays[major, bucket] = self._clock() + delay
                if global_limit:
                    self._global = self._clock() + delay
            if response.status == 429:
                raise Unavailable("rate_limited", max(delay, 1), connection_wide=global_limit)
            if response.status == 401:
                self._health["state"] = "authentication_failed"
                raise Unavailable("authentication_failed", connection_wide=True)
            if response.status in (403, 404):
                self._health["state"] = "permission_denied"
                raise Unavailable("permission_denied")
            if response.status == 400:
                raise Unavailable("request_refused")
            if response.status != 200 or value is None:
                raise ValueError("invalid Discord response")
            return value
        finally:
            timer.cancel()
            timer.join()
            conn.close()

    def _limits(self, max_bytes, timeout):
        if type(max_bytes) is not int or not 0 < max_bytes <= 512 * 1024 or not 0 < timeout <= 10:
            raise ValueError("invalid request bounds")
        return {"max_bytes": max_bytes, "deadline": time.monotonic() + timeout}

    def _access(self, guild, channel, required, limits):
        snowflake(guild)
        snowflake(channel)
        user = self._request("GET", "/users/@me", **limits)
        if user.get("id") != self.bot_id or user.get("bot") is not True:
            raise Unavailable("bot_identity_mismatch", connection_wide=True)
        ch = self._request("GET", f"/channels/{channel}", **limits)
        if (
            ch.get("id") != channel
            or ch.get("guild_id") != guild
            or type(ch.get("type")) is not int
            or ch.get("type") not in (0, 5)
        ):
            raise Unavailable("channel_identity_mismatch")
        self._guilds[channel] = guild
        g = self._request("GET", f"/guilds/{guild}", **limits)
        member = self._request("GET", f"/guilds/{guild}/members/{self.bot_id}", **limits)
        if g.get("id") != guild or member["user"]["id"] != self.bot_id:
            raise Unavailable("guild_identity_mismatch")
        roles = set(member["roles"])
        permissions = 0
        found = set()
        for role in g["roles"]:
            if role["id"] in roles | {guild}:
                permissions |= cursor(role["permissions"])
                found.add(role["id"])
        if found != roles | {guild}:
            raise ValueError("missing role")
        if permissions & ADMIN or g.get("owner_id") == self.bot_id:
            permissions |= VIEW | SEND | HISTORY
        else:
            overwrites = ch["permission_overwrites"]
            for selected in (
                [o for o in overwrites if o["id"] == guild and o["type"] == 0],
                [o for o in overwrites if o["id"] in roles and o["type"] == 0],
                [o for o in overwrites if o["id"] == self.bot_id and o["type"] == 1],
            ):
                deny = allow = 0
                for item in selected:
                    deny |= cursor(item["deny"])
                    allow |= cursor(item["allow"])
                permissions = (permissions & ~deny) | allow
        if member.get("communication_disabled_until") is not None:
            until = datetime.fromisoformat(member["communication_disabled_until"])
            if until.timestamp() > time.time():
                permissions &= VIEW | HISTORY
        if permissions & required != required:
            self._health["state"] = "permission_denied"
            raise Unavailable("permission_denied")
        self._health["state"] = "ready"

    def _messages(self, guild, channel, query, limits):
        values = self._request("GET", f"/channels/{channel}/messages?{query}", **limits)
        if type(values) is not list or len(values) > 50:
            raise ValueError("invalid history page")
        result = []
        for v in values:
            if v["channel_id"] != channel or v.get("guild_id", guild) != guild:
                raise ValueError("message source mismatch")
            if any(type(v[k]) is not bool for k in ("tts", "mention_everyone", "pinned") if k in v):
                raise ValueError("invalid message flags")
            author = v["author"]
            if (
                any(type(author[k]) is not bool for k in ("bot", "system") if k in author)
                or type(v["type"]) is not int
            ):
                raise ValueError("invalid author facts")
            text = v["content"]
            if type(text) is not str or len(text.encode()) > 32768:
                raise ValueError("invalid message content")
            timestamp = datetime.fromisoformat(v["timestamp"])
            if timestamp.tzinfo is None:
                raise ValueError("timestamp lacks zone")
            result.append(
                Message(
                    self.bot_id,
                    guild,
                    channel,
                    snowflake(v["id"]),
                    snowflake(author["id"]),
                    int(timestamp.timestamp()),
                    text,
                    any(snowflake(m["id"]) == self.bot_id for m in v["mentions"]),
                    not author.get("bot", False) and not author.get("system", False),
                    "webhook_id" in v,
                    v["type"] in (0, 19),
                )
            )
        ids = [int(m.message_id) for m in result]
        if ids != sorted(set(ids), reverse=True):
            raise ValueError("unordered history response")
        return result

    @diagnostics
    def latest(self, guild_id, channel_id, *, max_bytes, timeout):
        limits = self._limits(max_bytes, timeout)
        self._access(guild_id, channel_id, VIEW | HISTORY, limits)
        rows = self._messages(guild_id, channel_id, "limit=1", limits)
        return rows[0].message_id if rows else "0"

    @diagnostics
    def poll(
        self, guild_id, channel_id, *, after, through, limit, max_bytes, timeout, scan_before=None
    ):
        limits = self._limits(max_bytes, timeout)
        if not 1 <= limit <= 50 or cursor(after) > cursor(through):
            raise ValueError("invalid page interval")
        self._access(guild_id, channel_id, VIEW | HISTORY, limits)
        before = cursor(scan_before) if scan_before is not None else cursor(through) + 1
        rows = self._messages(
            guild_id, channel_id, f"before={before}&limit={max(2, limit)}", limits
        )
        if any(int(m.message_id) >= before for m in rows):
            raise ValueError("page outside requested interval")
        # Discord documents newest-first selection. Scan backwards without committing
        # bodies or decisions; an overlapping boundary survives deletion and restart.
        if len(rows) == max(2, limit) and int(rows[-1].message_id) > cursor(after):
            frontier = str(int(rows[-1].message_id) + 1)
            if int(frontier) < before:
                return Page((), False, scan_before=frontier)
        selected = tuple(reversed([m for m in rows if int(m.message_id) > cursor(after)]))
        if len(selected) > limit:
            selected = selected[:limit]
            examined = selected[-1].message_id
        else:
            examined = str(before - 1)
        return Page(selected, examined == through, examined_through=examined)

    @diagnostics
    def probe(self, guild_id, channel_id, *, read, send, max_bytes, timeout):
        limits = self._limits(max_bytes, timeout)
        self._access(
            guild_id, channel_id, VIEW | (HISTORY if read else 0) | (SEND if send else 0), limits
        )
        if read:
            app = self._request("GET", "/applications/@me", **limits)
            flags = app.get("flags")
            self._health["content_access"] = (
                "unknown"
                if type(flags) is not int
                else "available"
                if flags & ((1 << 18) | (1 << 19))
                else "unavailable"
            )
        return self.health()

    @diagnostics
    def history(
        self, guild_id, channel_id, *, before=None, limit=50, max_bytes=512 * 1024, timeout=10
    ):
        limits = self._limits(max_bytes, timeout)
        if not 1 <= limit <= 50:
            raise ValueError("invalid history limit")
        self._access(guild_id, channel_id, VIEW | HISTORY, limits)
        app = self._request("GET", "/applications/@me", **limits)
        flags = app.get("flags")
        content = (
            "unknown"
            if type(flags) is not int
            else ("available" if flags & ((1 << 18) | (1 << 19)) else "unavailable")
        )
        self._health["content_access"] = content
        query = f"limit={limit}" + (f"&before={snowflake(before)}" if before else "")
        rows = self._messages(guild_id, channel_id, query, limits)
        kept, size = [], 0
        for m in rows:
            size += len(json.dumps(m.__dict__, ensure_ascii=False).encode())
            if size > 32768:
                break
            kept.append(m)
        truncated = len(kept) < len(rows) or len(rows) == limit
        return History(
            tuple(kept),
            not truncated and content == "available",
            truncated,
            content,
            before=kept[-1].message_id if kept else (rows[0].message_id if rows else None),
            omitted_count=1 if rows and not kept else 0,
        )

    def send(self, permit: Permit, *, max_bytes, timeout):
        def receipt(outcome, evidence, **extra):
            return Receipt(
                attempt_id=permit.attempt_id,
                intent_sha256=permit.intent_sha256,
                outcome=outcome,
                evidence=evidence,
                **extra,
            )

        limits = self._limits(max_bytes, timeout)
        intent = permit.intent
        dest = intent.destination
        if (
            intent.transport != "discord"
            or intent.bot_id != self.bot_id
            or not isinstance(dest, Destination)
        ):
            return receipt("refused", "destination_invalid")
        try:
            self._access(
                dest.guild_id,
                dest.channel_id,
                VIEW | SEND | (HISTORY if intent.kind == "reply" else 0),
                limits,
            )
        except Unavailable as error:
            self._health["state"] = error.code
            return SendResult(
                receipt(
                    "safe_failure"
                    if error.code
                    in ("rate_limited", "deadline", "dns_busy", "dns_timeout", "dns_unavailable")
                    else "refused",
                    error.code,
                    retry_after=error.seconds,
                ),
                error.connection_wide,
            )
        except Exception:
            return receipt("safe_failure", "preflight_unavailable", retry_after=5)
        payload: dict = {
            "content": intent.text,
            "allowed_mentions": {"parse": [], "replied_user": False},
            "flags": 4,
            "nonce": hashlib.sha256(permit.operation_id.encode()).hexdigest()[:25],
            "enforce_nonce": True,
        }
        if intent.kind == "reply":
            if permit.reply_message_id is None:
                return receipt("refused", "reply_source_missing")
            payload["message_reference"] = {
                "type": 0,
                "message_id": snowflake(permit.reply_message_id),
                "channel_id": dest.channel_id,
                "guild_id": dest.guild_id,
                "fail_if_not_exists": True,
            }
        try:
            value = self._request(
                "POST", f"/channels/{dest.channel_id}/messages", body=payload, **limits
            )
            if value["channel_id"] != dest.channel_id or value["author"]["id"] != self.bot_id:
                raise ValueError("send identity mismatch")
            if value.get("nonce") != payload["nonce"] or value.get("content") != intent.text:
                raise ValueError("send payload mismatch")
            if intent.kind == "reply" and any(
                value.get("message_reference", {}).get(k, 0 if k == "type" else None) != v
                for k, v in payload["message_reference"].items()
                if k != "fail_if_not_exists"
            ):
                raise ValueError("reply reference mismatch")
            return receipt(
                "confirmed", "discord_message_created", external_id=snowflake(value["id"])
            )
        except Unavailable as error:
            self._health["state"] = error.code
            return SendResult(
                receipt(
                    "safe_failure"
                    if error.code
                    in ("rate_limited", "deadline", "dns_busy", "dns_timeout", "dns_unavailable")
                    else "refused",
                    error.code,
                    retry_after=error.seconds,
                ),
                error.connection_wide,
            )
        except Exception:
            return receipt("unknown", "discord_send_uncertain")
