"""Measure what the Claude CLI and Hearth's own bridge do inside a container. Never in CI.

Slice #186 of epic #183 moves a `claude_subscription` run into the sandbox, and four
facts it rests on were open questions rather than measurements: where a Linux login
lives, whether the CLI will run with the configuration directory Hearth gives it, what
it needs of the image besides its own bytes, and whether Hearth's bridge -- the shim
inside the container, the socket outside it, the peer-credential check between them --
still holds across the boundary. This measures all four against a real daemon and
writes the answers as evidence.

    uv run --frozen python scripts/measure-claude-sandbox.py \
        --claude /path/to/the/linux/build/of/the/pinned/cli \
        --docker /usr/local/bin/docker \
        --report docs/evidence/sandbox-claude-<date>.json

**No credential is read, copied or written by this script.** The login questions are
answered with a *synthetic* credentials file this script writes itself -- the token in
it is the string `synthetic-not-a-token`, and every session it opens is refused by the
provider with a 401, which is the answer being measured. Nothing here spends money and
nothing here needs a login. A real logged-in session is `scripts/claude-journey.py`,
which the operator runs with a login of their own.

It writes into a temporary directory of its own, removes every container it started by
id -- never by label, because Hearth stamps that label on every production sandbox and
this script may be run on a host that is serving work -- and never touches a Hearth
data directory.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from hearth.integrations.claude.config import (  # noqa: E402
    CREDENTIALS,
    VERSION,
    binary_digest,
    environment,
    session_command,
)
from hearth.integrations.claude.mcp_bridge import (  # noqa: E402
    MODULE,
    SOCKET_NAME,
    configuration,
)
from hearth.integrations.launcher import (  # noqa: E402
    CONTAINER,
    LABEL,
    LOGIN,
    PYTHON,
    WORKSPACE,
    Placement,
)

# Any image with the interpreter the sandbox image is built on will do for the
# questions about the runtime; the questions about the CLI run the CLI itself, mounted
# in. What is measured is the boundary, not the base layer.
DEFAULT_IMAGE = "python:3.14-slim"
# Where the hearth package sits inside the sandbox image, and therefore where it is
# mounted here: the shim is started with `python -I`, which ignores `PYTHONPATH`.
SITE = "/usr/local/lib/python3.14/site-packages/hearth"
# The synthetic login. It is a real *shape* -- the CLI reads it and reports a login --
# and it is not a credential: the provider answers 401 to it, every time.
SYNTHETIC = {
    "claudeAiOauth": {
        "accessToken": "synthetic-not-a-token",
        "refreshToken": "synthetic-not-a-token",
        "expiresAt": 33229735200000,
        "scopes": ["user:inference", "user:profile"],
        "subscriptionType": "max",
    }
}
STARTED: list[str] = []


def client(docker: str, *arguments: str, stdin: bytes | None = None, timeout=180) -> str:
    result = subprocess.run(
        [docker, *arguments],
        input=stdin,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return (result.stdout + result.stderr).decode("utf-8", "replace").strip()


def contained(docker: str, image: str, flags, command, *, uid=None, stdin=None) -> str:
    """One throwaway container, run to completion, everything it said.

    `flags` are the runtime's, `command` is what runs inside; the image sits between
    them, which is where the real client's own parsing puts it.
    """
    identity = "hearth-measure-" + os.urandom(8).hex()
    STARTED.append(identity)
    person = f"{os.getuid()}:{os.getgid()}" if uid is None else uid
    return client(
        docker,
        "run",
        "--rm",
        "--name",
        identity,
        "--label",
        LABEL + "=1",
        "--user",
        person,
        *flags,
        image,
        *command,
        stdin=stdin,
    )


def daemon(docker: str) -> dict:
    """Which runtime answered, and what it is running on."""
    fields = (
        ("version", "{{.Server.Version}}"),
        ("os", "{{.Server.Os}}/{{.Server.Arch}}"),
        ("kernel", "{{.Server.KernelVersion}}"),
    )
    answer = {name: client(docker, "version", "--format", form) for name, form in fields}
    answer["host"] = platform.platform()
    return answer


def cli(docker: str, image: str, claude: Path, workspace: Path) -> dict:
    """The Linux build of the pinned CLI: its bytes, and what it calls itself.

    Hearth pins a provider CLI by sha256, and a macOS build and a Linux build are
    never the same bytes -- so a sandboxed household is configured with the Linux
    build, the image carries that same file, and the version string it reports has to
    be the one this runtime knows.
    """
    return {
        "path": str(claude),
        "sha256": binary_digest(claude),
        "version": contained(
            docker,
            image,
            [
                "--network",
                "none",
                "--mount",
                f"type=bind,source={claude},target=/usr/local/bin/claude,readonly",
            ],
            ["/usr/local/bin/claude", "--version"],
        ),
        "hearth_expects": VERSION,
    }


def login_directory(docker: str, image: str, claude: Path, workspace: Path) -> dict:
    """Where a login lives on Linux, and what the CLI writes beside it.

    ADR 0016 assumed a login is a directory mounted read-only at the CLI's own
    configuration path. Two things are asked here: which file in that directory is the
    login, and whether the directory can be read-only at all.
    """
    empty = workspace / "empty-configuration"
    planted = workspace / "planted-configuration"
    sealed = workspace / "read-only-configuration"
    for path in (empty, planted, sealed):
        path.mkdir()
    (planted / CREDENTIALS).write_text(json.dumps(SYNTHETIC))

    def status(directory: Path, *, readonly: bool) -> dict:
        answer = contained(
            docker,
            image,
            [
                "--network",
                "none",
                "--mount",
                f"type=bind,source={claude},target=/usr/local/bin/claude,readonly",
                "--mount",
                f"type=bind,source={directory},target=/cfg" + (",readonly" if readonly else ""),
            ],
            [
                "env",
                "-i",
                "PATH=/usr/bin:/bin",
                "CLAUDE_CONFIG_DIR=/cfg",
                "/usr/local/bin/claude",
                "auth",
                "status",
                "--json",
            ],
        )
        try:
            read = json.loads(answer)
        except ValueError:
            return {"answer": answer[:400]}
        return {
            # `loggedIn` is the one field Hearth reads. The rest of that answer names
            # an account, and none of it is a credential; the login here is synthetic.
            "loggedIn": read.get("loggedIn"),
            "authMethod": read.get("authMethod"),
            "projectsDirectory": read.get("projectsDirectory"),
            "wrote": sorted(item.name for item in directory.iterdir()),
        }

    return {
        "environment": "env -i PATH CLAUDE_CONFIG_DIR (no USER, no HOME)",
        "empty_directory": status(empty, readonly=False),
        "with_credentials_file": status(planted, readonly=False),
        "credentials_file": CREDENTIALS,
        "read_only_directory": status(sealed, readonly=True),
    }


def session(docker: str, image: str, claude: Path, workspace: Path, *, passwd: str) -> dict:
    """One bounded session in the shape Hearth gives it, under the sandbox's own flags.

    The argv, the environment and the placement are Hearth's own: `Placement` answers
    every path, `session_command` builds the whole command, and the login is a tmpfs
    with one file bind-mounted read-only inside it. The provider refuses the synthetic
    token, so what this measures is everything up to and including the first API
    answer -- which is the whole of what the container has to make possible.
    """
    placement = Placement(CONTAINER)
    # A household login of its own, holding nothing but the credential, so that what
    # is in it afterwards is what these sessions put there and nothing earlier did.
    directory = workspace / "session-configuration"
    directory.mkdir()
    (directory / CREDENTIALS).write_text(json.dumps(SYNTHETIC))
    home = placement.login(directory, CREDENTIALS, LOGIN)
    command = session_command(
        placement.binary(claude, "claude"), budget_usd="0.010000", tools=(), mcp_config=None
    )
    env = environment(home, account=False)
    common = [
        "--init",
        "--interactive",
        "--network",
        "bridge",
        "--read-only",
        "--tmpfs",
        WORKSPACE + ":rw,noexec,nosuid,nodev,size=64m",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--workdir",
        WORKSPACE,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--pids-limit",
        "512",
        "--mount",
        f"type=bind,source={claude},target=/usr/local/bin/claude,readonly",
        *[part for name, value in sorted(env.items()) for part in ("--env", f"{name}={value}")],
    ]

    def opened(mounts: list[str]) -> list[dict]:
        answer = contained(
            docker,
            passwd,
            common + [part for mount in mounts for part in ("--mount", mount)],
            command,
            stdin=b"Say pong and nothing else.",
        )
        events = []
        for line in answer.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                events.append({"not_json": line[:200]})
                continue
            events.append(
                {
                    key: event.get(key)
                    for key in ("type", "subtype", "api_error_status", "terminal_reason", "cwd")
                    if event.get(key) is not None
                }
            )
        return events[:8]

    placed = [mount.argument() for mount in placement.mounts]
    return {
        "command": command,
        "environment": env,
        "mounts": placed,
        "as_hearth_places_it": opened(placed),
        # What ADR 0016 asked for instead: the household's directory itself, mounted
        # read-only at the CLI's configuration path.
        "with_a_read_only_configuration_directory": opened(
            [f"type=bind,source={directory},target={LOGIN},readonly"]
        ),
        # Both sessions above ran against this directory, and it holds what it held.
        # Everything the CLI wrote -- its own `.claude.json`, a lock, backups, the
        # projects and sessions directories -- went into the run's own tmpfs and left
        # with it, or was refused by the read-only mount and left nothing either.
        "the_household_login_afterwards": sorted(item.name for item in directory.iterdir()),
    }


def home_directory(docker: str, image: str, passwd: str, claude: Path) -> dict:
    """Whether the uid the sandbox runs as has to exist in the image's own passwd file.

    Hearth runs the session as its own uid so that the bridge's peer check still means
    something. Whether that uid is *named* in the image is a build-time question, and
    it turned out to be a hard one. Asked with a question the CLI has to start a
    runtime to answer, because `--version` is answered before it looks.
    """

    def attempt(image_name: str) -> str:
        return contained(
            docker,
            image_name,
            [
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,size=64m",
                "--tmpfs",
                "/cfg:rw,size=64m",
                "--mount",
                f"type=bind,source={claude},target=/usr/local/bin/claude,readonly",
            ],
            [
                "env",
                "-i",
                "PATH=/usr/bin:/bin",
                "CLAUDE_CONFIG_DIR=/cfg",
                "/usr/local/bin/claude",
                "auth",
                "status",
                "--json",
            ],
        )

    return {
        "uid": os.getuid(),
        "without_a_passwd_entry": attempt(image)[-400:],
        "with_a_passwd_entry": attempt(passwd)[-400:],
    }


# -- the bridge across the boundary ----------------------------------------


def serve(path: str) -> int:
    """The trusted half, for this measurement: Hearth's own socket and its own check.

    It is *not* Hearth's authority -- there is no store here and nothing is mutated.
    What it is, exactly, is the transport and the peer-credential rule from
    `mcp_bridge.py`, so what the measurement shows is whether those two hold when the
    other end is inside a container.
    """
    from hearth.integrations.claude.mcp_bridge import listen, peer_uid

    listener = listen(path)
    listener.setblocking(True)
    listener.settimeout(60)
    seen = []
    print(json.dumps({"listening": path, "uid": os.getuid()}), flush=True)
    while len(seen) < 2:
        try:
            connection, _ = listener.accept()
        except OSError:
            break
        uid = peer_uid(connection)
        accepted = uid == os.getuid()
        seen.append({"peer_uid": uid, "accepted": accepted})
        print(json.dumps({"connection": seen[-1]}), flush=True)
        if not accepted:
            # The rule the worker applies: only the user that owns the run reaches
            # its tools, whatever else can see the socket.
            connection.close()
            continue
        buffer = bytearray()
        while b"\n" not in buffer:
            chunk = connection.recv(65536)
            if not chunk:
                break
            buffer.extend(chunk)
        request = json.loads(bytes(buffer).split(b"\n", 1)[0])
        print(json.dumps({"asked": request}), flush=True)
        if request.get("op") == "tools/list":
            result = {"tools": [{"name": "hearth_journal_write", "description": "", "in": {}}]}
        else:
            result = {"content": [{"type": "text", "text": "the trusted half answered"}]}
        connection.sendall(json.dumps({"result": result}).encode() + b"\n")
        connection.close()
    listener.close()
    return 0


def bridge(docker: str, image: str, repository: Path) -> dict:
    """The shim in a container, the socket in a volume, the peer check between them.

    This is the shape a burrow runs: Hearth's worker owns the socket, the session is
    in a container of its own, and the socket is mounted into it at the path the
    configuration Hearth wrote already names. On a Mac the socket cannot come from the
    host's filesystem at all (`docs/sandbox.md`, measurement 6), so both ends are
    containers here and the socket lives in a volume the daemon owns -- which is the
    server shape, and the one the production burrow uses.
    """
    volume = "hearth-claude-bridge-measurement"
    inside = f"/run/hearth/{SOCKET_NAME}"
    package = f"type=bind,source={repository / 'backend' / 'hearth'},target={SITE},readonly"
    mount = f"type=volume,source={volume},target=/run/hearth"
    answer: dict = {
        "socket_inside_both_containers": inside,
        "configuration_hearth_would_write": configuration(PYTHON, Path(inside)),
    }
    client(docker, "volume", "create", volume)
    # The volume is the daemon's and starts as root's. Hearth's own run folder is
    # Hearth's already; here it is given to the uid both ends run as.
    client(
        docker,
        "run",
        "--rm",
        "--label",
        LABEL + "=1",
        "--mount",
        mount,
        image,
        "chown",
        f"{os.getuid()}:{os.getgid()}",
        "/run/hearth",
    )
    identity = "hearth-measure-" + os.urandom(8).hex()
    STARTED.append(identity)
    server = subprocess.Popen(
        [
            docker,
            "run",
            "--rm",
            "--name",
            identity,
            "--label",
            LABEL + "=1",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--mount",
            mount,
            "--mount",
            package,
            "--mount",
            f"type=bind,source={Path(__file__).resolve()},target=/measure.py,readonly",
            image,
            "python3",
            "-u",
            "/measure.py",
            "--serve",
            inside,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert server.stdout is not None
        answer["server"] = json.loads(server.stdout.readline())

        def shim(uid: str, request: dict) -> dict:
            """One MCP request, spoken to the shim inside a container of its own."""
            frames = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                    }
                )
                + "\n"
                + json.dumps(request)
                + "\n"
            )
            said = contained(
                docker,
                image,
                [
                    "--interactive",
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,size=16m",
                    "--mount",
                    mount,
                    "--mount",
                    package,
                    # The environment the configuration document declares for the
                    # shim: a search path and nothing else.
                    "--env",
                    "PATH=/usr/local/bin:/usr/bin:/bin",
                ],
                # Exactly the command that document names.
                ["python3", "-I", "-m", MODULE, inside],
                uid=uid,
                stdin=frames.encode(),
            )
            replies = []
            for line in said.splitlines():
                try:
                    replies.append(json.loads(line))
                except ValueError:
                    replies.append({"not_json": line[:200]})
            return {"uid": uid, "replies": replies}

        answer["as_hearths_own_uid"] = shim(
            f"{os.getuid()}:{os.getgid()}", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        # The negative control: another user on the same host reaches the socket file
        # and is refused by the kernel's own answer about who it is.
        answer["as_another_uid"] = shim("0:0", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        server.wait(60)
        answer["server_saw"] = [
            json.loads(line)
            for line in (server.stdout.read() or b"").decode().splitlines()
            if line.strip()
        ]
    finally:
        if server.poll() is None:
            client(docker, "kill", identity)
            server.wait(30)
        client(docker, "volume", "rm", "--force", volume)
    return answer


def cleanup(docker: str) -> None:
    for identity in STARTED:
        subprocess.run(
            [docker, "rm", "--force", identity], capture_output=True, check=False, timeout=60
        )


def build_passwd(docker: str, image: str, workspace: Path) -> str:
    """A copy of the base image that names Hearth's uid, as the sandbox image does."""
    context = workspace / "passwd-image"
    context.mkdir()
    (context / "Dockerfile").write_text(
        f"FROM {image}\n"
        f"RUN groupadd --gid {os.getgid()} --non-unique hearth || true; \\\n"
        f"    useradd --uid {os.getuid()} --gid {os.getgid()} --non-unique --no-create-home \\\n"
        "        --home-dir /nonexistent --shell /usr/sbin/nologin hearth\n"
    )
    tag = "hearth-measure-passwd:local"
    client(docker, "build", "--quiet", "--tag", tag, str(context), timeout=600)
    return tag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", help=serve.__doc__)
    parser.add_argument("--claude", type=Path, help="the Linux build of the pinned CLI")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    if arguments.serve:
        return serve(arguments.serve)
    if arguments.claude is None:
        parser.error("--claude is required")
    repository = Path(__file__).resolve().parent.parent
    workspace = Path(tempfile.mkdtemp(prefix="hearth-claude-measure-"))
    report: dict = {
        "measured": time.strftime("%Y-%m-%d"),
        "what": "the Claude runtime inside a per-run sandbox (hearth#186)",
        "credentials": (
            "none read, copied or written; the login here is a synthetic file this "
            "script writes and the provider refuses with a 401"
        ),
        "cost": "nothing: no session reached a model",
    }
    try:
        report["daemon"] = daemon(arguments.docker)
        report["cli"] = cli(arguments.docker, arguments.image, arguments.claude, workspace)
        # Everything the CLI is asked below runs as Hearth's own uid, and that uid has
        # to be a user the image knows -- which is the next measurement, and the
        # reason this image is built before the ones that need it.
        passwd = build_passwd(arguments.docker, arguments.image, workspace)
        report["home_directory"] = home_directory(
            arguments.docker, arguments.image, passwd, arguments.claude
        )
        report["login_directory"] = login_directory(
            arguments.docker, passwd, arguments.claude, workspace
        )
        report["session"] = session(
            arguments.docker, arguments.image, arguments.claude, workspace, passwd=passwd
        )
        report["bridge"] = bridge(arguments.docker, arguments.image, repository)
    finally:
        cleanup(arguments.docker)
        shutil.rmtree(workspace, ignore_errors=True)
    raw = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if arguments.report:
        arguments.report.write_text(raw)
        print("wrote", arguments.report)
    else:
        print(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
