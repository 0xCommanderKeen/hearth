"""The sandbox's only network, measured from inside it before anything is admitted.

ADR 0016: "the sandbox has no route to Hearth's API, to the host or to the LAN; its
egress is the provider's own API host". Enforcement is the operator's -- a container
network with that policy, `deploy/README.md` for how one is built -- and Hearth's part
is the sentence after it: *a fence Hearth cannot see holding is not one it relies on*.
So a start on the `container` launcher does not take the network's name as a promise.
It runs one container on that network, from the same pinned image a session runs from,
as the same uid, with nothing mounted and nothing in its environment, and asks it what
it can reach (`integrations/reach.py`). Anything but "the provider and nothing else"
refuses `sandbox_network_open`, and what was seen is in the audit either way.

The fence is *two lists of addresses*, not a policy Hearth understands:

    HEARTH_SANDBOX_SHUT=<host:port>,...   must not be reachable -- Hearth's own
                                          address, one address on the LAN
    HEARTH_SANDBOX_OPEN=<host:port>,...   must be reachable -- the provider's API host

Hearth does not derive them. Which address answers for Hearth depends on how the
operator published it, and which address is "the LAN" is a fact about a building; a
guess at either would be a fence measured somewhere nobody lives. Both lists are
required on the container launcher, because an empty one is a measurement that always
passes.

It is not part of `Sandbox`, and so it never travels to the worker: this is a question
about the burrow asked at start and again whenever an operator asks `GET /api/health`,
not something a run carries. A run's own network is the network's, and the start that
admitted it is what vouched for that.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass

from hearth.integrations.launcher import (
    CLIENT_TIMEOUT,
    CONTAINER,
    LABEL,
    PYTHON,
    ContainerLauncher,
    Sandbox,
)
from hearth.integrations.reach import BLOCKED, CONNECTED, LIMIT
from hearth.residents.models import Refused

SHUT = "HEARTH_SANDBOX_SHUT"
OPEN = "HEARTH_SANDBOX_OPEN"
# One address, as an operator writes it: a host name or an IPv4 address and a port.
# IPv6 is not spelled here, because `host:port` cannot hold it unambiguously and no
# host this has run on needed it; an operator who does names the address by name.
ADDRESS = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,253}):([0-9]{1,5})\Z")
# The probe, named as the image's own interpreter will import it.
PROBE = "hearth.integrations.reach"


@dataclass(frozen=True)
class Fence:
    """What must be unreachable from inside a sandbox, and what must be reachable."""

    shut: tuple[str, ...] = ()
    open: tuple[str, ...] = ()

    def __post_init__(self):
        for address in self.addresses:
            match = ADDRESS.match(address) if isinstance(address, str) else None
            if match is None or not 0 < int(match.group(2)) < 65536:
                raise Refused("sandbox_fence_invalid")
        if len(self.addresses) > LIMIT or len(set(self.addresses)) != len(self.addresses):
            # One address on both lists is an operator asking for two answers about
            # one place, and the probe would give it one.
            raise Refused("sandbox_fence_invalid")

    @property
    def addresses(self) -> tuple[str, ...]:
        """Every address this fence names, shut before open, in the order it is asked."""
        return (*self.shut, *self.open)

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> Fence:
        values = os.environ if environment is None else environment

        def listed(name: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in values.get(name, "").split(",") if part.strip())

        return cls(shut=listed(SHUT), open=listed(OPEN))

    def observe(self, sandbox: Sandbox) -> dict:
        """Run the probe on the sandbox network and read what it reached.

        The probe container is the session's own shape with everything a session gets
        taken away: the pinned image, the sandbox network, Hearth's uid, a read-only
        root, no capabilities, no mount and no environment. It is labelled like every
        other container Hearth starts, so an operator can find one left behind -- and
        it ends on its own within a couple of seconds whatever happens to Hearth,
        because dialling an address is all it does.
        """
        # Nothing to measure is refused rather than asserted: a caller with a process
        # launcher or half a fence is asking a question with no answer, and an answer
        # is the one thing this may not invent. `/api/health` reports the refusal.
        if sandbox.launcher != CONTAINER or not self.shut or not self.open:
            raise Refused("sandbox_fence_unconfigured")
        launcher = sandbox.open()
        assert isinstance(launcher, ContainerLauncher)
        try:
            result = launcher.attempt(
                "run",
                "--rm",
                "--network",
                launcher.network,
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
                "--pids-limit",
                "64",
                "--label",
                LABEL + "=1",
                "--entrypoint",
                PYTHON,
                launcher.image,
                "-I",
                "-m",
                PROBE,
                *self.addresses,
                timeout=CLIENT_TIMEOUT,
            )
        except OSError, subprocess.SubprocessError:
            raise Refused("sandbox_runtime_unavailable") from None
        # The same separation `hashed` makes, for the same reason: a daemon that would
        # not run the container at all and an image that could not answer send an
        # operator to opposite places, and 125 is the client's own word for the first.
        if result.returncode == 125:
            raise Refused("sandbox_runtime_unavailable")
        try:
            answers = json.loads(result.stdout)
        except ValueError:
            answers = None
        if (
            result.returncode
            or not isinstance(answers, dict)
            or set(answers) != set(self.addresses)
            or not all(isinstance(answer, str) for answer in answers.values())
        ):
            # Not a fence that is open: a fence nobody measured. Saying it held would
            # be the one thing this module exists to refuse to say.
            raise Refused("sandbox_fence_unmeasured")
        return self.verdict(answers)

    def verdict(self, answers: dict[str, str]) -> dict:
        """What the probe's words mean for this fence, address by address.

        A shut address holds only when the packet did not get out -- `refused` is a
        reset, which is a packet that arrived at something, and a fence that lets a
        packet arrive is relying on nothing being there to answer it tomorrow. An open
        address holds only on a completed handshake, because that is the one word that
        means a session could really talk to the provider.
        """
        shut = {address: answers[address] for address in self.shut}
        opened = {address: answers[address] for address in self.open}
        return {
            "shut": shut,
            "open": opened,
            "held": all(answer in BLOCKED for answer in shut.values())
            and all(answer == CONNECTED for answer in opened.values()),
        }


def check(database, sandbox: Sandbox, fence: Fence, clock=time.time) -> dict:
    """Measure the fence this instance's sessions will be on, and refuse an open one.

    Written down before it is refused: an operator whose instance will not start needs
    to read *which* address answered, and the refusal itself carries only a name. The
    fact is recorded once per start, as an absent runtime and a lapsed login are.
    """
    if sandbox.launcher != CONTAINER:
        return {}
    state = fence.observe(sandbox)
    from hearth.work.service import _audit

    with database.transaction(write=True) as db:
        _audit(
            db,
            "sandbox.fence",
            CONTAINER,
            int(clock()),
            {"network": sandbox.network} | state,
        )
    if not state["held"]:
        raise Refused("sandbox_network_open")
    return state
