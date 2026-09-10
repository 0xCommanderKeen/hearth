"""What the probe inside the image reports about one address.

`hearth.integrations.reach` is the only Hearth code that ever runs on the sandbox
network, and it is asked one question: what happens when this address is dialled. Its
whole vocabulary matters, because `integrations/fence.py` reads a fence's verdict off
these words and refuses a start on them.
"""

import json
import socket
import subprocess
import sys

from hearth.integrations import reach


def listening() -> tuple[str, int, socket.socket]:
    """A listening socket, so `connected` is a real handshake and not a guess.

    Nothing accepts: the kernel completes the handshake into the backlog, which is
    exactly what the probe measures and all it measures.
    """
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return "127.0.0.1", server.getsockname()[1], server


def test_an_address_that_answers_is_connected_and_one_that_resets_is_refused():
    host, port, server = listening()
    try:
        assert reach.reach(f"{host}:{port}") == "connected"
    finally:
        server.close()
    # The same port with nothing behind it now: the packet still arrives and the
    # kernel answers it. That is not a fence holding, and the word says so.
    assert reach.reach(f"{host}:{port}") == "refused"


def test_a_name_that_does_not_resolve_is_not_reported_as_a_fence_holding():
    assert reach.reach("hearth-fence-nothing.invalid:443") == "unresolved"


def test_an_address_this_probe_cannot_read_is_said_so_rather_than_guessed_at():
    assert reach.reach("no-port-here") == "unreadable"
    assert reach.reach("host:0") == "unreadable"
    assert reach.reach("host:70000") == "unreadable"
    assert reach.reach(":443") == "unreadable"


def test_only_a_completed_handshake_counts_as_reachable():
    # Every other word this probe knows means the session did not get through, and
    # exactly one of them means it did.
    assert reach.CONNECTED == "connected"
    assert reach.CONNECTED not in reach.BLOCKED
    assert "refused" not in reach.BLOCKED


def test_the_probe_answers_one_json_document_for_every_address_it_was_given():
    host, port, server = listening()
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "hearth.integrations.reach", f"{host}:{port}", "host:0"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.close()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {f"{host}:{port}": "connected", "host:0": "unreadable"}


def test_the_probe_refuses_more_addresses_than_a_fence_may_name():
    result = subprocess.run(
        [sys.executable, "-I", "-m", "hearth.integrations.reach", *(["host:1"] * 40)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout.strip() == ""
