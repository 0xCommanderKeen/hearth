"""Durable ownership for the isolated offline Codex CLI and collector containers."""

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from hearth.integrations.codex.pricing import MODEL, PRICE_SCHEDULE
from hearth.integrations.codex.usage import UsageBinding, publish, read
from hearth.integrations.mock.container import IMAGE, LocalDocker, container_lock
from hearth.residents.models import Refused
from hearth.storage.artifacts import sync_directory

LABEL = "org.hearth.codex-container"


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CodexContainer:
    """Reopening can observe or stop; only the original creator can attempt start.

    Root and mounted sources belong to the trusted worker. This module grants no
    database execution authority; the worker must hold its run dispatch guard when
    calling start. The configuration permits only the offline split fixture.
    """

    def __init__(self, root: Path, *, docker=None):
        self.root = root
        self.docker = docker if docker is not None else LocalDocker()
        self._may_start = False

    @classmethod
    def create(
        cls,
        root: Path,
        binding: UsageBinding,
        *,
        role: str,
        name: str,
        mounts: list[tuple[Path, str, bool]],
        command: list[str],
        network: str = "none",
        docker=None,
    ):
        root.mkdir(mode=0o700)
        sync_directory(root.parent)
        value = cls(root, docker=docker)
        claim = {
            "version": 1,
            "binding": asdict(binding),
            "role": role,
            "name": name,
            "image": IMAGE,
            "user": f"{os.getuid()}:{os.getgid()}",
            "network": network,
            "mounts": [
                [str(source.resolve()), target, writable] for source, target, writable in mounts
            ],
            "command": command,
        }
        value._validate_claim(claim)
        # Exclusive publication and parent sync precede even Docker create.
        publish(root / "claim.json", claim)
        with container_lock(root / ".lock"):
            cid = value.docker(*value._create_args(claim)).strip()
            if not re.fullmatch("[0-9a-f]{64}", cid):
                raise Refused("codex_container_identity_invalid")
            state = value._live(claim, expected_id=cid)
            value._record("identity.json", {"binding": digest(claim), "id": state["Id"]})
            value._may_start = True
        return value

    @staticmethod
    def _validate_claim(claim):
        try:
            if (
                set(claim)
                != {
                    "version",
                    "binding",
                    "role",
                    "name",
                    "image",
                    "user",
                    "network",
                    "mounts",
                    "command",
                }
                or type(claim["version"]) is not int
                or claim["version"] != 1
                or claim["image"] != IMAGE
                or claim["role"] not in {"collector", "cli"}
                or not re.fullmatch(r"hearth-codex-[a-z0-9-]{1,100}", claim["name"])
                or not re.fullmatch(r"[1-9][0-9]*:[0-9]+", claim["user"])
                or not isinstance(claim["command"], list)
                or not all(
                    isinstance(item, str) and len(item) <= 512_000 for item in claim["command"]
                )
                or len(claim["command"]) > 32
            ):
                raise ValueError()
            binding = UsageBinding(**claim["binding"])
            if not binding.run_id or not re.fullmatch("[0-9a-f]{64}", binding.input_digest):
                raise ValueError()
            command = claim["command"]
            if (
                binding.model != MODEL
                or binding.mode != "standard"
                or binding.schedule != PRICE_SCHEDULE
                or len(command) < 6
                or command[:3] != ["--run-id", binding.run_id, "--prompt"]
                or hashlib.sha256(command[3].encode()).hexdigest() != binding.input_digest
                or command[4] != "--expires"
                or not command[5].isdigit()
                or not 0 < int(command[5]) < 2**63
                or len(set(command[6:])) != len(command[6:])
                or not set(command[6:]) <= {"--attack", "--interrupt", "--collector"}
                or ("--collector" in command) != (claim["role"] == "collector")
            ):
                raise ValueError()
            expected = (
                {"/probe.py": False, "/app": False, "/journal": True, "/collector-secret": False}
                if claim["role"] == "collector"
                else {"/runtime": False, "/probe.py": False}
            )
            if len(claim["mounts"]) != len(expected):
                raise ValueError()
            for source, target, writable in claim["mounts"]:
                if (
                    not Path(source).is_absolute()
                    or type(writable) is not bool
                    or expected.pop(target) != writable
                ):
                    raise ValueError()
            if (claim["role"] == "collector" and claim["network"] != "none") or (
                claim["role"] == "cli"
                and not re.fullmatch("container:[0-9a-f]{64}", claim["network"])
            ):
                raise ValueError()
        except ValueError, TypeError, KeyError, AttributeError:
            raise Refused("codex_container_claim_invalid") from None

    def _claim(self):
        if (self.root / "conflict.json").exists():
            raise Refused("codex_container_conflict")
        value = read(self.root / "claim.json")
        self._validate_claim(value)
        return value

    def _record(self, filename, value):
        path = self.root / filename
        try:
            publish(path, value)
        except FileExistsError:
            if digest(read(path)) != digest(value):
                try:
                    publish(self.root / "conflict.json", {"file": filename})
                except FileExistsError:
                    read(self.root / "conflict.json")
                raise Refused("codex_container_conflict") from None

    def _create_args(self, claim):
        return [
            "create",
            "--pull",
            "never",
            "--name",
            claim["name"],
            "--label",
            LABEL + "=" + digest(claim),
            "--restart",
            "no",
            "--network",
            claim["network"],
            "--read-only",
            "--user",
            claim["user"],
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            "seccomp=builtin",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/scratch:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            *[
                part
                for source, target, writable in claim["mounts"]
                for part in (
                    "--mount",
                    f"type=bind,source={source},target={target}"
                    + ("" if writable else ",readonly"),
                )
            ],
            IMAGE,
            "python3",
            "-I",
            "/probe.py",
            *claim["command"],
        ]

    def _live(self, claim, *, expected_id=None):
        if expected_id is None and (self.root / "identity.json").exists():
            identity = read(self.root / "identity.json")
            if (
                set(identity) != {"binding", "id"}
                or identity["binding"] != digest(claim)
                or not re.fullmatch("[0-9a-f]{64}", identity["id"])
            ):
                raise Refused("codex_container_identity_invalid")
            expected_id = identity["id"]
        state = json.loads(self.docker("inspect", expected_id or claim["name"]))[0]
        host = state["HostConfig"]
        if (
            not re.fullmatch("[0-9a-f]{64}", state["Id"])
            or (expected_id is not None and state["Id"] != expected_id)
            or state["Name"] != "/" + claim["name"]
            or state["Config"]["Labels"].get(LABEL) != digest(claim)
            or state["Config"]["Image"] != IMAGE
            or state["Config"]["User"] != claim["user"]
            or state["Config"]["Cmd"] != ["python3", "-I", "/probe.py", *claim["command"]]
            or host["NetworkMode"] != claim["network"]
            or host["ReadonlyRootfs"] is not True
            or host["PidMode"] != ""
            or host["Init"] is not True
            or host["CapDrop"] != ["ALL"]
            or set(host["SecurityOpt"]) != {"no-new-privileges=true", "seccomp=builtin"}
            or host["Memory"] != 512 * 1024**2
            or host["MemorySwap"] != 512 * 1024**2
            or host["NanoCpus"] != 500_000_000
            or host["PidsLimit"] != 128
            or host["RestartPolicy"]["Name"] != "no"
            or {(m["Source"], m["Destination"], m["RW"]) for m in state["Mounts"]}
            != {tuple(m) for m in claim["mounts"]}
        ):
            raise Refused("codex_container_identity_invalid")
        self._record("identity.json", {"binding": digest(claim), "id": state["Id"]})
        return state

    @property
    def container_id(self):
        claim = self._claim()
        identity = read(self.root / "identity.json")
        if identity["binding"] != digest(claim) or not re.fullmatch("[0-9a-f]{64}", identity["id"]):
            raise Refused("codex_container_identity_invalid")
        return identity["id"]

    def start(self):
        with container_lock(self.root / ".lock"):
            claim = self._claim()
            if (
                not self._may_start
                or (self.root / "start.json").exists()
                or (self.root / "terminal.json").exists()
            ):
                raise Refused("codex_container_start_already_claimed")
            self._may_start = False
            state = self._live(claim)
            if state["State"]["Status"] != "created":
                raise Refused("codex_container_start_state_invalid")
            self._record("start.json", {"binding": digest(claim), "id": state["Id"]})
            self.docker("start", state["Id"])

    def _verify_start(self, claim, container_id):
        if read(self.root / "start.json") != {"binding": digest(claim), "id": container_id}:
            raise Refused("codex_container_start_invalid")

    @staticmethod
    def _execution(state):
        current = state["State"]
        if (
            type(current["Running"]) is not bool
            or type(current["Pid"]) is not int
            or current["Pid"] < 0
            or (not current["Running"] and current["Pid"] != 0)
            or type(current["ExitCode"]) is not int
            or not 0 <= current["ExitCode"] <= 255
            or type(state["RestartCount"]) is not int
            or state["RestartCount"] < 0
            or any(
                not isinstance(current[key], str) or len(current[key]) > 64
                for key in ("Status", "StartedAt", "FinishedAt")
            )
        ):
            raise Refused("codex_container_execution_invalid")
        return {
            "state": {
                key: current[key]
                for key in ("Status", "Running", "ExitCode", "StartedAt", "FinishedAt", "Pid")
            },
            "restarts": state["RestartCount"],
        }

    def _terminal(self, claim):
        return self.validate_export(self._export(claim))

    def _export(self, claim):
        return {
            "claim": claim,
            "identity": read(self.root / "identity.json"),
            "start": read(self.root / "start.json")
            if (self.root / "start.json").exists()
            else None,
            "terminal": read(self.root / "terminal.json"),
        }

    def export(self):
        with container_lock(self.root / ".lock"):
            value = self._export(self._claim())
            self.validate_export(value)
            return value

    @classmethod
    def validate_export(cls, value):
        if set(value) != {"claim", "identity", "start", "terminal"}:
            raise Refused("codex_container_terminal_invalid")
        claim, identity, receipt = value["claim"], value["identity"], value["terminal"]
        cls._validate_claim(claim)
        execution = cls._execution(
            {
                "State": receipt["execution"]["state"],
                "RestartCount": receipt["execution"]["restarts"],
            }
        )
        if digest(execution) != digest(receipt["execution"]):
            raise Refused("codex_container_execution_invalid")
        if (
            set(receipt)
            != {"binding", "id", "status", "exit_code", "logs", "logs_sha256", "execution"}
            or identity != {"binding": digest(claim), "id": receipt["id"]}
            or receipt["binding"] != digest(claim)
            or not re.fullmatch("[0-9a-f]{64}", receipt["id"])
            or receipt["status"] not in {"exited", "dead", "created"}
            or receipt["execution"]["state"]["Status"] != receipt["status"]
            or receipt["execution"]["state"]["ExitCode"] != receipt["exit_code"]
            or receipt["execution"]["state"]["Running"] is not False
            or type(receipt["exit_code"]) is not int
            or not 0 <= receipt["exit_code"] <= 255
            or not isinstance(receipt["logs"], str)
            or hashlib.sha256(receipt["logs"].encode()).hexdigest() != receipt["logs_sha256"]
        ):
            raise Refused("codex_container_terminal_invalid")
        if receipt["status"] == "created" and value["start"] is not None:
            raise Refused("codex_container_start_outcome_unknown")
        if receipt["status"] != "created":
            if value["start"] != {"binding": digest(claim), "id": receipt["id"]}:
                raise Refused("codex_container_start_invalid")
        return receipt

    def inspect(self):
        with container_lock(self.root / ".lock"):
            claim = self._claim()
            if (self.root / "terminal.json").exists():
                return self._terminal(claim)
            state = self._live(claim)
            return self._capture(claim, state)

    def _capture(self, claim, state):
        if state["State"]["Status"] == "created" and (self.root / "start.json").exists():
            raise Refused("codex_container_start_outcome_unknown")
        if state["State"]["Status"] != "created":
            self._verify_start(claim, state["Id"])
        if state["State"]["Running"]:
            return {"status": "running", "id": state["Id"]}
        if state["State"]["Status"] not in {"exited", "dead", "created"}:
            raise Refused("codex_container_state_unknown")
        receipt = {
            "binding": digest(claim),
            "id": state["Id"],
            "status": state["State"]["Status"],
            "exit_code": state["State"]["ExitCode"],
            "logs": self.docker("logs", state["Id"]),
            "execution": self._execution(state),
        }
        receipt["logs_sha256"] = hashlib.sha256(receipt["logs"].encode()).hexdigest()
        if self._execution(self._live(claim)) != receipt["execution"]:
            self._record("conflict.json", {"file": "terminal.json"})
            raise Refused("codex_container_terminal_changed")
        self._record("terminal.json", receipt)
        return self._terminal(claim)

    def stop(self):
        with container_lock(self.root / ".lock"):
            claim = self._claim()
            if (self.root / "terminal.json").exists():
                return self._terminal(claim)
            state = self._live(claim)
            if state["State"]["Running"]:
                self._verify_start(claim, state["Id"])
                self.docker("stop", "--time", "12", state["Id"])
                state = self._live(claim)
            return self._capture(claim, state)

    def remove(self):
        with container_lock(self.root / ".lock"):
            claim = self._claim()
            receipt = self._terminal(claim)
            removal = {"id": receipt["id"], "terminal": digest(receipt)}
            if (self.root / "removed.json").exists():
                if read(self.root / "removed.json") != removal:
                    raise Refused("codex_container_removal_invalid")
                return
            if (self.root / "remove-intent.json").exists():
                if read(self.root / "remove-intent.json") != removal:
                    raise Refused("codex_container_removal_invalid")
                # A successful daemon listing, not an inspect exception, proves absence.
                present = self.docker(
                    "ps",
                    "-a",
                    "--no-trunc",
                    "--filter",
                    "id=" + receipt["id"],
                    "--format",
                    "{{.ID}}",
                ).strip()
                if not present:
                    self._record("removed.json", removal)
                    return
                if present != receipt["id"]:
                    raise Refused("codex_container_removal_invalid")
            state = self._live(claim)
            if (
                self._execution(state) != receipt["execution"]
                or state["State"]["Running"]
                or (
                    state["State"]["Status"],
                    state["State"]["ExitCode"],
                )
                != (receipt["status"], receipt["exit_code"])
            ):
                self._record("conflict.json", {"file": "terminal.json"})
                raise Refused("codex_container_terminal_changed")
            self._record("remove-intent.json", removal)
            self.docker("rm", state["Id"])
            self._record("removed.json", removal)
