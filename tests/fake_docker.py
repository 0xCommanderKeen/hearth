"""A `docker` that runs the command it is given, so the suite never needs a daemon.

It parses exactly the subset `integrations/launcher.py` speaks -- `run`, `inspect`,
`kill`, `wait`, `version`, `image inspect`, `network inspect` -- and then executes the
command on the host, in its own session, with the environment the `--env` flags name
and nothing else. That is enough for every runtime test to run under the container
launcher as well as the process one: the same fake CLI is started, the same stream
comes back through the same pipe, and the same signals end it.

What it deliberately does not do is confine anything. A mount is recorded, never
applied; a read-only root, a tmpfs and a uid are recorded, never enforced. The fake
proves the launcher's argv and the worker's handling of it, and the real container
semantics are measured against a real daemon instead (`docs/sandbox.md`).

The daemon's own contents are the state directory beside the installed executable:
`images/<digest>` and `networks/<name>` are what it holds, `image-files/<path>` is the
image's filesystem for `sha256sum`, `containers/<id>.json` is what is running, and
`calls.jsonl` is every invocation, for a test that wants to read the argv back.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Flags this client takes a value for. Anything else beginning with a dash is a
# switch, and the first bare word is the image.
VALUED = {
    "--cidfile",
    "--user",
    "--network",
    "--tmpfs",
    "--workdir",
    "--pids-limit",
    "--cap-drop",
    "--cap-add",
    "--security-opt",
    "--entrypoint",
    "--pull",
    "--name",
    "--label",
    "--signal",
    "--format",
    "--type",
    "--env",
    "-e",
    "--mount",
    "-v",
    "--memory",
    "--cpus",
    "--time",
}


def install(directory: Path) -> Path:
    """Write this module into `directory` as an executable `docker`, and return it."""
    path = directory / "docker"
    path.write_text("#!" + sys.executable + "\n" + Path(__file__).read_text())
    path.chmod(0o700)
    state(path).mkdir(parents=True, exist_ok=True)
    return path


def state(executable: Path | None = None) -> Path:
    """The daemon's contents: one directory beside the installed executable."""
    return (Path(__file__) if executable is None else executable).parent / "docker-state"


def hold(executable: Path, *, image: str | None = None, network: str | None = None) -> None:
    """Give the fake daemon an image digest or a network, as an operator would."""
    if image is not None:
        digest = image.split("@")[-1]
        (state(executable) / "images").mkdir(parents=True, exist_ok=True)
        (state(executable) / "images" / digest.replace(":", "-")).write_text(image)
    if network is not None:
        (state(executable) / "networks").mkdir(parents=True, exist_ok=True)
        (state(executable) / "networks" / network).write_text(network)


def carry(executable: Path, path: str, content: bytes) -> str:
    """Put one file inside the fake image and answer the sha256 it will report."""
    target = state(executable) / "image-files" / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def calls(executable: Path) -> list[list[str]]:
    """Every invocation the fake client has seen, oldest first."""
    path = state(executable) / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _parse(argv):
    """Flags up to the first bare word; everything from it is taken verbatim.

    That is the real client's own rule, and it is the one that matters here: the
    command after the image is the session's argv, and a `-c` or a `--print` inside
    it is the CLI's flag, never this client's.
    """
    flags, positional = {}, []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in VALUED:
            flags.setdefault(item, []).append(argv[index + 1])
            index += 2
            continue
        if item.startswith("-"):
            flags.setdefault(item, []).append("")
            index += 1
            continue
        positional = list(argv[index:])
        break
    return flags, positional


def _one(flags, *names, default=None):
    for name in names:
        if name in flags:
            return flags[name][-1]
    return default


def _record(root: Path, argv):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps(argv) + "\n")


def _container(root: Path, identity: str) -> Path:
    return root / "containers" / (identity + ".json")


def _translate(mounts: list[str], value: str) -> str:
    """One path inside the container, as the host names it.

    The fake has no namespace of its own, so a mount is applied by rewriting the paths
    it maps: a session told to read `/hearth/login/auth.json` reads the host directory
    that was mounted there. That is enough to drive an adapter that builds its command
    out of container paths, and it is the one thing about a mount this fake can honour.
    """
    for mount in mounts:
        fields = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)
        source, target = fields.get("source"), fields.get("target")
        if source and target and (value == target or value.startswith(target + "/")):
            return source + value[len(target) :]
    return value


def _run(root: Path, argv) -> int:
    flags, positional = _parse(argv)
    if not positional:
        return 125
    command = positional[1:]
    entrypoint = _one(flags, "--entrypoint")
    if entrypoint is not None:
        command = [entrypoint, *command]
    if command[:1] == ["sha256sum"]:
        # The image's own hash of one of its files, which is how the launcher checks
        # that the CLIs inside it are the ones this store is pinned to.
        path = root / "image-files" / command[1].lstrip("/")
        if not path.is_file():
            return 1
        print(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + command[1])
        return 0
    identity = os.urandom(32).hex()
    cidfile = _one(flags, "--cidfile")
    mounts = flags.get("--mount", [])
    command = [_translate(mounts, part) for part in command]
    environment = {
        name: _translate(mounts, value)
        for name, value in (
            item.split("=", 1)
            for item in flags.get("--env", []) + flags.get("-e", [])
            if "=" in item
        )
    }
    # A command that names a file the image carries runs the image's copy of it: the
    # session executes what the image holds, never what the host happens to have.
    carried = root / "image-files" / command[0].lstrip("/")
    if carried.is_file():
        carried.chmod(0o700)
        command = [str(carried), *command[1:]]
    child = subprocess.Popen(command, env=environment, start_new_session=True)
    _container(root, identity).parent.mkdir(parents=True, exist_ok=True)
    _container(root, identity).write_text(json.dumps({"pid": child.pid, "status": "running"}))
    if cidfile is not None:
        Path(cidfile).write_text(identity)
    code = child.wait()
    _container(root, identity).write_text(
        json.dumps({"pid": child.pid, "status": "exited", "code": code})
    )
    # A container the runtime signalled reports 128 plus the signal, never a negative
    # number; the launcher's own tests hold that difference from a plain child.
    return 128 - code if code < 0 else code


def _inspect(root: Path, argv) -> int:
    flags, positional = _parse(argv)
    if not positional:
        return 1
    path = _container(root, positional[0])
    if not path.is_file():
        return 1
    print(json.loads(path.read_text())["status"])
    return 0


def _kill(root: Path, argv) -> int:
    flags, positional = _parse(argv)
    if not positional:
        return 1
    path = _container(root, positional[0])
    if not path.is_file():
        return 1
    value = json.loads(path.read_text())
    if value["status"] != "running":
        return 1
    number = _one(flags, "--signal", default=str(int(signal.SIGKILL)))
    try:
        os.killpg(value["pid"], int(number))
    except ProcessLookupError, ValueError:
        return 1
    return 0


def _remove(root: Path, argv) -> int:
    """Take a container away, as `--rm` does when one ends and Hearth does to a stray."""
    _, positional = _parse(argv)
    if not positional:
        return 1
    path = _container(root, positional[0])
    if not path.is_file():
        return 1
    path.unlink()
    return 0


def _wait(root: Path, argv) -> int:
    flags, positional = _parse(argv)
    if not positional:
        return 1
    path = _container(root, positional[0])
    while path.is_file() and json.loads(path.read_text())["status"] == "running":
        time.sleep(0.02)
    if not path.is_file():
        return 1
    print(json.loads(path.read_text()).get("code", 0))
    return 0


def main(argv) -> int:
    root = state()
    _record(root, argv)
    if not argv:
        return 1
    if argv[0] == "version":
        print("27.3.1")
        return 0
    if argv[0] == "run":
        return _run(root, argv[1:])
    if argv[0] == "inspect":
        return _inspect(root, argv[1:])
    if argv[0] == "kill":
        return _kill(root, argv[1:])
    if argv[0] == "rm":
        return _remove(root, argv[1:])
    if argv[0] == "wait":
        return _wait(root, argv[1:])
    if argv[:2] == ["image", "inspect"]:
        _, positional = _parse(argv[2:])
        name = positional[0] if positional else ""
        held = root / "images" / name.split("@")[-1].replace(":", "-")
        if not held.is_file():
            return 1
        print("sha256:" + "0" * 64)
        return 0
    if argv[:2] == ["network", "inspect"]:
        _, positional = _parse(argv[2:])
        name = positional[0] if positional else ""
        if not (root / "networks" / name).is_file():
            return 1
        print(name)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
