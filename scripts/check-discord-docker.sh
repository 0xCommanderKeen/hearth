#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
image=${HEARTH_DISCORD_TEST_IMAGE:-hearth-discord-synthetic:local}
docker build -f "$repo_root/deploy/Dockerfile" -t "$image" "$repo_root"
docker image inspect --format 'Synthetic Discord production image: {{.Id}}' "$image"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 --tmpfs /journey:rw,nosuid,nodev,mode=1777 \
  --mount "type=bind,src=$repo_root/tests,dst=/fixtures/tests,readonly" \
  --mount "type=bind,src=$repo_root/scripts,dst=/fixtures/scripts,readonly" \
  --workdir /journey --entrypoint /opt/hearth/bin/python "$image" \
  -I /fixtures/scripts/smoke-communications.py
