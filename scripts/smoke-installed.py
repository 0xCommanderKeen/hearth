"""Exercise only the installed release, with synthetic data and loopback HTTP."""

import json
import re
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import hearth
import uvicorn
from hearth.api import create_app
from hearth.backup import capture, restore
from hearth.core import Hearth
from hearth.database import Database
from hearth.memory import Memory

TOKEN = "synthetic-installed-release-token"


@contextmanager
def serve(data):
    app = create_app(data, TOKEN)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not worker.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Installed server did not start")
                time.sleep(0.02)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("Installed server did not stop")


def request(base, path, *, body=None, authenticated=True, key=None):
    headers = {"Authorization": "Bearer " + TOKEN} if authenticated else {}
    if key:
        headers["Idempotency-Key"] = key
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(base + path, data=data, headers=headers), timeout=5) as response:
        content = response.read()
        return json.loads(content) if path.startswith("/api/") else content


def main():
    assert hearth.__file__ is not None
    assert Path(hearth.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    data = Path.cwd() / "http-data"
    with serve(data) as base:
        html = request(base, "/", authenticated=False).decode()
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
        assert assets and all(request(base, asset, authenticated=False) for asset in assets)
        try:
            request(base, "/api/state", authenticated=False)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Installed API accepted an unauthenticated request")
        assert request(base, "/api/state")["residents"] == []
        request(base, "/api/demo/reader", body={})
        memory = Memory(Hearth(Database(data / "hearth.db")))
        saved = memory.save("reader", "# Synthetic release memory\n", expected_revision=0)
        body = {
            "resident_id": "reader",
            "instruction": "Synthetic release summary",
            "expires_at": int(time.time()) + 300,
        }
        receipt = request(base, "/api/tasks", body=body, key="release-summary")
        assert request(base, "/api/tasks", body=body, key="release-summary") == receipt
        run = request(base, f"/api/tasks/{receipt['task_id']}/start", body={})
        deadline = time.monotonic() + 10
        while True:
            result = request(base, f"/api/runs/{run['run_id']}")
            if result["status"] == "succeeded":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Installed mock task did not complete")
            time.sleep(0.05)
        artifact = request(base, f"/api/artifacts/{result['artifact_id']}")
        assert artifact["artifact"]["simulated"] and "simulation" in artifact["content"]
    with serve(data) as base:
        assert request(base, f"/api/commands/{receipt['command_id']}") == receipt
        assert request(base, f"/api/runs/{run['run_id']}") == result
        assert request(base, "/api/residents/reader/memory") == saved
    capture(data, Path.cwd() / "backup")
    restore(Path.cwd() / "backup", Path.cwd() / "restored")
    with serve(Path.cwd() / "restored") as base:
        assert request(base, "/api/state")["restore_hold"] is True
        assert request(base, f"/api/artifacts/{result['artifact_id']}") == artifact
        try:
            request(base, "/api/tasks", body=body, key="held-summary")
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("Restored installed application accepted new work")
    print("Installed wheel: assets, auth, Reader, retry, result, restart and held restore passed.")


if __name__ == "__main__":
    main()
