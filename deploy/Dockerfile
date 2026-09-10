# Hearth itself, for a server: the release wheel, the locked dependencies, a container
# client to start sandboxes with, and nothing else.
#
# This is the *control plane*, not the sandbox. It holds the store, the authority and
# the detached workers; `deploy/Dockerfile.sandbox` is the image a run's session
# executes in, and the two never share a layer. What makes this image a container at
# all is that the burrow is a Linux server (ADR 0016): the bridge socket a session
# talks to Hearth over cannot be bind-mounted from a Mac (measurement 6), and the
# store's provider pins are Linux binaries.
#
# Unlike the sandbox image, this one is built from the repository and the network:
# nothing in it is a credential and nothing in it is pinned by a store. What it does
# carry from the build context is the release wheel, because that wheel holds the
# built browser (`scripts/build-web.py`) and no `pip install .` reproduces it:
#
#   make check                       # builds web/dist into the package and the wheel
#   docker build -f deploy/Dockerfile -t hearth:<version> .
#
# `deploy/README.md` is the runbook: the network, the fence, the volumes, the logins
# and what an upgrade and a restore look like.

# The interpreter Hearth is written for, pinned by digest, and the same base the
# sandbox image is built on -- one thing to move when either moves.
ARG BASE=python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
# The resolver, only in the build. Hearth's dependencies are locked in `uv.lock`, and
# an image that resolved them again would not be the release this repository tested.
ARG UV=ghcr.io/astral-sh/uv@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac
# The container client. Hearth starts every session by running this, so the client is
# part of the image and the *daemon* is the socket compose mounts (ADR 0016: that
# socket is root-equivalent on the host, which is why nothing a resident says ever
# reaches this client's argv unparsed).
ARG DOCKER=docker@sha256:328eb399a065780c2cebe9224de003aa14084cf69efae882ac27430f921819b7

FROM ${UV} AS uv
FROM ${DOCKER} AS client


FROM ${BASE} AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
# The lock, exported with its hashes, is what the runtime environment is built from.
COPY pyproject.toml uv.lock ./
RUN uv export --quiet --frozen --no-dev --no-emit-project --output-file /tmp/locked.txt
# The release wheel this repository built, with the browser inside it.
ARG WHEEL=dist/hearth-0.1.0-py3-none-any.whl
COPY ${WHEEL} /tmp/wheel/
RUN set -eux; \
    uv venv /opt/hearth --python "$(command -v python3)"; \
    uv pip sync --python /opt/hearth/bin/python --require-hashes /tmp/locked.txt; \
    uv pip install --python /opt/hearth/bin/python --no-deps /tmp/wheel/*.whl; \
    # A wheel whose browser did not come along is a release nobody can operate. The
    # Makefile's own wheel check says the same thing about the file; this says it
    # about the image, where an operator would otherwise find out by opening a page.
    test -f /opt/hearth/lib/python3.14/site-packages/hearth/web/index.html


FROM ${BASE}
# The client, not the daemon: `HEARTH_SANDBOX_DOCKER` names this path and compose
# mounts the socket it talks to.
COPY --from=client /usr/local/bin/docker /usr/local/bin/docker
COPY --from=build /opt/hearth /opt/hearth

# The uid Hearth runs as, and therefore the uid every sandbox runs as -- so the same
# number has to have built `deploy/Dockerfile.sandbox`, or every Claude session in it
# ends before its first byte (`docs/sandbox.md`, measurement 11). It is a build
# argument here for symmetry with that image and because the entry has to exist: the
# CLIs Hearth starts ask the system who they are.
ARG HEARTH_UID=1000
ARG HEARTH_GID=1000
RUN set -eux; \
    groupadd --gid ${HEARTH_GID} --non-unique hearth; \
    useradd --uid ${HEARTH_UID} --gid ${HEARTH_GID} --non-unique --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hearth; \
    # Nothing in here installs anything. The data directory is a volume and the code
    # is a release; a package manager left runnable is one more way for a container
    # with the daemon's socket to become something else.
    rm -rf /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-* \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 \
        /usr/bin/apt /usr/bin/apt-get /usr/bin/apt-cache /usr/bin/apt-config \
        /usr/bin/apt-key /usr/bin/apt-mark /usr/bin/dpkg /usr/bin/dpkg-deb \
        /usr/bin/dpkg-query /usr/bin/dpkg-split /usr/bin/dpkg-trigger \
        /usr/bin/dpkg-divert /usr/bin/dpkg-statoverride /usr/bin/dpkg-maintscript-helper \
        /var/lib/apt/lists/* /var/cache/apt/*; \
    # The release really runs, as the user it will run as, before this is an image.
    su hearth -s /bin/sh -c '/opt/hearth/bin/python -I -c "import hearth.app"'

ENV PATH=/opt/hearth/bin:/usr/local/bin:/usr/bin:/bin
# Where the store is. It has no default: the path a Hearth in a container hands the
# daemon is resolved in the *host's* filesystem, so it has to be the same string on
# both sides, and only `deploy/compose.yaml` knows what that is here.
ENV HEARTH_SANDBOX=container
USER ${HEARTH_UID}:${HEARTH_GID}
EXPOSE 8000
# One process, in the foreground, with the container as its supervisor. The detached
# workers it starts are its children and outlive a restart of it exactly as they do on
# a host -- that is ADR 0008's whole shape and this changes none of it.
ENTRYPOINT ["/opt/hearth/bin/python", "-m", "uvicorn", "hearth.app:from_env", "--factory"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
