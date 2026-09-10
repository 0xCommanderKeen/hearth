# Hearth on a server

Everything in this folder is one deployment: Hearth's own image, the image a run's
session executes in, the compose file that ties them together, and the packet filter
that makes the sandbox network what ADR 0016 says it is. Nothing here is tied to a
machine. Any Linux host with a container runtime is a burrow.

The decisions are `docs/adr/0016-sandbox-per-run.md`; what was measured, and where a
measurement contradicted the decision, is `docs/sandbox.md`. This page is the order to
do things in.

| file | what it is |
| --- | --- |
| `Dockerfile` | Hearth itself: the release wheel, the locked dependencies, a container client |
| `Dockerfile.sandbox` | the sandbox: both pinned CLIs, Hearth's bridge shim and fence probe, no package manager, no root |
| `compose.yaml` | the deployment: volumes, networks, the runtime socket, the fence's configuration |
| `.env.example` | every value the compose file wants, with what each one is for |
| `fence.sh` | the packet filter that makes `hearth-egress` the sandbox's only network |

**What is not here.** No credential, ever — not in an image layer, not in the compose
file, not in `.env`. A login is a directory an operator seeds by hand with that
provider's own login flow, on a volume, and Hearth never creates or copies one.

---

## 1. Build and pin the two images

The sandbox image carries the provider CLIs, and **nothing Hearth ships downloads
them**: an image that fetches its own tools cannot be the same image twice, and the
whole point of the pin is that it can. So they are copied in from the build context, as
the *Linux* builds for this host's architecture — a macOS build of either will hash
correctly and refuse to execute.

```sh
mkdir -p binaries          # gitignored; these are large and are not this repository's
# Codex: its package ships four things and the CLI finds them by their place in that
# layout, so all four go in (`docs/sandbox.md`, measurement 8).
npm pack "@openai/codex@<version>-linux-<arch>"
tar xzf openai-codex-*.tgz
cp package/vendor/*/bin/codex                  binaries/codex
cp package/vendor/*/bin/codex-code-mode-host   binaries/codex-code-mode-host
cp -R package/vendor/*/codex-resources         binaries/codex-resources
cp -R package/vendor/*/codex-path              binaries/codex-path
# Claude: the Linux build of the release the store is pinned to. Hash what you
# downloaded against that release's own manifest before building with it.
cp /path/to/claude-<version>-linux-<arch>      binaries/claude

docker build -f deploy/Dockerfile.sandbox \
    --build-arg HEARTH_UID="$HEARTH_UID" --build-arg HEARTH_GID="$HEARTH_DOCKER_GID" \
    -t hearth/sandbox:<version> .
```

`HEARTH_UID` is load-bearing twice over: it is the uid every sandbox runs as, which is
what keeps the bridge's peer-credential check meaningful across the boundary, and it
must exist in the image's own passwd file or the Claude CLI ends before its first byte
(measurement 11). An image built for one uid and run as another fails every Claude run
in it. `HEARTH_DOCKER_GID` is the group that owns the runtime socket, because that is
the gid Hearth's own container runs with and therefore the gid it passes to `--user`.

Hearth's own image is built from the repository, and it takes the release wheel from
the build context because that wheel is what holds the built browser:

```sh
make check                 # builds web/dist into the package and the release wheel
docker build -f deploy/Dockerfile \
    --build-arg HEARTH_UID="$HEARTH_UID" --build-arg HEARTH_GID="$HEARTH_DOCKER_GID" \
    -t hearth:<version> .
```

**Pin both by digest.** A locally built image has no repository digest at all — that
only exists once an image has been pushed somewhere — so push both to whatever registry
this host reads from and pin what came back:

```sh
docker push <registry>/hearth/sandbox:<version>
docker image inspect --format '{{index .RepoDigests 0}}' <registry>/hearth/sandbox:<version>
```

A `registry:2` container on the loopback is enough and nothing leaves the machine.
Put the two digests in `deploy/.env` as `HEARTH_IMAGE` and `HEARTH_SANDBOX_IMAGE`.
Hearth refuses a tag: `sandbox_configuration_invalid`.

## 2. Configure

```sh
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
```

`.env.example` says what each value is for. Three deserve attention here.

**`HEARTH_UID` / `HEARTH_DOCKER_GID`** must be the pair the sandbox image was built
for and the group that owns this host's runtime socket. On Docker Desktop that socket
is `root:root` inside a container, so the gid is `0`; on most Linux hosts it is the
`docker` group.

**`HEARTH_VOLUME_ROOT`** is this daemon's volume directory
(`docker info --format '{{.DockerRootDir}}'` plus `/volumes`). Every volume is mounted
inside Hearth at the path it has on the host, because the paths Hearth hands the daemon
— a run's bridge socket, a login directory, a granted folder — are resolved in the
*host's* filesystem, not in Hearth's. A path that means one thing inside Hearth's
container and another to the daemon is a mount of the wrong directory.

**`HEARTH_SANDBOX_SHUT` / `HEARTH_SANDBOX_OPEN`** are the fence, below.

## 3. Create the networks and install the fence

`hearth-egress` is the operator's own: no service in the compose file is attached to
it, which is exactly the point, so nothing in that file can create it either. Make it
first, with the subnet the fence will be keyed to, and let compose make its own:

```sh
docker network create --subnet "$HEARTH_EGRESS_SUBNET" hearth-egress
docker compose --env-file deploy/.env -f deploy/compose.yaml create
```

That leaves `hearth` (Hearth's own; nothing else is on it) beside it. Then install the
filter that makes `hearth-egress` a fence, as root on the Docker host:

```sh
HEARTH_EGRESS_SUBNET=172.31.240.0/24 deploy/fence.sh apply
deploy/fence.sh show
```

It drops every private destination from the sandbox subnet — RFC 1918, link-local and
the carrier-grade range — in `DOCKER-USER`, and the host's own addresses in `INPUT`,
which forwarded traffic never passes through. Everything else is left alone, which is
the public internet, which is the provider.

**These rules are not persistent.** `DOCKER-USER` is rebuilt when the daemon restarts,
so run `fence.sh apply` from whatever this host uses to restore firewall state at boot
(`iptables-persistent`, a `systemd` unit ordered after `docker.service`, your
configuration manager). Hearth does not depend on you remembering: it measures the
fence at every start and will not open on an open one.

**On Docker Desktop** the rules live inside its Linux VM, and the way in is a
privileged container on the host network. Its embedded resolver also forwards name
lookups from inside the container's own namespace, so DNS has to be let through
explicitly or every provider name answers `unresolved` (measured):

```sh
docker run --rm --privileged --network host \
    -v "$PWD/deploy/fence.sh:/fence.sh:ro" \
    -e HEARTH_EGRESS_SUBNET=172.31.240.0/24 \
    -e HEARTH_EGRESS_RESOLVER=192.168.65.7 \
    alpine:3 sh -c 'apk add --no-cache iptables iptables-legacy >/dev/null && sh /fence.sh apply'
```

The resolver address is the `ExtServers` line in any container's `/etc/resolv.conf`.
Docker Desktop's VM does not keep these rules across a restart of Docker Desktop
itself; on a server they are ordinary firewall state.

**What this fence is, exactly.** "Nothing of this house" — not "the provider and
nothing else". The providers are behind CDNs whose addresses rotate, so an allowlist of
their addresses is a fence that breaks on somebody else's deploy; saying it exactly
needs an egress proxy the CLIs are pointed at, which this does not build.
`docs/sandbox.md` and ADR 0016's Measured section both say so in these words. What
Hearth measures is what is claimed and no more.

## 4. Seed the binaries and the logins

Both volumes exist after step 3. Copy the pinned CLIs onto `hearth-binaries` — the same
files the sandbox image was built from, because the image's own copies are hashed
against the store's pin at every start:

```sh
# `--user 0:0` only here: a fresh volume belongs to root and this is the one write
# that has to happen before `init` hands it over.
docker run --rm --user 0:0 -v hearth-binaries:/binaries -v "$PWD/binaries:/in:ro" \
    --entrypoint /bin/cp "$HEARTH_IMAGE" /in/codex /in/claude /binaries/
docker run --rm --user 0:0 -v hearth-binaries:/binaries \
    --entrypoint /bin/chmod "$HEARTH_IMAGE" 0555 /binaries/codex /binaries/claude
```

Then the household's logins, one directory per provider, on `hearth-credentials`.
**Hearth never creates a login and never copies one**: point the CLI's own
configuration path at the directory and run its own login flow. On a server with no
browser, Codex offers `login --device-auth` (and `--with-api-key` /
`--with-access-token`, each reading the secret from stdin); the pinned Claude build has
no device-code flag, so its browser flow has to be completed somewhere that has one,
with a forwarded port, or the resulting `.credentials.json` placed in the directory by
hand.

```sh
# One shell inside the volume, with the pinned CLI and the login directory both on it.
docker run --rm -it \
    -v hearth-credentials:/credentials -v hearth-binaries:/binaries:ro \
    -e CODEX_HOME=/credentials/codex_subscription \
    --entrypoint /binaries/codex "$HEARTH_SANDBOX_IMAGE" login --device-auth
```

A resident may have a login of its own instead, at
`<data>/credentials/<resident id>/<kind>/` on the store volume — same flow, different
directory. `docs/sandbox.md`, *Whose login a run spends*, is the whole of it. Check
what is seeded without reading anything secret:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml \
    run --rm --entrypoint /opt/hearth/bin/python hearth -I -m hearth credentials \
    --data /var/lib/docker/volumes/hearth-store/_data
```

## 5. First start

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f hearth
```

The first start pins this store to the sandbox image's digest (`sandbox.configured`),
hashes the CLIs inside that image against the store's binary pins, measures the fence
and records what it saw (`sandbox.fence`). Every one of those refuses by name rather
than by a failed run later:

| refusal | what to fix |
| --- | --- |
| `sandbox_configuration_invalid` | the image is not digest-pinned, or a name is not a name |
| `sandbox_runtime_unavailable` | the daemon did not answer — the socket, `HEARTH_SANDBOX_DOCKER_HOST` |
| `sandbox_image_unavailable` | this daemon does not hold that image; nothing is ever pulled |
| `sandbox_network_missing` | `hearth-egress` was not created — step 3 |
| `sandbox_image_changed` | this store is pinned to a different image; see *Upgrade* |
| `sandbox_binary_mismatch` | the image's CLI is not the one this store's binary pin names |
| `sandbox_fence_unconfigured` | `HEARTH_SANDBOX_SHUT` or `HEARTH_SANDBOX_OPEN` is empty |
| `sandbox_fence_unmeasured` | the probe in the image could not answer — an image too old to carry it |
| `sandbox_network_open` | the fence does not hold; the audit says which address answered |

Then:

```sh
curl -s localhost:8000/health
curl -s -H "Authorization: Bearer $HEARTH_OPERATOR_TOKEN" localhost:8000/api/health
```

`/health` names the launcher and the image digest — bytes, not this machine — and
nothing else. Everything about this building is behind the operator's token:
`/api/health` names the network, the fence as measured on that very ask, every runtime
this instance could not open and every resident whose own login has lapsed.

## 6. Upgrade

A new image digest is a deliberate act, and Hearth refuses to start on one it was not
told about. The order is: new digest ⇒ quiet store ⇒ restart.

```sh
# 1. Build and push the new image, and read its digest (step 1).
# 2. Quiet the store: pause every resident and let the in-flight runs settle. A
#    detached worker outlives its Hearth by design, and a container it started is not
#    a stray while its worker is alive.
curl -s -H "Authorization: Bearer $HEARTH_OPERATOR_TOKEN" localhost:8000/api/state \
    | grep -c '"status": "running"'      # zero before going on
# 3. Take a backup (below). An upgrade you cannot undo is not an upgrade.
# 4. Point the store at the new digest and restart.
$EDITOR deploy/.env                      # HEARTH_SANDBOX_IMAGE, HEARTH_IMAGE
docker run --rm -v hearth-store:/store --entrypoint /opt/hearth/bin/python "$HEARTH_IMAGE" \
    -I -c 'import sqlite3,sys
db = sqlite3.connect("/store/hearth.db")
db.execute("UPDATE system_meta SET value=? WHERE key=?", (sys.argv[1], "sandbox_image"))
db.commit()' "sha256:<the new digest>"
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
```

**That fourth step is a rough edge and is meant to be read as one.** Hearth has no
command for re-pinning an image, so an operator writes the new digest into
`system_meta.sandbox_image` by hand, with the store quiet — which is why the backup
comes first. The refusal is the part that is designed: a store never accepts a different
sandbox without somebody saying so.

Rolling back is the same four steps with the old digest, and the store's schema is the
thing to watch: it upgrades forward on start and does not come back down, so a rollback
across a schema change is a *restore*, not a redeploy.

The store's own schema upgrades forward on start and needs no fresh data directory,
ever (`docs/adr/0013-forward-schema-upgrades.md`): an older store is rebuilt in place
with the original kept beside it.

## 7. Backup and restore

The format is the household — `hearth.db`, `artifacts/`, resident `memory/` and the
archived journal entries under it — and never a credential, not even the per-resident
logins that live under the data directory itself. `docs/backup-restore.md` is the whole
of it; here it is against a running deployment:

```sh
compose="docker compose --env-file deploy/.env -f deploy/compose.yaml"
store=/var/lib/docker/volumes/hearth-store/_data

# Capture. It refuses while the executor is busy or a run is priced but not settled;
# retry later rather than forcing it.
$compose run --rm -v hearth-backups:/backups --entrypoint /opt/hearth/bin/python hearth \
    -I -m hearth backup --data "$store" --destination /backups/$(date +%Y-%m-%d)
$compose run --rm -v hearth-backups:/backups --entrypoint /opt/hearth/bin/python hearth \
    -I -m hearth verify-backup --source /backups/<name>
```

**Between hosts, and between launchers.** A backup is the household, not the machine
it was on: the same store restored on a Mac runs on the `process` launcher and on a
server runs on `container`, and the forward upgrade holds either way. Copy the backup
directory across (`docker cp`, `rsync`, anything that preserves bytes) and restore it
into a directory of its own:

```sh
python -m hearth restore --source /path/to/backup --destination /path/to/new-store
```

A restored copy is **held**: the restore sets a durable `restore_hold`, changes the
observation epoch, refuses every ordinary mutation and starts no supervision. That is
deliberate and there is no activation command — a copied store must not go on claiming
work the original is still doing. Production recovery means an ownership
reconciliation plan and real host checks first. A held copy still reports the launcher
it was opened with, pins nothing and measures no fence: it starts no session, so it has
no network to fence.

## 8. Where a resident's folders live

A grant's `mounts` are host paths, and Hearth's own data directory, every login
directory and the runtime socket are refused to every grant, at write time. So a
household's folders go on a volume of their own — `hearth-folders` in the compose file,
or any other volume added beside it, always mounted at the path it has on the host:

```yaml
volumes:
  - hearth-photos:/var/lib/docker/volumes/hearth-photos/_data
```

and granted by that path, `ro` unless the resident is meant to write. What a run
reached is on the run itself, and a writable folder that was really written is audited
as `run.mount_rw_used`.
