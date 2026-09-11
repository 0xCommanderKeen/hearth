"""Bounded authenticated CLI client of the running communications owner."""

import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from hearth.channels.chat.model import Connection, Grant, Route
from hearth.residents.models import Refused


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def communications(args):
    token = os.environ.get("HEARTH_OPERATOR_TOKEN", "")
    if not token:
        raise Refused("communications_operator_token_required")
    origin = args.url.rstrip("/")
    try:
        parts = urlsplit(origin)
        _ = parts.port  # Validate malformed numeric ports without echoing the URL.
    except ValueError:
        raise Refused("communications_server_invalid") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path
        or any(ord(character) < 33 for character in origin)
    ):
        raise Refused("communications_server_invalid")
    if not 1 <= args.limit <= 100 or args.offset < 0:
        raise Refused("communications_page_invalid")
    path = "/api/communications"
    query, body, method = {}, None, "GET"
    if args.action in {"save", "revoke", "activate"}:
        if not args.identity or args.expected_revision is None or args.expected_revision < 0:
            raise Refused("communications_identity_and_revision_required")
        body = {"expected_revision": args.expected_revision}
        method = "POST"
        if args.action == "activate":
            if not args.old_consumer_stopped:
                raise Refused("delivery_consumer_stop_required")
            path += f"/connections/{quote(args.identity, safe='')}/activate"
            body["old_consumer_stopped"] = True
        else:
            models = {"connection": Connection, "route": Route, "grant": Grant}
            if args.kind not in models:
                raise Refused("communications_configuration_kind_required")
            path += f"/configuration/{args.kind}/{quote(args.identity, safe='')}"
            if args.action == "revoke":
                path += "/revoke"
            else:
                if args.file is None:
                    raise Refused("communications_configuration_file_required")
                try:
                    with args.file.open("rb") as source:
                        raw = source.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("configuration too large")
                    body["value"] = models[args.kind].model_validate_json(raw).model_dump()
                except OSError, ValueError, ValidationError:
                    raise Refused("communications_configuration_invalid") from None
                method = "PUT"
    elif args.action == "probe":
        if not args.identity or not args.route:
            raise Refused("communications_probe_requires_connection_and_route")
        path += f"/connections/{quote(args.identity, safe='')}/probe"
        body = {"route_id": args.route}
        method = "POST"
    elif args.action == "inspect":
        if not args.identity or args.section not in {"conversations", "deliveries"}:
            raise Refused("communications_inspect_requires_section_and_id")
        path += f"/{args.section}/{quote(args.identity, safe='')}"
        if args.section == "conversations":
            query = {"limit": min(args.limit, 50)}
            if args.before:
                query["before"] = args.before
    elif args.action == "list":
        path += "" if args.section == "configuration" else f"/{args.section}"
        query = {"limit": args.limit}
        if args.section in {"conversations", "deliveries"} and args.resident:
            query["resident_id"] = args.resident
        if args.section in {"usage", "deliveries"}:
            query["offset"] = args.offset
        elif args.after:
            query["after"] = args.after
        if args.section == "deliveries" and args.kind:
            query["kind"] = args.kind
    else:
        raise Refused("communications_action_required")
    request = Request(
        origin + path + ("?" + urlencode(query) if query else ""),
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            content = response.read(1_000_001)
            if len(content) > 1_000_000:
                raise Refused("communications_response_too_large")
            result = json.loads(content)
    except HTTPError as error:
        # Never echo server/proxy response text or the request's token/URL.
        raise Refused(
            "communications_unauthorized" if error.code == 401 else "communications_request_refused"
        ) from None
    except Refused:
        raise
    except URLError, OSError, ValueError, HTTPException:
        raise Refused("communications_server_unavailable") from None
    return result
