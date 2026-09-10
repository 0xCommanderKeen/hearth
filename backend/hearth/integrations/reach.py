"""What one address answers, asked from inside the sandbox itself.

ADR 0016 says a run's only network is the provider's: Hearth's own API, the host and
the LAN are not reachable from a session, and "a fence Hearth cannot see holding is not
one it relies on". Seeing it hold means asking from where the session is -- on the
sandbox network, from the sandbox image, as the sandbox's own uid -- because a fence
measured from Hearth's own network is a measurement of a different place.

This module is the whole of what runs there. It is started the way the bridge shim is,
`python3 -I -m hearth.integrations.reach <address> ...`, so it holds to the same rules:
the standard library alone, no import of anything else in Hearth, and one JSON document
on stdout. It opens a TCP connection and closes it; it sends no bytes, reads none, and
knows nothing about what is listening. `integrations/fence.py` is the half outside that
decides what the answers mean.

The vocabulary is the point. Exactly one word -- `connected` -- means a session got
through, and the rest are not interchangeable:

    connected   the handshake completed. Something is there and a session could talk.
    dropped     nothing came back at all. This is what a filtered packet looks like.
    no route    the kernel refused to send it: there is no way from here to there.
    refused     a reset came back. The packet *arrived*; a fence that lets it arrive
                and relies on nothing listening is not a fence, so this is not blocked.
    unresolved  the name has no address here. Not evidence about the network.
    unreadable  not an address this probe can dial, which is the operator's own typo.

`BLOCKED` names the two that mean a packet did not get out. `refused` and `unresolved`
are deliberately outside it: both are answers a fence could stop giving tomorrow
without anybody changing the fence.
"""

import concurrent.futures
import errno
import json
import socket
import sys

# One connection attempt's own patience. Every address is asked at once, so this is
# also very nearly the whole probe's runtime: a fence that drops packets answers by
# saying nothing, and saying nothing takes exactly this long.
TIMEOUT = 2.0
# At most this many addresses in one probe. A fence names a handful of places; a list
# longer than that is a mistake, and the probe is the last place to notice it.
LIMIT = 16

CONNECTED = "connected"
# The answers that mean the packet did not leave this network.
BLOCKED = ("dropped", "no route")


def reach(address: str, timeout: float = TIMEOUT) -> str:
    """Dial one `host:port` and answer in one word from the vocabulary above."""
    host, separator, port = address.rpartition(":")
    if not separator or not host or not port.isdigit() or not 0 < int(port) < 65536:
        return "unreadable"
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
    except socket.gaierror:
        return "unresolved"
    except TimeoutError:
        return "dropped"
    except OSError as error:
        if error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN):
            return "no route"
        if error.errno == errno.ECONNREFUSED:
            return "refused"
        # Anything else is a connection that did not happen for a reason this probe
        # cannot name. It is reported as itself rather than rounded into "blocked":
        # the fence's verdict must never be a word chosen because nothing better fit.
        return errno.errorcode.get(error.errno, "failed").lower()
    return CONNECTED


def measure(addresses, timeout: float = TIMEOUT) -> dict[str, str]:
    """Every address at once, because a dropped packet is measured by waiting."""
    addresses = list(dict.fromkeys(addresses))
    if not addresses:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        answers = pool.map(lambda one: reach(one, timeout), addresses)
        return dict(zip(addresses, answers, strict=True))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or len(argv) > LIMIT:
        print(f"usage: reach <host:port> ... (at most {LIMIT})", file=sys.stderr)
        return 2
    print(json.dumps(measure(argv), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
