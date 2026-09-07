"""Bounded authenticated HTTP transport."""

import asyncio
import hmac
import re

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from hearth.authority.run_access import RunAccess
from hearth.residents.memory import MAX_MEMORY
from hearth.residents.models import Refused

MAX_BODY = 65_536


class OperatorAuth:
    """Authenticate before reading a bounded request body; credentials stay in headers."""

    def __init__(self, app: ASGIApp, token: str, run_access: RunAccess):
        self.app = app
        self.token = token.encode()
        self.run_access = run_access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        runtime_route = re.fullmatch(
            r"/api/runtime/runs/([a-zA-Z0-9][a-zA-Z0-9:_-]{0,127})/context", scope["path"]
        )
        if runtime_route:
            bearer = headers.get(b"authorization", b"")
            try:
                if (
                    scope["method"] != "GET"
                    or not bearer.startswith(b"Bearer ")
                    or len(bearer) > 128
                ):
                    raise Refused("runtime_unauthorized")
                scope["hearth.runtime_context"] = await asyncio.to_thread(
                    self.run_access.context, bearer[7:].decode("ascii"), runtime_route.group(1)
                )
            except Refused, UnicodeDecodeError:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        elif scope["path"].startswith("/api/runtime/"):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        elif not hmac.compare_digest(headers.get(b"authorization", b""), b"Bearer " + self.token):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        body = bytearray()
        body_limit = MAX_BODY
        if scope["method"] == "PUT" and re.fullmatch(
            r"/api/residents/[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127}/memory", scope["path"]
        ):
            body_limit = MAX_MEMORY * 6 + 1024  # JSON escaping may expand UTF-8 bytes.
        if scope["method"] in {"POST", "PUT"} and re.fullmatch(
            r"/api/skills(?:/[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127})?", scope["path"]
        ):
            body_limit = (120 + 2000 + 32000) * 6 + 1024
        if scope["method"] in {"POST", "PUT"} and re.fullmatch(
            r"/api/input-sets(?:/[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127})?", scope["path"]
        ):
            body_limit = 32768 * 6 + 1024
        if scope["method"] == "POST" and scope["path"] == "/api/residents/provision":
            body_limit = 1_500_000  # Aggregate transport cap; semantic/UTF-8 limits also apply.
        if scope["method"] == "PUT" and re.fullmatch(
            r"/api/residents/[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127}/configuration", scope["path"]
        ):
            body_limit = 1_500_000
        try:
            while True:
                message = await asyncio.wait_for(receive(), timeout=15)
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > body_limit:
                    await JSONResponse({"error": "body_too_large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break
        except TimeoutError:
            await JSONResponse({"error": "request_timeout"}, status_code=408)(scope, receive, send)
            return
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, replay, private_send)
