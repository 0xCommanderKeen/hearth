"""Application runtime for the actual Codex CLI with a local synthetic model service."""

import fcntl
import hashlib
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from hearth import codex_accounting, codex_assets
from hearth.artifacts import Artifacts, sync_directory
from hearth.codex_container import CodexContainer
from hearth.codex_usage import UsageBinding, UsageJournal, publish, read
from hearth.container_rehearsal import container_lock
from hearth.core import Hearth, _audit
from hearth.database import Database
from hearth.execution import Execution
from hearth.models import Refused, identifier
from hearth.runtime import Evidence


class CodexMockRuntime:
    kind = "codex_mock"
    version = 1

    def __init__(self, data: Path, *, archive: Path | None = None, scenario="success", docker=None):
        if scenario not in {"success", "hold"}:
            raise Refused("codex_mock_scenario_invalid")
        self.data = data.resolve()
        self.root = self.data / "codex-runs"
        self.assets = self.data / "codex-assets"
        self.database = Database(self.data / "hearth.db")
        self.scenario = scenario
        self.docker = docker
        if not self.database.restored():
            pin = codex_assets.prepare(self.assets, archive)
            with self.database.transaction(write=True) as db:
                old = db.execute(
                    "SELECT value FROM system_meta WHERE key='codex_assets'"
                ).fetchone()
                if old is None:
                    db.execute("INSERT INTO system_meta VALUES ('codex_assets',?)", (pin,))
                    _audit(
                        db,
                        "runtime.codex_mock_configured",
                        "codex_mock",
                        int(time.time()),
                        {"assets": pin, "simulated": True},
                    )
                elif old[0] != pin:
                    raise Refused("codex_mock_assets_changed")
            self.root.mkdir(mode=0o700, exist_ok=True)

    def folder(self, run_id):
        identifier(run_id)
        return self.root / run_id

    def _request(self, run_id):
        value = read(self.folder(run_id) / "request.json")
        with self.database.transaction() as db:
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
            assets = db.execute(
                "SELECT value FROM system_meta WHERE key='codex_assets'"
            ).fetchone()[0]
            if (
                run is None
                or run["runtime_kind"] != self.kind
                or value["binding"] != asdict(codex_accounting.binding(db, run))
                or value["owner"] != run["owner_token"]
                or value["epoch"] != epoch
                or value["assets"] != assets
            ):
                raise Refused("codex_mock_request_conflict")
        return value

    def start(self, run_id, instruction):
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        with container_lock(self.root / ".launch.lock"):
            folder = self.folder(run_id)
            if folder.exists():
                request = self._request(run_id)
                if (
                    request["binding"]["input_digest"]
                    != hashlib.sha256(instruction.encode()).hexdigest()
                ):
                    raise Refused("runtime_identity_conflict")
                return
            with self.database.transaction() as db:
                run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                binding = codex_accounting.binding(db, run)
                if (
                    binding.input_digest != hashlib.sha256(instruction.encode()).hexdigest()
                    or not run["launch_attempted"]
                ):
                    raise Refused("runtime_identity_conflict")
                request = {
                    "binding": asdict(binding),
                    "owner": run["owner_token"],
                    "epoch": db.execute(
                        "SELECT value FROM system_meta WHERE key='epoch'"
                    ).fetchone()[0],
                    "assets": db.execute(
                        "SELECT value FROM system_meta WHERE key='codex_assets'"
                    ).fetchone()[0],
                    "scenario": self.scenario,
                    "deadline": time.time() + 55,
                    "prompt": instruction,
                }
            folder.mkdir(mode=0o700)
            sync_directory(self.root)
            publish(folder / "request.json", request)
            # No launch retries after this durable request, even if thread creation fails.
            threading.Thread(target=self._worker, args=(run_id,), daemon=True).start()

    def stop(self, run_id):
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        if folder.exists():
            with container_lock(folder / ".cancel.lock"):
                if not (folder / "cancel.json").exists():
                    publish(folder / "cancel.json", {"cancelled": True})

    def _cancelled(self, run_id):
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT cancellation_requested FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            return bool(row[0]) or (self.folder(run_id) / "cancel.json").exists()

    def _container(self, run_id, role):
        return CodexContainer(self.folder(run_id) / role, docker=self.docker)

    def _worker(self, run_id):
        folder = self.folder(run_id)
        try:
            with container_lock(folder / "worker.lock"):
                publish(folder / "worker-started.json", {"started": True})
                request = self._request(run_id)
                binding = UsageBinding(**request["binding"])
                (folder / "journal").mkdir(mode=0o700)
                (folder / "secret").write_text("synthetic-upstream-demo")
                (folder / "secret").chmod(0o400)
                command = [
                    "--run-id",
                    run_id,
                    "--prompt",
                    request["prompt"],
                    "--expires",
                    str(int(time.time()) + 3600),
                ]
                if request["scenario"] == "hold":
                    command.append("--interrupt")
                execution = Execution(Hearth(self.database), Artifacts(self.data / "artifacts"))
                for role in ("collector", "cli"):
                    if self._cancelled(run_id):
                        break
                    mounts = [(self.assets / "fixture.py", "/probe.py", False)]
                    network = "none"
                    if role == "collector":
                        mounts += [
                            (self.assets / "app", "/app", False),
                            (folder / "journal", "/journal", True),
                            (folder / "secret", "/collector-secret", False),
                        ]
                    else:
                        mounts += [(self.assets / "runtime", "/runtime", False)]
                        network = "container:" + self._container(run_id, "collector").container_id
                    container = CodexContainer.create(
                        folder / role,
                        binding,
                        role=role,
                        name="hearth-codex-" + run_id + "-" + role,
                        mounts=mounts,
                        command=command + (["--collector"] if role == "collector" else []),
                        network=network,
                        docker=self.docker,
                    )
                    with execution.dispatch_guard(
                        run_id,
                        request["owner"],
                        epoch=request["epoch"],
                        input_digest=binding.input_digest,
                    ):
                        container.start()
                    if role == "collector":
                        deadline = time.monotonic() + 10
                        while not (folder / "journal/ready.json").exists():
                            if time.monotonic() >= deadline or self._cancelled(run_id):
                                break
                            time.sleep(0.05)
                while self._reconcile(run_id).status == "running":
                    time.sleep(0.1)
        except Exception:
            # Durable identities remain for inspect-only recovery; no worker relaunch.
            return

    def inspect(self, run_id, *, expected_digest=None):
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        try:
            request = self._request(run_id)
            if (
                expected_digest is not None
                and request["binding"]["input_digest"] != expected_digest
            ):
                return Evidence("unknown")
            with (folder / "worker.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return Evidence("running")
                return self._reconcile(run_id)
        except OSError, ValueError, KeyError, TypeError, Refused:
            return Evidence("unknown")

    def receipt(self, run_id):
        return read(self.folder(run_id) / "settlement.json")

    def _reconcile(self, run_id):
        folder = self.folder(run_id)
        request = self._request(run_id)
        binding = UsageBinding(**request["binding"])
        if (folder / "settlement.json").exists():
            value = self.receipt(run_id)
            evidence = codex_accounting.encode_receipt(value, binding)[2]
            self._cleanup(run_id, value["containers"])
            return evidence
        cancelled = self._cancelled(run_id)
        expired = time.time() >= request["deadline"]
        containers = {}
        for role in ("cli", "collector"):
            container = self._container(run_id, role)
            if not (container.root / "claim.json").exists():
                containers[role] = None
                continue
            terminal = container.inspect()
            if role == "cli" and terminal["status"] == "running" and not cancelled and not expired:
                return Evidence("running")
            if terminal["status"] == "running":
                terminal = container.stop()
            containers[role] = container.export()
        # Without a complete CLI launch, only cancellation/deadline can close the attempt.
        if containers["cli"] is None and not cancelled and not expired:
            return Evidence("unknown")
        result = None
        if containers["cli"] is not None:
            cli = containers["cli"]["terminal"]
            if cli["status"] == "exited" and cli["exit_code"] == 0:
                try:
                    result = json.loads(cli["logs"])
                except ValueError:
                    pass
        value = {
            "binding": asdict(binding),
            "assets": request["assets"],
            "containers": containers,
            "requests": [],
            "terminal": None,
            "stopped": None,
            "journal": {},
        }
        journal_root = folder / "journal/usage"
        if journal_root.exists():
            value["journal"] = {path.name: read(path) for path in journal_root.glob("*.json")}
        if result is not None and result.get("returncode") == 0 and not result.get("timeout"):
            journal = UsageJournal(journal_root, binding)
            journal.seal(result["stdout"], exit_code=result["returncode"], final=result["final"])
            with journal.snapshot() as snapshot:
                value.update(snapshot)
            report = read(folder / "journal/collector.json")
            if report["errors"] or not report["stopped_by_host"]:
                raise Refused("codex_collector_handoff_invalid")
        else:
            value["stopped"] = "cancelled" if cancelled else "failed"
        evidence = codex_accounting.encode_receipt(value, binding)[2]
        publish(folder / "settlement.json", value)
        self._cleanup(run_id, containers)
        return evidence

    def _cleanup(self, run_id, containers):
        for role, item in containers.items():
            if item is not None:
                self._container(run_id, role).remove()
