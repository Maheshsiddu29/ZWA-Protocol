# Zebra image provenance: ARM64/AMD64 migration runbook

**Status: implemented and unit-tested locally. Never run against AWS state.**
Every AWS command below needs explicit owner approval. It runs first on **new
volumes restored from a paired backup, in a separate Compose project**. The
original AWS volumes, containers and backups are never modified.

## Problem

The AWS EC2 host runs the experimental Zebra regtest node on `linux/amd64`:

| | |
|---|---|
| Running image (amd64) | `sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9` |
| Original image (arm64) | `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185` |
| Declared Zebra source | `0aef55cea41b83f17610e6ea708e59995f9e739f` |

Up to `cf7f133`, `exchange/persistent_chain.py` hardcoded the arm64 ID as
`IMAGE`. `adopt_external` wrote that ID into the AWS `chain-identity.json`,
which claims the arm64 image although amd64 runs. The AWS identity has these values:

- version 1, `management: external`;
- `anchorHeight: 335`;
- genesis `029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`.

The settled transaction
`c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6` is in
block 325, below the anchor.

### What the image IDs do and do not prove

An image ID is the digest of the image's configuration. It identifies image
*content* exactly, but it does not prove which source built that content.
There is no reproducible-build evidence, recorded build log or signed
provenance linking either image to `0aef55ce…`. The source revision is the
owner's declaration, and records store it as `declaredSourceRevision`.

The AMD64 image `2c3f3dde…` was built on or for the AWS host; its build
inputs were not captured in this repository and it was not available to the
reviewers. Treat its source as declared, not verified. Closing that gap
needs a separate reproducible rebuild (same source, toolchain and Dockerfile)
that yields the same ID, or an attested build record.

## Model: three separate facts

| Fact | Where | Established by | Ever rewritten? |
|---|---|---|---|
| Historical chain identity | `genesisHash`, `anchorHeight`, `anchorHash`, `configSha256` | adoption, then `checkpoint` (anchor only advances) | Never by this migration |
| Recorded image label | `image` (the arm64 ID in every version-1 identity) | adoption | Never. Historical evidence, not proof |
| Point-in-time runtime evidence | `zebraRuntime` (optional) | host attestation + explicit `record` | Added once; never changed by a later attestation |

`zebraRuntime` (schema 2; schema 1 was never deployed and is rejected):

```json
"zebraRuntime": {
  "schema": 2,
  "imageId": "sha256:2c3f3dde…32b9",
  "platform": "linux/amd64",
  "declaredSourceRevision": "0aef55cea41b83f17610e6ea708e59995f9e739f",
  "evidence": "POINT_IN_TIME_HOST_ATTESTATION",
  "attestedAt": 1760000000,
  "containerId": "<64-hex container the host inspected>",
  "composeProject": "<the project it belonged to>",
  "recordedImage": "sha256:540a3429…5185",
  "verifiedChain": {"genesisHash": "029f11d8…e327", "anchorHeight": 335,
                    "anchorHash": "<live hash at 335>", "observedHeight": "<tip>"},
  "migratedFromSha256": "<sha256 of the exact pre-migration file>"
}
```

It records that **one named container in one Compose project ran this pinned
image at `attestedAt`**. It does not establish which image runs now: Zebra may
since have been restarted, recreated or re-tagged. `status()` and `show`
therefore report one of three evidence values:

| `evidence` | `currentRuntimeVerified` | Meaning |
|---|---|---|
| `CONTAINER_INSPECTED` | `true` | docker-local only: this call inspected the running container |
| `POINT_IN_TIME_HOST_ATTESTATION` | `false` | a record exists; the current container is not verified by the engine |
| `LABEL_ONLY` | `false` | only the recorded `image` label; proves nothing about the runtime |

After **every** Zebra start, restart, recreation or image change, verify the
runtime afresh on the host. The record stays as it is: a later attestation of a
replaced container is reported as `sameContainerAsRecord: false` and is never
written into the identity.

```sh
python3 -I deploy/scripts/zebra-runtime-attest.py verify --image "$ZWA_ZEBRA_IMAGE" --project zwa-demo \
  [--docker-sudo] [--recorded-show show.json]
```

Readers reject the whole identity (fail closed) when a `zebraRuntime` block
is present and malformed, in any of these ways:

- extra or missing fields;
- an image that is not the pin for its platform;
- another declared source;
- evidence other than `POINT_IN_TIME_HOST_ATTESTATION`;
- a malformed container ID or project;
- a docker-local chain;
- `recordedImage` different from `image`;
- another genesis;
- an anchor above the current one, or at the current height with a different hash.

The identity stays **version 1**. `exchange/portfolio_scans.py`, the website
reader and the engine deployed at `cf7f133` all require version 1 and ignore
unknown keys. So an engine rollback needs no identity rollback.

## Compatibility matrix

| Identity | Engine at `cf7f133` | Engine with this change | `record` | `revert` |
|---|---|---|---|---|
| v1 docker-local (arm64 local workflow) | reads | reads; `CONTAINER_INSPECTED` | refused | no-op |
| v1 external, unmigrated (AWS today) | reads | reads; `LABEL_ONLY` | adds `zebraRuntime` | no-op |
| v1 external, migrated | reads (ignores key) | reads; `POINT_IN_TIME_HOST_ATTESTATION` | no-op (`ALREADY_RECORDED`, re-fsyncs) | exact pre-migration bytes |
| migrated, later checkpointed | reads | reads; record kept | no-op | refused: restore the paired backup |
| malformed `zebraRuntime` | reads (unchecked) | **refused** | refused | refused |
| `image` relabelled to amd64 | refused | refused | refused | refused |
| `detectedReset` present | reports reset | reports reset | refused | refused unless unchanged |
| version ≠ 1, bad JSON, symlink, FIFO, > 8 KiB | refused | refused | refused | refused |

`record` also refuses, before any chain read or write:

- a protected research root;
- an attestation that is stale (> 15 min), malformed, unpinned or architecture-mismatched;
- an attestation for another machine platform, or one whose container runs another image or is stopped;
- an attestation for **another Compose project** than `--expect-compose-project`;
- an identity that differs from `--expect-genesis` / `--expect-anchor-height`.

It then also refuses, still without writing:

- a live chain whose genesis or anchor differs, or whose tip is below the anchor;
- any RPC failure;
- a different runtime already recorded.

## Write guarantees and the writer-quiescence boundary

The identity is replaced with a complete temp file plus fsync, then a
`rename`, then a directory fsync. Mode and owner are preserved. Readers
therefore see the old or the new file, never a partial one.

**Coordination.** `persistent_chain.identity_lock` is an advisory `flock` on
the sidecar `.chain-identity.lock` (an empty file, mode 0600). These writers
hold it for their whole read-modify-write:

- `start`, `adopt_external` and `checkpoint`;
- `status` when it records a reset;
- `zebra_provenance record` and `revert`.

So among these writers no update can be lost. For example, a checkpoint
that arrives during a migration waits, then applies on top of it (tested).
`status` will not record a stale reset over an identity a checkpoint moved on.

**Boundary.** A process running code from before the lock exists, such as the
`cf7f133` engine, does not take it and is **not** excluded. `record`
re-compares the bytes just before the rename and refuses if they changed. But
a non-cooperating write that lands after that comparison can be overwritten,
or can overwrite the migration. That is reported as `UNCERTAIN` if
the read-back sees it. Therefore, during a migration:

- **Required:** no `order-service`, `demo-signer`, `portal`, `web` or `caddy` container of the target project runs. Only `zebra` runs. The staging driver guarantees this by starting only `zebra` in a new project.
- The engine one-shot that runs `record` must use an image containing the lock.

`record` waits at most 10 s for the lock, then refuses without writing.

## Outcomes and reconciliation

| Exit | Outcome | On disk | Do |
|---|---|---|---|
| 0 | `WRITTEN` / `ALREADY_RECORDED` / `DRY_RUN` | as reported, durable | continue |
| 1 | `PROVENANCE_REFUSED` | **unchanged** (a refusal happens before the rename) | read the reason |
| 3 | `PROVENANCE_UNCERTAIN` | the rename happened, but durability or read-back is unconfirmed | reconcile, **never revert blindly** |

To reconcile an exit 3:

1. Keep the pre-migration backup and `chain-identity.pre.json` untouched.
2. Run `show`. Compare `sha256` with the reported `after` (migration applied) and `before` (not applied).
3. If it equals `after`, re-run the **same** `record` command. It returns `ALREADY_RECORDED, durable: true` after re-fsyncing the file and directory. If it exits 3 again, the storage is failing: stop and investigate the disk.
4. If it equals neither, a non-cooperating writer is active. Stop every engine service, then compare with the paired backup before doing anything else.
5. After a power loss with an uncertain write, either the old or the new identity is on disk; both are valid. Repeat from step 2.

## Staging on the existing EC2 host

On the existing EC2 host, these facts hold:

- the live checkout uses a host-local `deploy/compose.amd64.yaml` override;
- `deploy/scripts/preflight.sh` has a local amd64 edit;
- the paired backups are `~/zwa-recovery/paired-*/` with `zwa-demo_<volume>.tar.gz` archives.

`deploy/scripts/provenance-stage.py` does the staging. Each step validates
its inputs and stops at the first unexpected result.

### 0. Variables (validated before use)

```sh
set -euo pipefail
LIVE="$HOME/ZWA-Protocol"                             # live checkout: read only below
STAGE_CHECKOUT="$HOME/zwa-provenance-stage"           # new, separate checkout
REVIEWED="REPLACE_WITH_40_HEX_REVIEWED_COMMIT"
BACKUP="$HOME/zwa-recovery/REPLACE_WITH_paired-DIRECTORY_NAME"
EVIDENCE="$HOME/zwa-prov-stage-evidence/$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$REVIEWED" =~ ^[0-9a-f]{40}$ ]] || { echo "REVIEWED is not a commit id" >&2; exit 1; }
[[ "$(basename "$BACKUP")" =~ ^paired-[A-Za-z0-9._-]+$ && -d "$BACKUP" && ! -L "$BACKUP" ]] \
  || { echo "BACKUP is not a ~/zwa-recovery/paired-* directory" >&2; exit 1; }
[[ ! -e "$STAGE_CHECKOUT" && ! -e "$EVIDENCE" ]] || { echo "staging paths already exist" >&2; exit 1; }
test -f "$LIVE/deploy/compose.amd64.yaml" && test -f "$LIVE/deploy/.env"
mkdir -p -m 700 "$(dirname "$EVIDENCE")"
# Docker access: plain if the user may use the socket, otherwise sudo (prompt once now).
if docker info > /dev/null 2>&1; then D=(docker); SUDO_FLAG=()
else sudo -v; D=(sudo -n docker); SUDO_FLAG=(--docker-sudo); fi
"${D[@]}" info --format '{{.OSType}}/{{.Architecture}}'      # expect linux/x86_64
```

### 1. Preserve AWS-local files (read only)

```sh
KEEP="$HOME/zwa-aws-local-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$KEEP"
git -C "$LIVE" rev-parse HEAD                     # record it; expect cf7f133f0bb34cd4a4add8a87fba06273ba90a8f
git -C "$LIVE" status --porcelain | tee "$KEEP/live-status.txt"
git -C "$LIVE" diff -- deploy/scripts/preflight.sh > "$KEEP/preflight-local.patch"
cp -p "$LIVE/deploy/scripts/preflight.sh" "$LIVE/deploy/compose.amd64.yaml" "$KEEP/"
sha256sum "$KEEP"/* | tee "$KEEP/SHA256SUMS"
```

Never run `git checkout -- .`, `git reset --hard`, `git stash`, `git pull` or
`docker compose down` in the live checkout for this work. The new preflight
accepts both pins, which supersedes the local preflight edit. Retire that edit
only after the owner compares it with the new file.

### 2. Separate checkout of the reviewed commit

The commit arrives by an owner push, or as a bundle made by the owner
(`git bundle create zwa-provenance.bundle cf7f133..<branch>`).

```sh
git clone --no-checkout "$LIVE" "$STAGE_CHECKOUT"
git -C "$STAGE_CHECKOUT" bundle verify "$HOME/zwa-provenance.bundle"     # if using a bundle
git -C "$STAGE_CHECKOUT" fetch "$HOME/zwa-provenance.bundle" "+refs/heads/*:refs/remotes/bundle/*"
git -C "$STAGE_CHECKOUT" checkout --detach "$REVIEWED"
test "$(git -C "$STAGE_CHECKOUT" rev-parse HEAD)" = "$REVIEWED"
install -m 644 "$LIVE/deploy/compose.amd64.yaml" "$STAGE_CHECKOUT/deploy/compose.amd64.yaml"
install -d -m 700 "$STAGE_CHECKOUT/deploy/env"
install -m 600 "$LIVE/deploy/env/web.env" "$STAGE_CHECKOUT/deploy/env/web.env"
cat "$STAGE_CHECKOUT/deploy/compose.amd64.yaml"   # review: zebra image and entrypoint for AMD64
```

The driver passes the live `.env` read-only through `--env-file`. It writes
`$EVIDENCE/stage.env` (mode 600), which differs only in `ZWA_ENGINE_TAG`. Every
Compose command it runs is
`docker compose --project-directory <checkout>/deploy --env-file $EVIDENCE/stage.env -p zwa-prov-stage -f compose.yaml -f compose.amd64.yaml …`.

### 3. Engine image with the reviewed code

Preferred: a full rebuild per `deploy/README.md`. A staging alternative is an
overlay on the deployed engine image of the two engine files changed since
`cf7f133`:

```sh
LIVE_PORTAL=$("${D[@]}" ps -q --filter label=com.docker.compose.project=zwa-demo \
  --filter label=com.docker.compose.service=portal)
LIVE_ENGINE=$("${D[@]}" inspect --format '{{.Config.Image}}' "$LIVE_PORTAL")     # read only
[[ "$LIVE_ENGINE" =~ ^zwa-engine:[A-Za-z0-9_.-]+$ ]] || { echo "unexpected engine image $LIVE_ENGINE" >&2; exit 1; }
STAGE_TAG="provenance-${REVIEWED:0:12}"
git -C "$STAGE_CHECKOUT" diff --name-only cf7f133 -- exchange ':!exchange/test_*'   # expect exactly the two below
printf 'FROM %s\nCOPY exchange/persistent_chain.py exchange/zebra_provenance.py /app/ZWA-Brand-Integration/exchange/\n' \
  "$LIVE_ENGINE" > "$(dirname "$EVIDENCE")/Dockerfile.$STAGE_TAG"
"${D[@]}" build -f "$(dirname "$EVIDENCE")/Dockerfile.$STAGE_TAG" -t "zwa-engine:$STAGE_TAG" "$STAGE_CHECKOUT"
```

`plan` refuses any engine image whose `exchange/*.py` differ from the reviewed
checkout. If the deployed image was not built from `cf7f133`, the overlay
fails that check; then rebuild fully.

### 4. Plan (read only)

```sh
cd "$STAGE_CHECKOUT"
python3 -I deploy/scripts/provenance-stage.py plan "${SUDO_FLAG[@]}" \
  --backup-dir "$BACKUP" --engine-tag "$STAGE_TAG" --env-file "$LIVE/deploy/.env" --evidence-dir "$EVIDENCE" \
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327 \
  --expect-anchor-height 335 \
  --expect-settled-tx c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6:325
```

`plan` checks the following, and creates no volume, container or file outside `$EVIDENCE`:

- **Backup:** `$BACKUP/SHA256SUMS` lists and matches all three `zwa-demo_*.tar.gz` (`--checksum-file` names another manifest).
- **Archive contents** (read without extracting): safe paths and types; no setuid; nothing below symlinks.
- **Archived identity:** the expected unmigrated external identity.
- **Proving parameters:** match their checksums.
- **Rendered Compose configuration:**
  - project `zwa-prov-stage`;
  - new `zwa-prov-stage_*` volumes;
  - a zebra entrypoint that is **not** the ARM64 one;
  - the pinned native AMD64 zebra image;
  - the staging engine image;
  - no published ports, no Docker socket, no `zwa-demo_*` volumes.
- **Engine code:** matches the checkout.
- **Isolation:** no staging volume or container exists yet.

Review `$EVIDENCE/plan.json` and `$EVIDENCE/rendered-config.json` (mode 600;
it contains the portal token, so do not share it). Note `renderedSha256`.

### 5. Stage

```sh
python3 -I deploy/scripts/provenance-stage.py stage "${SUDO_FLAG[@]}" \
  --backup-dir "$BACKUP" --engine-tag "$STAGE_TAG" --env-file "$LIVE/deploy/.env" --evidence-dir "$EVIDENCE" \
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327 \
  --expect-anchor-height 335 \
  --expect-settled-tx c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6:325 \
  --rendered-sha256 <renderedSha256 from plan.json>
```

`stage` repeats every plan check and refuses a different rendering, override,
env file, backup or engine image. Then it runs these steps:

1. Creates `zwa-prov-stage_{zwa-root,zebra-state,zwa-params}`.
2. Restores each from the read-only backup mount with `tar --numeric-owner --same-owner --same-permissions -xzf` inside `--network none` one-shots. It verifies every restored path's type, owner, group and mode against the archive.
3. Re-verifies the backup checksums.
4. Starts **only** `zebra`. On the host, it checks that the running container uses the pinned AMD64 ID and the reviewed (non-ARM64) entrypoint.
5. Checks that the settled transaction is in block 325 and that the tip has reached the 335 anchor.
6. Snapshots `show`, the M10 state report, and every file except the identity (owner, mode, sha256).
7. Runs seven negative drills, each asserted. Each must exit **1** with its specific `PROVENANCE_REFUSED` reason, and the identity sha256 must be identical before and after. An unexpected success, a different reason, or an identity change halts staging at once.
   - wrong anchor height;
   - unpinned image;
   - architecture mismatch;
   - ARM64 claim on an AMD64 engine;
   - another Compose project (`zwa-demo`);
   - stale attestation;
   - malformed JSON.
8. Runs a fresh host attestation, then a dry run (identity unchanged), `record`, and `record` again (`ALREADY_RECORDED`). It checks the record (AMD64 pin, project, `migratedFromSha256`).
9. Checks that the files snapshot and the M10 state report are identical before and after. It runs a fresh host `verify` (`sameContainerAsRecord`), and checks `status` (`currentRuntimeVerified: false`) and the settled transaction again.
10. Revert rehearsal: the identity returns to the archived bytes, then is recorded again.

It prints `STAGING_PASSED` and writes `$EVIDENCE/stage.json`. Zebra stays
running on the internal network for inspection. Exit 2 means stop and
inspect: the staging volumes are kept and nothing live was touched. Exit 3 is
an uncertain write: reconcile as above and **do not revert**.

### 6. Stop staging

```sh
python3 -I deploy/scripts/provenance-stage.py stop "${SUDO_FLAG[@]}" --backup-dir "$BACKUP" \
  --engine-tag "$STAGE_TAG" --env-file "$LIVE/deploy/.env" --evidence-dir "$EVIDENCE" \
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327 \
  --expect-anchor-height 335 \
  --expect-settled-tx c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6:325
```

`stop` runs `docker compose down` for `zwa-prov-stage` only, keeping its
volumes. Remove `zwa-prov-stage_*` volumes only after the owner reviews the
evidence, by name. Never use `down -v`, and never touch `zwa-demo_*`.

## Original AWS state (separate owner approval; not executed)

Only after staging passes and the owner approves:

1. Pause sales.
2. Stop `caddy`, `web`, `portal`, `order-service` and `demo-signer`, so no non-cooperating writer remains (see the boundary above). Then stop `zebra`.
3. Take and checksum a fresh paired backup.
4. Start only `zebra`.
5. Run `zebra-runtime-attest.py attest --project zwa-demo`.
6. Run the reviewed engine image's one-shot `record --expect-compose-project zwa-demo …` with the same expectations.
7. Run `verify`, start the services with the reviewed engine image, and check `/readyz`.

## Rollback (the original paired backup is never modified)

Backups are mounted read-only and checksummed before and after every use.
Nothing is ever restored onto them or onto live volumes.

- **Engine only:** set `ZWA_ENGINE_TAG` back to the previous image. That engine reads the migrated identity unchanged (tested with the `cf7f133` reader). It does not take the identity lock, which is harmless once no migration runs.
- **Identity, unchanged since migration:** run `zebra_provenance revert`. It restores the exact pre-migration bytes only if they hash to `migratedFromSha256`. After an exit 3, reconcile first; never revert on an uncertain outcome.
- **Identity, chain moved on** (`revert` refuses): keep the record (harmless to both engines). Alternatively, restore the paired pre-migration backup into new volumes in an isolated project, verify it with `provenance-stage.py`-style checks and the M10 report, and only then switch service. Never combine archives from different points in time.
- **Preflight:** the preserved `preflight.sh` and patch are in `$KEEP`.
