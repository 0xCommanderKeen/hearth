#!/bin/sh
# The packet filter that makes `hearth-egress` the sandbox's only network.
#
# ADR 0016 says a session's egress is the provider and nothing else, and that Hearth
# does not trust the network's name for it: `integrations/fence.py` measures what a
# container on this network can really reach, and an instance whose fence does not hold
# refuses `sandbox_network_open` and does not open. This script is the other half --
# what an operator installs so that the measurement passes.
#
# What it does, keyed to the sandbox network's subnet and nothing else on the host:
#
#   * every private destination is dropped -- RFC 1918, the link-local range and the
#     carrier-grade range Tailscale uses. That covers Hearth's own network, every
#     other container on this daemon, the LAN and the host's own addresses on it.
#   * the host itself is dropped, which needs a rule in `INPUT` as well: a container
#     reaching its own gateway is not forwarded traffic and `DOCKER-USER` never sees it.
#   * everything else is left alone, which is the public internet, which is the
#     provider.
#
# **What this is not.** It is not an allowlist of the provider's own addresses. The
# provider is behind a CDN whose addresses rotate, so a list of them is a fence that
# breaks on somebody else's deploy; expressing "the provider and nothing else" exactly
# needs an egress proxy the CLIs are pointed at, which is not built here. What is built
# here, and what Hearth measures, is "nothing of this house". `docs/sandbox.md` says so
# in the same words, and the ADR's own Measured section records the difference.
#
# DROP, never REJECT --reject-with tcp-reset: a reset is a packet that arrived, Hearth's
# probe reads it as `refused`, and `refused` is not a fence holding.
#
# Usage (on a Linux host, as root):
#
#     deploy/fence.sh apply           # install the rules
#     deploy/fence.sh show            # what is installed
#     deploy/fence.sh remove          # take them out again
#
# On Docker Desktop the rules live inside its Linux VM, and the way in is a privileged
# container on the host network -- see `deploy/README.md`.
#
# Environment:
#
#     HEARTH_EGRESS_SUBNET    the sandbox network's subnet (compose fixes it)
#     HEARTH_EGRESS_RESOLVER  an address to let DNS through to, when the daemon's
#                             embedded resolver forwards from inside the container's
#                             own namespace. Docker Desktop does (measured: without
#                             this every name answered `unresolved`); most Linux hosts
#                             forward from the host's namespace and need nothing here.

set -eu

SUBNET=${HEARTH_EGRESS_SUBNET:-172.31.240.0/24}
RESOLVER=${HEARTH_EGRESS_RESOLVER:-}
PRIVATE="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10"

# Docker's own chains are in whichever iptables variant Docker used, and a host may
# have both. The one that has `DOCKER-USER` is the one that is live.
iptables=""
for candidate in iptables-legacy iptables-nft iptables; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -S DOCKER-USER >/dev/null 2>&1; then
        iptables=$candidate
        break
    fi
done
if [ -z "$iptables" ]; then
    echo "no iptables with a DOCKER-USER chain here; is this the Docker host?" >&2
    exit 1
fi

# Every rule this script owns, as arguments, most specific first. `apply` inserts them
# in reverse so they end up in this order; `remove` deletes them all. Both are
# idempotent, because every rule is deleted before it is inserted.
rules() {
    if [ -n "$RESOLVER" ]; then
        # The daemon's own resolver, for name lookups and nothing else. A session that
        # cannot resolve the provider cannot reach it, and Hearth would refuse.
        echo "DOCKER-USER -s $SUBNET -d $RESOLVER -p udp --dport 53 -j RETURN"
        echo "DOCKER-USER -s $SUBNET -d $RESOLVER -p tcp --dport 53 -j RETURN"
        echo "INPUT -s $SUBNET -d $RESOLVER -p udp --dport 53 -j ACCEPT"
        echo "INPUT -s $SUBNET -d $RESOLVER -p tcp --dport 53 -j ACCEPT"
    fi
    for destination in $PRIVATE; do
        echo "DOCKER-USER -s $SUBNET -d $destination -j DROP"
    done
    # The host's own addresses, which forwarded traffic never passes through.
    echo "INPUT -s $SUBNET -j DROP"
}

remove() {
    rules | while read -r chain arguments; do
        # shellcheck disable=SC2086
        while "$iptables" -D "$chain" $arguments 2>/dev/null; do :; done
    done
}

apply() {
    remove
    # Reverse, because each is inserted at the top of its chain.
    rules | sed '1!G;h;$!d' | while read -r chain arguments; do
        # shellcheck disable=SC2086
        "$iptables" -I "$chain" 1 $arguments
    done
}

case "${1:-}" in
apply)
    apply
    echo "fence applied for $SUBNET with $iptables"
    ;;
remove)
    remove
    echo "fence removed for $SUBNET with $iptables"
    ;;
show)
    "$iptables" -S DOCKER-USER
    "$iptables" -S INPUT
    ;;
*)
    echo "usage: $0 apply|remove|show" >&2
    exit 2
    ;;
esac
