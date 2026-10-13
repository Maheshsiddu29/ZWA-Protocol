#!/usr/bin/env bash
# Checks before `docker compose up`: pinned images, proving parameters,
# secrets present and private. Run from the deploy/ directory.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
test "$(docker image inspect --format '{{.Id}}' "$ZWA_ZEBRA_IMAGE")" = \
    sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185 \
    || { echo "Zebra image is not the pinned build" >&2; exit 2; }
if command -v sha256sum > /dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
(cd "$ZWA_PARAMS_DIR" && $SHA -c - > /dev/null <<'CHECKSUMS'
8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13  sapling-spend.params
2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4  sapling-output.params
CHECKSUMS
) || { echo "proving parameter checksum mismatch" >&2; exit 2; }
test "${#ZWA_PORTAL_TOKEN}" -ge 32 || { echo "ZWA_PORTAL_TOKEN is too short" >&2; exit 2; }
test -f env/web.env && test "$(stat -c %a env/web.env 2>/dev/null || stat -f %Lp env/web.env)" = 600 \
    || { echo "env/web.env must exist with mode 600" >&2; exit 2; }
grep -q '^ZWA_SESSION_SECRET=' env/web.env && grep -Eq '"role": ?"seller"' env/web.env \
    || { echo "env/web.env lacks the session secret or the dealer account" >&2; exit 2; }
docker image inspect zwa-engine:"${ZWA_ENGINE_TAG:-local}" zwa-web:"${ZWA_WEB_TAG:-local}" > /dev/null
echo "PREFLIGHT_OK"
