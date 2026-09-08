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
from hearth.app import create_app
from hearth.inputs.catalog import Inputs
from hearth.inputs.selection import save_selection
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.service import Hearth

# The release ships one runtime and no Codex subscription exists here, so the smoke
# run injects the same fake the test suite uses. It is imported from the checkout,
# never from the wheel: `tests/` is not packaged, and this proves it need not be.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fake_runtime import fake_runtime  # noqa: E402

TOKEN = "synthetic-installed-release-token"


def seed_reader(hearth_service) -> None:
    """Create the synthetic Reader used by this smoke run; the release seeds nothing."""
    with hearth_service.database.transaction(write=True) as db:
        Inputs(hearth_service).save_in_transaction(
            db,
            "seed-synthetic-reader-notes",
            input_set_id="synthetic-reader-notes",
            name="Synthetic Reader example notes",
            notes=["Synthetic note: exercise the installed release."],
            actor="operator",
        )
        hearth_service.save_resident_in_transaction(
            db,
            "reader",
            Declaration(
                "Reader",
                "A daily summary of synthetic notes. Read-only; no external actions.",
                10_000_000,
                budget_timezone="Europe/Ljubljana",
            ),
            expected_revision=0,
        )
        save_selection(
            db,
            "reader",
            [{"input_set_id": "synthetic-reader-notes"}],
            expected_revision=0,
            command_id="seed-reader-inputs",
            actor="operator",
            now=int(hearth_service.clock()),
        )


@contextmanager
def serve(data):
    app = create_app(data, TOKEN, runtime=fake_runtime())
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


def check_application():
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
        seed_reader(Hearth(Database(data / "hearth.db")))
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
                raise RuntimeError("Installed task did not complete")
            time.sleep(0.05)
        artifact = request(base, f"/api/artifacts/{result['artifact_id']}")
        assert not artifact["artifact"]["simulated"]
        assert "Daily summary" in artifact["content"]
        assert result["runtime_kind"] == "codex_subscription" and result["runtime_version"] == 1
        assert len(result["input_digest"]) == 64
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
    check_application()
