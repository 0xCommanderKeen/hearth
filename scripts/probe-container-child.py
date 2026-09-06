"""Untrusted-side synthetic checks. Run only through probe-mac-container.py."""

import errno
import fcntl
import json
import os
import signal
import socket
import struct
import time
from pathlib import Path


def denied(operation):
    try:
        operation()
    except OSError as error:
        assert error.errno in {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT}, error
        return
    raise AssertionError("Forbidden operation succeeded")


def main():
    context = json.loads(Path("/input/context.json").read_text())
    assert context["notes"] == ["Synthetic isolation note"]
    assert os.getuid() == os.getgid() == 65534
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    assert int(status["NoNewPrivs"]) == 1
    assert int(status["Seccomp"]) == 2
    assert int(status["CapEff"], 16) == int(status["CapBnd"], 16) == 0
    cgroup = Path("/sys/fs/cgroup")
    assert (cgroup / "pids.max").read_text().strip() == "32"
    assert (cgroup / "memory.max").read_text().strip() == str(64 * 1024**2)
    assert (cgroup / "memory.swap.max").read_text().strip() == "0"
    quota, period = map(int, (cgroup / "cpu.max").read_text().split())
    assert quota * 2 == period
    interfaces = socket.if_nameindex()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        for _, interface in interfaces:
            request = struct.pack("256s", interface.encode())
            flags = struct.unpack_from("H", fcntl.ioctl(control, 0x8913, request), 16)[0]
            assert interface == "lo" or not flags & 1, (interface, flags)
    assert len(Path("/proc/net/route").read_text().splitlines()) == 1
    for route in Path("/proc/net/ipv6_route").read_text().splitlines():
        assert route.split()[-1] == "lo", route
    for path in context["unmounted"] + ["/input/escape", "/var/run/docker.sock"]:
        denied(lambda path=path: Path(path).read_bytes())
    for path in ["/input/context.json", "/etc/probe-write", "/tmp/probe-write", "/etc/hosts"]:
        denied(lambda path=path: Path(path).write_text("forbidden"))
    scratch = Path("/scratch/check")
    scratch.write_text("synthetic scratch")
    assert scratch.read_text() == "synthetic scratch"
    scratch.unlink()
    try:
        with scratch.open("wb") as file:
            for _ in range(32):
                file.write(b"x" * 65536)
    except OSError as error:
        assert error.errno == errno.ENOSPC, error
    else:
        raise AssertionError("Scratch quota did not hold")
    scratch.unlink()
    for host in ("192.0.2.1", "host.docker.internal"):
        try:
            with socket.create_connection((host, 9), timeout=1):
                raise AssertionError("Network connection succeeded")
        except OSError:
            pass
    # Ignore graceful cancellation, and escape the original process group/session.
    # A container-level kill must terminate this descendant too.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = os.fork()
    if child == 0:
        os.setsid()
        if os.fork():
            os._exit(0)
        Path("/scratch/descendant.json").write_text(
            json.dumps({"pid": os.getpid(), "sid": os.getsid(0), "pgid": os.getpgrp()})
        )
        while True:
            Path("/scratch/heartbeat").write_text(str(time.monotonic_ns()))
            time.sleep(0.05)
    os.waitpid(child, 0)
    deadline = time.monotonic() + 3
    while not Path("/scratch/descendant.json").exists():
        if time.monotonic() > deadline:
            raise AssertionError("Descendant did not start")
        time.sleep(0.02)
    print(
        json.dumps(
            {
                "checks": "passed",
                "parent": os.getpid(),
                "descendant": json.loads(Path("/scratch/descendant.json").read_text()),
                "seccomp": 2,
                "non_root": True,
            }
        ),
        flush=True,
    )
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
