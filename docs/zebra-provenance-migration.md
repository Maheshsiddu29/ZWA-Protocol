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
- a different runtime already recorded;
- an existing record that belongs to **another Compose project**, even with the same image and architecture. A record is confirmed (`ALREADY_RECORDED`) only for the same project; a replaced container there is reported (`sameContainerAsRecord: false`), never written.

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

- The live checkout is **`/opt/zwa/repo`** and its deployment directory is **`/opt/zwa/repo/deploy`**.
- That deployment uses a host-local `deploy/compose.amd64.yaml` override, and `deploy/scripts/preflight.sh` has a local amd64 edit.
- The paired backups are `~/zwa-recovery/paired-*/`, holding `zwa-demo_<volume>.tar.gz` archives.

Nothing below modifies, replaces or moves any file in `/opt/zwa/repo`. Files
there are only read and copied out, with `sudo` where they are not readable by
the operator. `deploy/scripts/provenance-stage.py` does the staging. Each step
validates its inputs and stops at the first unexpected result.

### 0. Variables and helpers (validated before use)

```sh
set -euo pipefail
LIVE="/opt/zwa/repo"                                  # live checkout: read only below
LIVE_DEPLOY="$LIVE/deploy"                            # live deployment directory: read only below
STAGE_CHECKOUT="$HOME/zwa-provenance-stage"           # new, separate checkout (never under /opt/zwa)
REVIEWED="REPLACE_WITH_40_HEX_REVIEWED_COMMIT"
BUNDLE=""                                             # optional: path to an owner-made git bundle
BACKUP="$HOME/zwa-recovery/paired-20261013T230743Z"     # the paired backup to stage (never modified)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
INPUTS="$HOME/zwa-prov-stage-inputs/$STAMP"           # private snapshot of the live inputs
EVIDENCE="$HOME/zwa-prov-stage-evidence/$STAMP"
[[ "$REVIEWED" =~ ^[0-9a-f]{40}$ ]] || { echo "REVIEWED is not a commit id" >&2; exit 1; }
[[ "$(basename "$BACKUP")" =~ ^paired-[A-Za-z0-9._-]+$ && -d "$BACKUP" && ! -L "$BACKUP" \
   && "$(dirname "$BACKUP")" == "$HOME/zwa-recovery" ]] \
  || { echo "BACKUP is not a ~/zwa-recovery/paired-* directory" >&2; exit 1; }
[[ -z "$BUNDLE" || ( -f "$BUNDLE" && ! -L "$BUNDLE" ) ]] || { echo "BUNDLE is not a file" >&2; exit 1; }
[[ ! -e "$STAGE_CHECKOUT" && ! -e "$INPUTS" && ! -e "$EVIDENCE" ]] \
  || { echo "staging paths already exist" >&2; exit 1; }
[[ -d "$LIVE/.git" && -d "$LIVE_DEPLOY" ]] || { echo "LIVE is not the /opt/zwa/repo checkout" >&2; exit 1; }
mkdir -p -m 700 "$INPUTS" "$(dirname "$EVIDENCE")"
# Read a live file without modifying it; fall back to sudo only for reading.
read_live() { if [[ -r "$1" ]]; then cat -- "$1"; else sudo cat -- "$1"; fi; }
# Copy a live file out to a private path owned by the operator (the live file is untouched).
copy_live() { (umask 077; read_live "$1" > "$2"); chmod "$3" "$2"; }
git_live() { if git -C "$LIVE" rev-parse HEAD > /dev/null 2>&1; then git -C "$LIVE" "$@"; else sudo git -C "$LIVE" "$@"; fi; }
# Docker access: plain if the user may use the socket, otherwise sudo (prompt once now).
if docker info > /dev/null 2>&1; then D=(docker); SUDO_FLAG=()
else sudo -v; D=(sudo -n docker); SUDO_FLAG=(--docker-sudo); fi
# Backup access: the original backup is readable only by root on the EC2 host. The driver then reads it
# through one fixed reader run as `sudo -n /usr/bin/python3 -I -c <reader>`; nothing is chmodded,
# chowned or copied, and the driver itself stays unprivileged.
if [[ -r "$BACKUP/SHA256SUMS" ]]; then BACKUP_FLAG=(); else sudo -v; BACKUP_FLAG=(--backup-sudo); fi
"${D[@]}" info --format '{{.OSType}}/{{.Architecture}}'      # expect linux/x86_64
```

### Backup access and operator permissions

The original paired backup is never chmodded, chowned, copied, rewritten or
written. All host-side backup reads go through one fixed program in
`deploy/scripts/provenance-stage.py`, `BACKUP_READER`, run as its own
process:

- **Readable backup:** unprivileged.
- **Root-only backup** (`--backup-sudo`): exactly `sudo -n /usr/bin/python3 -I -c <BACKUP_READER> list <backup-dir>`, or `… read <backup-dir> <file-name>`.

The reader works as follows:

- It accepts only a canonical absolute directory (no symlink anywhere in its path), and plain file names directly inside it.
- It opens files with `O_NOFOLLOW | O_NONBLOCK` relative to that directory, requires a regular file, and streams its bytes to the unprivileged driver over a pipe. It never writes anything.
- The driver hashes and inspects the stream in memory-bounded chunks. It treats any non-zero reader exit as a failure, even after a complete stream.
- The `/usr/bin/python3` that `sudo` runs must be root-owned and writable only by root.
- No private working copy is made.
- Archive contents (wallets, ledger) are hashed, never printed or saved; evidence holds only names, metadata and digests.

The operator (`ubuntu`) needs:

- **Docker:** membership in the `docker` group, or `sudo` for `docker` (`--docker-sudo`).
- **Root-only backup:** `sudo -n /usr/bin/python3` without a prompt, either cached by `sudo -v` (step 0) or by a NOPASSWD rule. No other `sudo` is used by the driver.
- **The live checkout:** read access to `/opt/zwa/repo/deploy` files, directly or through `sudo cat` (step 1). No write access is needed or used.
- **Nothing else:** no write access to `~/zwa-recovery`, `/opt/zwa/repo` or any Docker volume of `zwa-demo`.

### 1. Snapshot and preserve the AWS-local inputs (read only)

```sh
git_live rev-parse HEAD | tee "$INPUTS/live-head.txt"       # record it; expected cf7f133f0bb34cd4a4add8a87fba06273ba90a8f
git_live status --porcelain | tee "$INPUTS/live-status.txt"
git_live diff -- deploy/scripts/preflight.sh > "$INPUTS/preflight-local.patch"
copy_live "$LIVE_DEPLOY/scripts/preflight.sh" "$INPUTS/preflight.sh" 600
copy_live "$LIVE_DEPLOY/compose.amd64.yaml"   "$INPUTS/compose.amd64.yaml" 600
copy_live "$LIVE_DEPLOY/.env"                 "$INPUTS/live.env" 600
copy_live "$LIVE_DEPLOY/env/web.env"          "$INPUTS/web.env" 600
(cd "$INPUTS" && sha256sum -- * > SHA256SUMS && cat SHA256SUMS)
```

Never run `git checkout -- .`, `git reset --hard`, `git stash`, `git pull`,
`install`, `cp` or `docker compose down` **into or in** `/opt/zwa/repo` for this
work. The new preflight accepts both pins, which supersedes the local preflight
edit. Retire that edit only after the owner compares it with the new file.

### 2. Separate checkout of the reviewed commit

Prepare the staging checkout under **`umask 022`**. Git writes checked-out
files with the shell's umask and does not track read permission, so a checkout
made under `umask 077` is clean to `git status` but leaves
`deploy/zebra/regtest-config.toml` at mode 600. The first AWS staging attempt
failed that way: zebra runs as its image's user with every capability dropped,
so not even root inside the container can read a mode-600 file owned by
`ubuntu`. The block below sets `umask 022` for this step only and then restores the previous umask.

The reviewed commit may already be available: in the live checkout's object
store, or on its `origin` after an owner push. Otherwise the owner supplies a
bundle (`git bundle create zwa-provenance.bundle cf7f133..<branch>`) and sets
`BUNDLE`. A bundle is verified and fetched only when one is used.

```sh
OLD_UMASK=$(umask); umask 022    # this step only: the containers' users must be able to read these files
git clone --no-checkout --no-hardlinks "$LIVE" "$STAGE_CHECKOUT" 2> /dev/null \
  || sudo git clone --no-checkout --no-hardlinks "$LIVE" "$STAGE_CHECKOUT"
[[ -O "$STAGE_CHECKOUT" ]] || sudo chown -R "$(id -u):$(id -g)" "$STAGE_CHECKOUT"   # the new copy only
has_reviewed() { git -C "$STAGE_CHECKOUT" cat-file -e "$REVIEWED^{commit}" 2> /dev/null; }
if ! has_reviewed && [[ -n "$BUNDLE" ]]; then
  git -C "$STAGE_CHECKOUT" bundle verify "$BUNDLE"
  git -C "$STAGE_CHECKOUT" fetch "$BUNDLE" "+refs/heads/*:refs/remotes/bundle/*"
fi
if ! has_reviewed; then
  ORIGIN_URL="$(git_live remote get-url origin)"
  if ! git -C "$STAGE_CHECKOUT" fetch "$ORIGIN_URL" "$REVIEWED"; then   # read-only fetch
    echo "origin does not serve $REVIEWED" >&2
  fi
fi
has_reviewed || { echo "reviewed commit $REVIEWED is not available; set BUNDLE" >&2; exit 1; }
git -C "$STAGE_CHECKOUT" checkout --detach "$REVIEWED"
test "$(git -C "$STAGE_CHECKOUT" rev-parse HEAD)" = "$REVIEWED"
install -m 644 "$INPUTS/compose.amd64.yaml" "$STAGE_CHECKOUT/deploy/compose.amd64.yaml"
install -d -m 700 "$STAGE_CHECKOUT/deploy/env"
install -m 600 "$INPUTS/web.env" "$STAGE_CHECKOUT/deploy/env/web.env"
umask "$OLD_UMASK"
cat "$STAGE_CHECKOUT/deploy/compose.amd64.yaml"   # review: zebra image and entrypoint for AMD64
# Read-only check: what zebra binds and the image build copies must be world-readable (directories also
# searchable). deploy/env/web.env stays mode 600; Compose reads it on the host, not through a bind.
BAD=$(find "$STAGE_CHECKOUT/deploy/zebra" "$STAGE_CHECKOUT/exchange" \
        \( \( -type f ! -perm -0444 \) -o \( -type d ! -perm -0555 \) \) -print)
[[ -z "$BAD" ]] || { printf 'not world-readable:\n%s\n' "$BAD" >&2; exit 1; }
stat -c '%a %U:%G %n' "$STAGE_CHECKOUT/deploy/zebra/regtest-config.toml"   # expect 644
```

If that check lists files, do **not** chmod anything in `/opt/zwa/repo`, and
never chmod live files to make staging pass. Remove the new staging checkout
(it is a fresh copy, never under `/opt/zwa`) and repeat this step under
`umask 022`. `plan` makes the same check through the real mount and refuses;
the driver itself never changes a file mode.

A failed origin fetch is reported and then caught by the explicit
`has_reviewed` check, which stops the script. If git reports "dubious
ownership" for `/opt/zwa/repo`, the helpers above already retry read-only
commands with `sudo`; never change that repository's configuration. The
driver reads the snapshot
`$INPUTS/live.env` through `--env-file` and writes `$EVIDENCE/stage.env` (mode
600), which differs only in `ZWA_ENGINE_TAG`. Every Compose command it runs is
`docker compose --project-directory <checkout>/deploy --env-file $EVIDENCE/stage.env -p zwa-prov-stage -f compose.yaml -f compose.amd64.yaml …`.

### 3. Engine image with the reviewed code

Every engine service runs as **user 10001:10001** with all capabilities
dropped. `COPY` without `--chmod` keeps the source file's mode and makes the
file `root:root`, so a source at mode 600 becomes code that user 10001 cannot
read. An earlier AWS overlay image failed that way. Build only from the
staging checkout prepared under `umask 022` in step 2, and give the overlay's
`COPY` an explicit `--chmod=0644` (BuildKit, the default builder since Docker
23; the legacy builder rejects the flag instead of ignoring it).

Preferred: a full rebuild per `deploy/README.md`. A staging alternative is an
overlay on the deployed engine image of the only two engine files changed
since `cf7f133`:

```sh
LIVE_PORTAL=$("${D[@]}" ps -q --filter label=com.docker.compose.project=zwa-demo \
  --filter label=com.docker.compose.service=portal)
LIVE_ENGINE=$("${D[@]}" inspect --format '{{.Config.Image}}' "$LIVE_PORTAL")     # read only
[[ "$LIVE_ENGINE" =~ ^zwa-engine:[A-Za-z0-9_.-]+$ ]] || { echo "unexpected engine image $LIVE_ENGINE" >&2; exit 1; }
STAGE_TAG="provenance-${REVIEWED:0:12}"
test "$(git -C "$STAGE_CHECKOUT" diff --name-only cf7f133 -- exchange ':!exchange/test_*' | tr '\n' ' ')" \
  = "exchange/persistent_chain.py exchange/zebra_provenance.py "
printf 'FROM %s\nCOPY --chmod=0644 exchange/persistent_chain.py exchange/zebra_provenance.py /app/ZWA-Brand-Integration/exchange/\n' \
  "$LIVE_ENGINE" > "$INPUTS/Dockerfile.$STAGE_TAG"
"${D[@]}" build -f "$INPUTS/Dockerfile.$STAGE_TAG" -t "zwa-engine:$STAGE_TAG" "$STAGE_CHECKOUT"
```

`plan` refuses any engine image whose `exchange/*.py` differ from the reviewed
checkout, or that has any file user 10001 cannot read (see step 4). If the
deployed image was not built from `cf7f133`, the overlay fails that check;
then rebuild fully. Never fix an image by chmodding files in a running
container or a live volume: rebuild it.

### 4. Plan (read only)

```sh
cd "$STAGE_CHECKOUT"
STAGE_ARGS=("${BACKUP_FLAG[@]}" --backup-dir "$BACKUP" --engine-tag "$STAGE_TAG" --env-file "$INPUTS/live.env"
  --evidence-dir "$EVIDENCE"
  --expect-genesis 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327
  --expect-anchor-height 335
  --expect-settled-tx c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6:325
  --expect-params-dir /srv/zwa/params/ZcashParams)
python3 -I deploy/scripts/provenance-stage.py plan "${SUDO_FLAG[@]}" "${STAGE_ARGS[@]}"
```

`plan` creates no named volume and no file outside `$EVIDENCE`. Its Docker
changes are short-lived:

- one never-started, network-less container, used to copy the zebra executable and the image's `/etc/passwd` and `/etc/group` out, then removed with `rm -v`;
- `docker run --rm` probe containers of the staging engine image (no network, read-only root, no capabilities, `no-new-privileges`). Each is removed on exit with any anonymous volume.

It checks:

- **Backup:** `$BACKUP/SHA256SUMS` (or `--checksum-file`) is parsed without being rewritten.
  - Entries may be plain names, or absolute paths directly inside exactly `$BACKUP`. The AWS manifest has five absolute entries.
  - Paths elsewhere, `..`, `.`, `//`, nesting and duplicates (after reducing an entry to its file name) are refused.
  - **Every** listed archive is checksummed, but only the three `zwa-demo_{zwa-root,zebra-state,zwa-params}.tar.gz` are restored.
  - The directory's names, modes, owners, sizes and mtimes are recorded and must be identical at the end of `stage`.
- **Archive contents** (read without extracting):
  - relative paths only; no traversal, devices, FIFOs or setuid;
  - nothing below a symlink, and hard links only to earlier regular members;
  - a streamed SHA-256 of every regular file.
- **Archived identity:** the expected unmigrated external identity.
- **Proving parameters:** match their checksums.
- **Rendered Compose configuration** (an exact allowlist). It is always rendered with `--profile setup`, so all eight services, including the never-started setup services, are checked. `COMPOSE_*` lines in the live `.env` are dropped from `stage.env`:
  - **`zebra`:** only its reviewed keys; exactly `zebra-state:/data` plus the read-only pinned config bind; only the project's `internal: true` network; `cap_drop: ALL` and `no-new-privileges`; `<zebrad> -c /config/regtest-config.toml`, which is not the ARM64 entrypoint; the pinned native AMD64 image.
  - **`portal`:** exactly `zwa-root` read-write and `zwa-params` read-only; user 10001 with a read-only root; the reviewed environment; the staging engine image.
  - **`params` / `bootstrap`** (setup profile): the reviewed commands, the staging engine image, user 10001, internal network only, exact mounts. `params` alone may bind the validated `ZWA_PARAMS_DIR` (pinned with `--expect-params-dir`) read-only at `/src`.
  - **Every service:** no privileged mode, host namespaces, `cap_add` or devices; no undeclared networks or volumes; no external, driver or `driver_opts` volumes; otherwise only read-only binds inside the reviewed `deploy/`; ports only on caddy 80/443; no Docker socket; no `zwa-demo_*` names.
- **Zebra executable:** the entrypoint inside the pinned image is an executable **x86-64 ELF** (sha256 recorded).
- **Zebra configuration access** (`zebraConfig` in `plan.json`). Zebra has no Compose `user`, so it runs as its image's `USER` with `cap_drop: ALL`, where even uid 0 cannot override permission bits:
  - its uid, gid and supplementary groups are resolved from that `USER` and the image's own `/etc/passwd` and `/etc/group`, copied out of the same throwaway container (a named user missing from them, or a malformed line, is refused). When `USER` names a group, Docker versions differ on also adding the user's `/etc/group` memberships, so both possible group sets are checked;
  - on the host, `deploy/zebra/regtest-config.toml` must be a regular file reached without symlinks, hold the pinned bytes, and be readable by that identity under the owner/group/other classes;
  - a one-shot probe container (from the staging engine image, which has Python; the zebra image needs no shell) then runs with that identity, `cap_drop: ALL`, `no-new-privileges`, no network, a read-only root and the **same read-only bind** at `/config/regtest-config.toml`. It must really open the file, read the pinned sha256, and see the host's mode.
  - The rendered zebra service must also keep exactly that bind, read-only, and `-c /config/regtest-config.toml`.
- **Engine files** (`engine` in `plan.json`), from a probe container of the staging engine image run exactly as the engine services run: `--user 10001:10001`, no extra groups, `cap_drop: ALL`, `no-new-privileges`, no network, read-only root:
  - every directory under `/app` and `/opt/zwa/tx-tool` must be listable and searchable by 10001, and every regular file must open and hash (judged by the real open and by the permission bits);
  - the tx tool and `/usr/local/bin/node` must be executable regular files; devices, FIFOs and sockets are refused;
  - the `exchange/*.py` hashes it read must equal the checkout's non-test files exactly (none missing, extra, changed or symlinked).
- None of these checks changes a file mode, in the checkout, the image, a volume or the backup. A missing, symlinked, unreadable or mismatched file refuses with exit 2.
- **Isolation:** no staging volume or container exists yet.

Review `$EVIDENCE/plan.json` and `$EVIDENCE/rendered-config.json` (mode 600;
it contains the portal token, so do not share it). Note `renderedSha256`.

### 5. Stage

```sh
read_live "$LIVE_DEPLOY/.env" | cmp - "$INPUTS/live.env"     # live inputs unchanged since the snapshot
python3 -I deploy/scripts/provenance-stage.py stage "${SUDO_FLAG[@]}" "${STAGE_ARGS[@]}" \
  --rendered-sha256 "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["renderedSha256"])' \
                       "$EVIDENCE/plan.json")"
```

Run `stage` only after reviewing the plan; passing the hash from `plan.json`
records that review. `stage` repeats every plan check, and refuses a
different rendering, override, env file, backup or engine image. Then it runs
these steps:

1. Creates `zwa-prov-stage_{zwa-root,zebra-state,zwa-params}`.
2. Restores each from the read-only backup mount with `tar --numeric-owner --same-owner --same-permissions -xzf` inside `--network none` one-shots. It verifies every restored path's type, owner, group, mode and size, and the **SHA-256 of every regular file**, against the archive.
3. Re-verifies the backup checksums.
4. Repeats the zebra configuration access check, then starts **only** `zebra`. On the host, it checks that the running container uses the pinned AMD64 ID and the reviewed (non-ARM64) entrypoint.
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
8. Runs a fresh host attestation, then a dry run (identity unchanged), `record`, and `record` again (`ALREADY_RECORDED` in the same project). It checks the record (AMD64 pin, project, `migratedFromSha256`).
9. Checks that the files snapshot and the M10 state report are identical before and after. It runs a fresh host `verify` (same project, `sameContainerAsRecord`), and checks `status` (`currentRuntimeVerified: false`) and the settled transaction again.
10. Revert rehearsal: the identity returns to the archived bytes, then is recorded again.

It prints `STAGING_PASSED` and writes `$EVIDENCE/stage.json`. Zebra stays
running on the internal network for inspection. Exit 2 means stop and
inspect: the staging volumes are kept and nothing live was touched. Exit 3 is
an uncertain write: reconcile as above and **do not revert**.

### 6. Stop staging

```sh
python3 -I deploy/scripts/provenance-stage.py stop "${SUDO_FLAG[@]}" "${STAGE_ARGS[@]}"
```

`stop` runs `docker compose down` for `zwa-prov-stage` only, keeping its
volumes. Remove `zwa-prov-stage_*` volumes only after the owner reviews the
evidence, by name. Never use `down -v`, and never touch `zwa-demo_*`.

### Not yet exercised on real Docker

The unit tests drive the driver against a faithful fake Docker host. On
the developer's arm64 Mac, only these real-Docker pieces were run:

- the Compose rendering and its allowlist;
- the executable copy-out and ELF check, against the arm64 image, which was correctly refused;
- the host attestation tool.

The configuration-access and engine-file probes ran only against the fake
host. On the real host, the pinned AMD64 image's actual `USER`, its account
files, the `--mount` bind seen through Docker, and a full walk of the real
engine image are still to be exercised by `plan`.

**Never run anywhere** until the owner runs them on the EC2 host:

- `plan` and `stage` end to end on an AMD64 host;
- the real `compose.amd64.yaml`;
- the real `SHA256SUMS` manifest and `.tar.gz` layout, and reading them through real `sudo` (`--backup-sudo`);
- `tar -xzf` restores and the `find`/digest one-shots into real volumes;
- `docker compose up`/`run` in `zwa-prov-stage`;
- real Zebra RPC for the settled-transaction check;
- `--docker-sudo`;
- this section's shell helpers on that host.

Any mismatch makes `plan` or `stage` refuse; it does not proceed.

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
