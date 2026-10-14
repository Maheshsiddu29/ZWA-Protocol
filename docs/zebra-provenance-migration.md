# Zebra image provenance: ARM64/AMD64 migration runbook

**Status: implemented and unit-tested locally. Not run against any AWS state.**
Every command against AWS below needs explicit owner approval. Run it first
on **new volumes restored from the paired backup**, never on the original
volumes.

## Problem

The AWS EC2 host runs the experimental Zebra regtest node on `linux/amd64`:

| | |
|---|---|
| Running image (amd64) | `sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9` |
| Original image (arm64) | `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185` |
| Pinned Zebra source | `0aef55cea41b83f17610e6ea708e59995f9e739f` |

Up to `cf7f133`, `exchange/persistent_chain.py` hardcoded the arm64 ID as
`IMAGE`. `adopt_external` wrote that ID into the AWS `chain-identity.json`,
which claims the arm64 image although amd64 runs. The engine cannot inspect a
Compose container, so nothing ever checked the claim. The AWS identity is
version 1, `management: external`, `anchorHeight: 335`, genesis
`029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`. The settled
transaction `c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6`
is in block 325, below that anchor.

## Model: three separate facts

| Fact | Where | Who establishes it | Ever rewritten? |
|---|---|---|---|
| Historical chain identity | `genesisHash`, `anchorHeight`, `anchorHash`, `configSha256` | adoption, then `checkpoint` (anchor only advances) | Never by this migration |
| Recorded image label | `image` (always the arm64 ID in version 1) | written at adoption | Never. It is historical evidence, not proof |
| Verified runtime provenance | `zebraRuntime` (optional) | host attestation + explicit `record` | Added once; never overwritten |

`status()` reports `provenance.evidence` as follows:

- `CONTAINER_INSPECTED`: docker-local, where the container image ID is checked on every call.
- `HOST_ATTESTED`: a recorded `zebraRuntime`.
- `UNVERIFIED`: the label alone.

The engine never claims the label proves the running image. Provenance is
written only by `python3 -m exchange.zebra_provenance record`, never on
start, status, checkpoint or bootstrap.

The identity stays **version 1**. `exchange/portfolio_scans.py` and the engine
image deployed at `cf7f133` both require version 1 and ignore unknown keys.
So a migrated identity is readable by the old engine, and an engine rollback
needs no identity rollback.

```json
"zebraRuntime": {
  "schema": 1,
  "imageId": "sha256:2c3f3dde…32b9",
  "platform": "linux/amd64",
  "sourceRevision": "0aef55cea41b83f17610e6ea708e59995f9e739f",
  "evidence": "HOST_ATTESTED",
  "attestedAt": 1760000000,
  "recordedImage": "sha256:540a3429…5185",
  "verifiedChain": {"genesisHash": "029f11d8…e327", "anchorHeight": 335,
                    "anchorHash": "<live hash at 335>", "observedHeight": "<tip>"},
  "migratedFromSha256": "<sha256 of the exact pre-migration file>"
}
```

Readers reject the whole identity (fail closed) if this block is present but
any of the following holds:

- it has extra or missing fields;
- its image is not the pin for its platform;
- the source revision is not the pinned one;
- the chain is docker-local;
- `recordedImage` differs from `image`;
- its genesis differs;
- its anchor is above the current durable anchor, or equal to it with another hash.

## Compatibility matrix

| Identity | Engine at `cf7f133` | Engine with this change | `record` | `revert` |
|---|---|---|---|---|
| v1 docker-local (arm64 local workflow) | reads | reads; `CONTAINER_INSPECTED` | refused (docker-local) | no-op |
| v1 external, unmigrated (AWS today) | reads | reads; `UNVERIFIED` | adds `zebraRuntime` | no-op (`NOT_RECORDED`) |
| v1 external, migrated amd64 | reads (ignores key) | reads; `HOST_ATTESTED` | no-op (`ALREADY_RECORDED`) | exact pre-migration bytes |
| migrated, later checkpointed | reads | reads; record kept | no-op | refused: restore paired backup |
| migrated, malformed `zebraRuntime` | reads (unchecked) | **refused** | refused | refused |
| `image` relabelled to amd64 | refused | refused | refused | refused |
| `detectedReset` present | reports reset | reports reset | refused | refused unless unchanged |
| version ≠ 1, bad JSON, symlink, FIFO, > 8 KiB | refused | refused | refused | refused |

`record` also refuses in each of these cases:

- a protected research root;
- an attestation that is stale (> 15 min), extra/missing-field, unpinned, or architecture-mismatched;
- an attestation for another machine platform, or one whose container runs another image or is stopped;
- `--expect-genesis` / `--expect-anchor-height` differing from the identity;
- a live chain whose genesis or anchor hash differs, or whose tip is below the anchor;
- any RPC failure;
- a different runtime already recorded;
- a concurrent change to the file.

Every refusal leaves the original bytes unchanged. The write is a temp file +
fsync + compare-and-swap + `rename` + directory fsync, keeping mode and owner.
A crash can leave at most a `.chain-identity.*.provenance-tmp` file and never
a partial identity.

## Trust boundary

`deploy/scripts/zebra-runtime-attest.py` runs **on the host** with the
operator's Docker access:

- `check-image` (used by `preflight.sh`): the configured tag resolves to a pinned ID, the image's OS/architecture match that pin, and the Docker host is natively that platform (no emulation).
- `attest`: additionally checks that the project's single `zebra` container (by Compose labels) runs exactly that ID and is running. It prints the attestation.

The engine side rechecks the attestation against its own pin table and its
own machine architecture, and checks genesis/anchor over the internal RPC. No
container mounts `docker.sock` (a test enforces it). A host attestation is
evidence of what ran at `attestedAt`. It is not continuous monitoring, so
re-run `attest` after every Zebra restart or image change.

## AWS preflight local patch

The AWS checkout has a local edit to `deploy/scripts/preflight.sh` (the amd64
ID). The new preflight accepts both pins, so that patch is superseded. **Do
not** overwrite it blindly. Preserve it first:

```sh
cd <AWS checkout>
git status --porcelain
git rev-parse HEAD                                   # expect cf7f133f0bb34cd4a4add8a87fba06273ba90a8f
git diff -- deploy/scripts/preflight.sh > ~/zwa-aws-preflight-local.patch
cp -p deploy/scripts/preflight.sh ~/preflight.sh.aws-local
sha256sum ~/zwa-aws-preflight-local.patch ~/preflight.sh.aws-local
```

Never run `git checkout -- .`, `git reset --hard` or `git stash` in that
checkout. Stage from a **separate** checkout of the reviewed commit, as below.
Retire the local patch only after the owner compares it with the new file.

## Stage on new restored volumes (AWS host or a separate isolated instance)

Prerequisites:

- the reviewed commit is available on the host (owner push, or `git bundle`);
- a stopped-point paired backup of `zwa-root`, `zebra-state` and `zwa-params` exists, with checksums;
- an engine image containing this change (see step 2).

Nothing below touches project `zwa-demo` or its volumes.

```sh
set -euo pipefail
P=zwa-prov-stage                                   # isolated Compose project, never zwa-demo
BACKUP=/srv/zwa-backups/<stopped-point>            # paired archives: zwa-root.tar zebra-state.tar zwa-params.tar
STAGE=$HOME/zwa-prov-stage-evidence; mkdir -m 700 -p "$STAGE"
STAGE_TAG=provenance-<reviewed short sha>

# 1. Separate checkout; reuse the live configuration read-only.
git clone --no-checkout <repo or bundle> ~/zwa-provenance-stage
git -C ~/zwa-provenance-stage checkout --detach <reviewed sha>
cd ~/zwa-provenance-stage/deploy
install -m 600 <AWS checkout>/deploy/.env .env
mkdir -m 700 env && install -m 600 <AWS checkout>/deploy/env/web.env env/web.env
set -a; . ./.env; set +a

# 2. Engine image with this change. Preferred: full rebuild per deploy/README.md.
#    A staging overlay on the deployed engine image changes only two files:
printf 'FROM zwa-engine:%s\nCOPY exchange/persistent_chain.py exchange/zebra_provenance.py /app/ZWA-Brand-Integration/exchange/\n' \
  "${ZWA_ENGINE_TAG:-local}" > "$STAGE/Dockerfile.overlay"
docker build -f "$STAGE/Dockerfile.overlay" -t "zwa-engine:$STAGE_TAG" ..
#    The overlay is only valid if the deployed image's other exchange files match cf7f133:
docker run --rm --network none --entrypoint sh "zwa-engine:${ZWA_ENGINE_TAG:-local}" -c \
  'cd /app/ZWA-Brand-Integration && sha256sum exchange/*.py' > "$STAGE/deployed-exchange.sha256"
(cd .. && for f in $(git ls-tree --name-only cf7f133 exchange/ | grep '\.py$' | grep -v '/test_'); do
   printf '%s  %s\n' "$(git show cf7f133:$f | sha256sum | cut -d' ' -f1)" "$f"; done) > "$STAGE/cf7f133-exchange.sha256"
diff "$STAGE/cf7f133-exchange.sha256" "$STAGE/deployed-exchange.sha256"   # must be empty
export ZWA_ENGINE_TAG=$STAGE_TAG

# 3. Host checks: pinned amd64 image on a native amd64 host.
python3 -I scripts/zebra-runtime-attest.py check-image --image "$ZWA_ZEBRA_IMAGE"
./scripts/preflight.sh

# 4. Verify the paired backup, then restore it into NEW volumes (archives mounted read-only).
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
test -z "$(docker volume ls -q --filter "name=^${P}_")"            # must not exist yet
for v in zwa-root zebra-state zwa-params; do
  docker volume create --label com.docker.compose.project="$P" --label com.docker.compose.volume="$v" "${P}_$v"
  docker run --rm --network none --user 0:0 --entrypoint tar -v "${P}_$v:/dst" -v "$BACKUP:/backup:ro" \
    "zwa-engine:$STAGE_TAG" --numeric-owner -xpf "/backup/$v.tar" -C /dst
done
(cd "$BACKUP" && sha256sum -c SHA256SUMS)                           # backup unchanged

# 5. Start only Zebra (internal network, no published ports). No writers, web or caddy.
docker compose -p "$P" up -d --no-deps zebra
ENGINE="docker compose -p $P run --rm --no-deps -T --entrypoint python3 portal"
$ENGINE -m exchange.zebra_provenance show --root /var/lib/zwa/zwa4e-demo | tee "$STAGE/show-before.json"
#    Expect genesis 029f11d8…e327, anchorHeight 335, provenance.evidence UNVERIFIED.
$ENGINE -c 'from exchange.persistent_chain import _rpc as r
t = r(18232, "getrawtransaction", ["c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6", 1], host="zebra")
print(t.get("height"), t.get("confirmations"), t.get("blockhash"), r(18232, "getblockhash", [325], host="zebra"))' \
  | tee "$STAGE/settled-tx.txt"                                     # expect height 325 and matching block hash
$ENGINE - < scripts/m10-state-report.py > "$STAGE/state-before.json"
docker compose -p "$P" run --rm --no-deps -T --entrypoint sh portal -c \
  'cd /var/lib/zwa && find . -type f ! -name chain-identity.json -print0 | sort -z | xargs -0 sha256sum' \
  > "$STAGE/files-before.sha256"
docker compose -p "$P" run --rm --no-deps -T --entrypoint cat portal /var/lib/zwa/zwa4e-demo/chain-identity.json \
  > "$STAGE/chain-identity.pre.json"

# 6. Attest on the host, then dry run, then record.
python3 -I scripts/zebra-runtime-attest.py attest --image "$ZWA_ZEBRA_IMAGE" --project "$P" > "$STAGE/attestation.json"
REC="$ENGINE -m exchange.zebra_provenance record --root /var/lib/zwa/zwa4e-demo --rpc-host zebra \
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327 --expect-anchor-height 335"
$REC --dry-run < "$STAGE/attestation.json" | tee "$STAGE/dry-run.json"
$REC           < "$STAGE/attestation.json" | tee "$STAGE/record.json"
$REC           < "$STAGE/attestation.json" | tee "$STAGE/record-again.json"   # expect ALREADY_RECORDED

# 7. Verify.
$ENGINE -m exchange.zebra_provenance show --root /var/lib/zwa/zwa4e-demo | tee "$STAGE/show-after.json"
sha256sum "$STAGE/chain-identity.pre.json"          # equals zebraRuntime.migratedFromSha256
docker compose -p "$P" run --rm --no-deps -T --entrypoint sh portal -c \
  'cd /var/lib/zwa && find . -type f ! -name chain-identity.json -print0 | sort -z | xargs -0 sha256sum' \
  > "$STAGE/files-after.sha256"
diff "$STAGE/files-before.sha256" "$STAGE/files-after.sha256"     # must be empty
$ENGINE -c 'from pathlib import Path; from exchange import persistent_chain as c
print(c.status(Path("/var/lib/zwa/zwa4e-demo"), "zebra", 18232, host="zebra", managed="external"))'
$ENGINE - < scripts/m10-state-report.py > "$STAGE/state-after.json"
diff "$STAGE/state-before.json" "$STAGE/state-after.json"           # must be empty
#    Negative drills: each must print PROVENANCE_REFUSED and change nothing.
$ENGINE -m exchange.zebra_provenance record --root /var/lib/zwa/zwa4e-demo --rpc-host zebra \
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327 --expect-anchor-height 334 \
  < "$STAGE/attestation.json" || true
sed 's/2c3f3dde/2c3f3ddf/g' "$STAGE/attestation.json" | $REC || true

# 8. Rehearse rollback on the staging copy, then record again if desired.
$ENGINE -m exchange.zebra_provenance revert --root /var/lib/zwa/zwa4e-demo --dry-run
$ENGINE -m exchange.zebra_provenance revert --root /var/lib/zwa/zwa4e-demo
docker compose -p "$P" run --rm --no-deps -T --entrypoint cat portal /var/lib/zwa/zwa4e-demo/chain-identity.json \
  | cmp - "$STAGE/chain-identity.pre.json"                          # identical bytes

# 9. Stop staging. Keep its volumes until the owner reviews the evidence.
docker compose -p "$P" down                                         # never `down -v`; never touch zwa-demo
```

Steps 5 and 7 open the ledger read-only through the portal's report. A
`-wal`/`-shm` file may appear. That is why file hashes are taken directly
around the migration only. The `m10-state-report.py` comparison must match
exactly. Zebra on a restored volume may drop up to 99 recent blocks; the
anchor (335) and block 325 must survive. If not, stop: the backup or restore
is not sound.

## Original AWS state (separate owner approval, after staging passes)

Not part of this change and not executed:

1. Pause sales.
2. Stop `caddy`, `web`, `portal`, `order-service` and `demo-signer`, so no writer can checkpoint during the migration. Then stop `zebra`.
3. Take and checksum a fresh paired backup.
4. Start only `zebra`.
5. Run steps 3 and 5–7 with `P=zwa-demo` and the deployed engine replaced by the reviewed image.
6. Restart the services and check `/readyz`, `docker compose ps`, and that a public read shows the same balances.

The compare-and-swap aborts if any writer modified the identity anyway.

## Rollback (preserves the original paired backup)

The original backup archives are only ever mounted read-only and verified
with `sha256sum -c` before and after every use. Never restore onto them or
onto live volumes.

- **Engine only:** set `ZWA_ENGINE_TAG` back to the previous image and
  `docker compose up -d`. The old engine reads the migrated identity
  unchanged. A test reproduces the `cf7f133` reader.
- **Identity, nothing changed since migration:** run
  `python3 -m exchange.zebra_provenance revert` in an engine one-shot. It
  restores the exact pre-migration bytes only if they hash to the recorded
  `migratedFromSha256`. Compare with `chain-identity.pre.json`.
- **Identity, chain moved on** (`revert` refuses after a checkpoint): either
  keep the record (harmless to both engines), or restore the paired
  pre-migration backup into **new** volumes in an isolated project, verify it
  with `m10-state-report.py` as in staging, and only then switch service to
  it. Never combine archives from different points in time.
- **Preflight:** restore `~/preflight.sh.aws-local` if the owner chooses.
