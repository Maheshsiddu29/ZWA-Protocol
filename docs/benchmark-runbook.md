# Cryptographic workload benchmark: runbook

**Every AWS step below needs the owner's explicit approval before it is run.**
Nothing here has been run on AWS yet. Design and limitations:
[`benchmark-architecture.md`](benchmark-architecture.md).

All commands run on the Docker host from a **separate checkout** of the
reviewed commit, never from the live deployment's checkout. Run them as the
operator user. Add `--docker-sudo` if Docker needs root (run `sudo -v` first).

```sh
BENCH="python3 -I deploy/scripts/zwa-bench.py"
P=zwa-bench-aws-r1                      # a new zwa-bench-* name for every prepared project
W=/home/ubuntu/zwa-bench/$P             # outside the checkout, ~/zwa-recovery and any exchange root
COMMON="--project $P --workdir $W --platform-override deploy/compose.amd64.yaml --docker-sudo"
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | passed |
| 2 | refused or failed before anything was submitted; nothing to clean up from this command |
| 3 | the level stopped or is partial; read the summary, then run `reconcile` |
| 4 | the evidence scan found a secret; restrict the workdir and report it |

## 1. AWS prerequisites

| Requirement | How to check (read-only) |
|---|---|
| Linux with cgroup v2 | `stat -fc %T /sys/fs/cgroup` prints `cgroup2fs` |
| Docker Engine with Compose 2.24 or newer (`!reset` in the override) | `docker compose version --short` |
| Python 3.10 or newer on the host | `python3 --version` |
| A clean checkout of the reviewed commit, made under `umask 022` | `git status --porcelain` prints at most `?? deploy/compose.amd64.yaml` |
| The host's AMD64 override in that checkout | `deploy/compose.amd64.yaml`, reviewed as for staging |
| The engine image built from that commit, and its verified ID | `docker image inspect zwa-engine:<tag> --format '{{.Id}}'` equals the ID that passed the hardened preflight (3,372 engine files) |
| The pinned AMD64 Zebra image | `python3 -I deploy/scripts/zebra-runtime-attest.py check-image --image qedit/zebra-zsa-swap-pinned:latest --docker-sudo` |
| Checksummed Sapling parameters | `--params-dir` (for example `/srv/zwa/params/ZcashParams`); `plan` verifies both files' sha256 |
| Disk | at least 10 GiB free above the stop threshold on the workdir and Docker filesystems |
| Memory | MemAvailable at least 2.5 GiB (1 GiB above the stop threshold) before any level |
| Nothing else heavy running | the live stack is idle; no build, backup or staging job in progress |

The benchmark project's Compose memory limits add up to 8.5 GiB (zebra 4 GiB,
order-service 3 GiB, demo-signer 1 GiB, portal 512 MiB) on an 8 GiB host, and
its CPU limits to 6 on 2 vCPUs. The limits are not reservations. The stop
conditions exist because they can be exceeded together.

## 2. Safety checklist (before every command that changes anything)

- [ ] The owner approved this specific step (prepare, each level, cleanup).
- [ ] `$P` starts with `zwa-bench-` and was never used before on this host.
- [ ] `$W` is new, owner-only, and outside the checkout, `~/zwa-recovery`, `/srv/zwa`
      and any `zwa4e-*` root.
- [ ] Record the protected projects' state, to compare afterwards:
      `docker ps -a --format '{{.Names}} {{.Status}}' | sort > $W-before-ps.txt` and
      `docker volume ls -q | sort > $W-before-volumes.txt`
      (the files go next to `$W`, not inside it, before `$W` exists).
- [ ] `plan` printed `readyForPrepare: true`, the expected `engine.id`,
      `zebra.id == zebraPinned`, three `$P_*` volumes and no `hostProblems`.
- [ ] The transaction budget is chosen (default 6: one Level 1, one Level 2,
      one Level 3).
- [ ] The operator stays at the terminal for the whole level.
- [ ] Stop thresholds are left at their defaults or tightened, never loosened,
      without the owner's agreement.

## 3. Procedure

### 3.1 Plan (read-only toward Docker)

```sh
$BENCH plan $COMMON --engine-tag <tag> --params-dir /srv/zwa/params/ZcashParams \
    --expect-engine-image-id sha256:<verified engine image ID>
```

This validates the project name, workdir, checkout, Zebra configuration,
parameters, images and rendered configuration, and that the project has no
Docker resources yet. It writes `$W/private/bench.env` (0600, with a new portal
token) and `$W/evidence/plan-<time>.json`. Review the output and note
`renderedSha256`.

### 3.2 Prepare (creates the isolated project) — owner approval

```sh
$BENCH prepare $COMMON --engine-tag <tag> --params-dir /srv/zwa/params/ZcashParams \
    --expect-engine-image-id sha256:<verified engine image ID> \
    --rendered-sha256 <renderedSha256 from plan> --max-transactions 6
```

Prepare repeats every plan check, writes `evidence/manifest.json` (the exact
resources it may create), then:

1. copies and checksums the parameters into `$P_zwa-params`;
2. starts Zebra and attests it with `zebra-runtime-attest.py verify`;
3. bootstraps a fresh root, with new dealer, sponsor and coordinator seeds,
   three test assets and a durable checkpoint;
4. starts order-service, demo-signer and portal;
5. waits for `/readyz` and for scanned dealer inventory, verifies the regtest
   genesis, and records `evidence/prepare-result.json`.

Bootstrap mines about 213 blocks: 100 for coinbase maturity, one per issuance
and 110 for durability. It then scans the dealer wallet. Its duration on this
host has not been measured yet; the harness allows up to 90 minutes. If prepare
fails, nothing is removed. Inspect the error and `status`, then either fix the
cause and use a **new** project name, or run cleanup (section 5).

### 3.3 Status (read-only)

```sh
$BENCH status $COMMON
```

### 3.4 Levels — each needs its own owner approval

Run them in order, reviewing each before the next.

```sh
$BENCH run $COMMON --level 0 --baseline-seconds 300        # idle baseline, no transactions
$BENCH run $COMMON --level 1                                # one real settlement
$BENCH run $COMMON --level 2                                # two purchases submitted together
```

Each run prints `runId`, `result`, `stopReason` and `summarySha256`, and writes
`$W/evidence/runs/<runId>/`.

Level 3 runs only after the Level 2 summary has been reviewed:

```sh
L2=$W/evidence/runs/<Level 2 runId>/summary.json
sha256sum "$L2"                                             # the reviewer records this value
$BENCH run $COMMON --level 3 --level2-summary "$L2" --level2-summary-sha256 <reviewed sha256>
```

It is refused unless that summary is a **PASS** Level 2 run of the same project
inside `$W`, and its sha256 matches.

Optional per-run settings:

- `--idle-before`, `--idle-after`: quiet windows, default 30 s each.
- `--sample-interval`: 0.25–1.0 s.
- `--no-wait-durability`: do not require the 110-block durability step. The
  summary and report then state that the PASS does not cover durability.
- Threshold flags: `--min-mem-available-gib`, `--max-swap-growth-mib`,
  `--min-disk-free-gib`, `--settlement-timeout`, `--drain-timeout`, etc.

A level refuses to start if:

- any order of the project is active or `UNCERTAIN` (reconcile first);
- the inventory accounting is inconsistent;
- the dealer policy is paused;
- the running images differ from the manifest;
- Zebra's attestation fails;
- the budget would be exceeded;
- MemAvailable or disk is already near its threshold.

**What PASS means.** A run is `PASS` only when all of these hold:

- No stop condition was recorded, there are no consistency problems, and a
  final probe exists.
- Monitoring was valid:
  - at least 2 samples;
  - at most 1% failed samples;
  - no gap over max(5 s, 5 intervals);
  - coverage of the whole run.
- Exactly the level's number of purchases ran, and each one:
  - ended `SETTLED_VERIFIED` with no recorded problem;
  - has final probe evidence and `SETTLED` reconciliation;
  - is confirmed included on regtest by `verify-tx`;
  - passed the final receipt re-check;
  - unless `--no-wait-durability`, reached durability and kept it.

Anything less is recorded as `ACCEPTANCE_FAILED` and the run is `STOPPED` or
`PARTIAL`. Any portal refusal (session start, buy, order lookup, confirmation)
stops the level.

The portal's generic `request could not be completed` answer is the
exchange's catch-all for an internal exception. It is treated as transient:
reads retry it, a `buy` is resent with the same key, and a confirmation is
never re-sent. Instead the order is read to see whether the confirmation
applied. Level 3 re-derives these rules from the reviewed Level 2
`summary.json` and does not trust its recorded result.

## 4. During a run: stops, safe cancellation and reconciliation

The harness never kills a process or container. When a stop condition fires,
or the operator presses **Ctrl-C** once (`OPERATOR_INTERRUPT`):

1. No new demo session, order or confirmation is submitted.
2. An order still before confirmation is cancelled with the portal's own
   `cancel`, which releases its reservation.
3. A confirmed order is observed until it settles, fails, or the drain window
   ends (`--drain-timeout`, default 1200 s). It is never cancelled. Proof
   generation, signing and broadcast finish or fail inside the exchange,
   which records an exact state.
4. The summary records the first stop reason and every later one. The result
   is `STOPPED` when all orders are terminal, otherwise `PARTIAL` (exit 3).

A second Ctrl-C does not abort the drain. To leave the harness at once, send
`kill <harness pid>` from another terminal. This ends only the harness, not the
exchange, which keeps settling or failing confirmed orders on its own.
`summary.json` is then missing for that run; use `reconcile` together with
the run's `events.jsonl` and `samples.jsonl`. If the harness itself fails
after submitting, it writes `run-error.json` and exits 3.

After `STOPPED` or `PARTIAL`:

```sh
$BENCH reconcile $COMMON --run <runId>      # read-only; writes reconcile-<time>.json
```

**Ambiguous purchases.** Before each `buy`, the harness writes a protected
correlation record to `$W/private/correlations/` (0600). It holds the demo
account, its session hash, the idempotency key, and the offer, instrument and
quantity. If the exchange's answer is lost:

1. The buy is resent with the **same** key and payload, at most 3 times
   (the ledger returns the existing order).
2. If the answer is still unknown, the harness reads the buyer's orders
   through the portal's read-only `orders` action:
   - no order: `NOT_ACCEPTED`;
   - exactly one matching order: `ACCEPTED`, and that order is followed;
   - anything else: `PURCHASE_AMBIGUOUS`, which stops the level as `PARTIAL`.

A new key or a second purchase is never used to repair an unknown result.

`reconcile` repeats that lookup for any unresolved record of the run, and
never submits. If it cannot resolve a record (for example, the demo session
has expired, after 2 hours), the record stays `AMBIGUOUS` for owner review.
Compare the ledger's orders for that demo account by hand. `cleanup` refuses
while any record is unresolved. Records contain session material: never copy
`private/` with the evidence.

| Class | Meaning | Action |
|---|---|---|
| `SETTLED` | receipt, and the transaction is in its block | none |
| `SETTLED_UNVERIFIED` | receipt, but the probe's RPC check failed | check the block independently before relying on it |
| `NOT_EXECUTED` | failed before submission; reservation released | none |
| `UNCONFIRMED` | still `BUYER_REQUESTED` or `QUOTE_READY` | none: an unconfirmed quote expires after 90 s and releases its reservation |
| `IN_PROGRESS` | confirmed and still moving | wait, then run `reconcile` again; never cancel |
| `UNCERTAIN` | the coordinator stopped after both approvals | wait: the order service releases it only after the expiry height passes and fresh scans equal the before-scans; otherwise it stays for owner review |
| `UNEXPECTED` | a failure state whose evidence says a transaction was submitted | stop; owner review |

Do not start another level until `reconcile` shows no `IN_PROGRESS`,
`UNCONFIRMED` or `UNCERTAIN` order (the level preconditions also enforce
this).

**Emergency stop**, only if the host itself is at risk (for example
MemAvailable near zero and still falling after the harness stopped
submitting):

1. Prefer waiting: the drain is bounded and nothing new is being submitted.
2. If you must stop the stack: `$BENCH stop $COMMON --confirm-project $P`.
   This runs `docker compose stop` for this project only and keeps
   containers, volumes and evidence. **It is crash-equivalent for the
   exchange.** An order whose coordinator stops after both approvals becomes
   `UNCERTAIN`, which is the designed fail-closed outcome. `stop` is not a
   step toward cleanup: cleanup is refused until the project is running,
   reconciled and quiescent again.
3. Never use `docker kill`, `docker compose down -v`, `docker volume prune` or
   `docker system prune`, and never touch another project.
4. Later, start the project again with exactly the harness's file set, attest
   Zebra, and then run `reconcile`:

   ```sh
   DC="sudo docker compose --project-directory deploy -p $P --env-file $W/private/bench.env \
       -f deploy/compose.yaml -f deploy/compose.amd64.yaml -f deploy/benchmark/compose.bench.yaml"
   $DC up -d zebra
   python3 -I deploy/scripts/zebra-runtime-attest.py verify --image qedit/zebra-zsa-swap-pinned:latest \
       --project $P --docker-sudo
   $DC up -d order-service demo-signer portal
   ```

## 5. Stop and cleanup (separate, explicit) — owner approval

Defaults keep everything:

- `stop` keeps containers, volumes and evidence.
- Nothing in the harness deletes evidence.

Cleanup removes this project's containers, network and exactly the three
volumes named in its manifest:

```sh
sha256sum $W/evidence/manifest.json
$BENCH cleanup $COMMON --confirm-project $P --confirm-manifest-sha256 <that sha256>
```

Cleanup deletes the ledger and wallet volumes, so it requires evidence that
nothing is left to settle or reconcile. The project must be **running**, and
every run reconciled. It refuses if:

- another benchmark command holds the workdir;
- any volume with this project's name or label is not in the manifest, or a
  volume is labelled for another project (all labels are checked before
  anything is changed);
- any purchase correlation record is unresolved;
- either of two read-only probes, `--quiescence-seconds` apart (default 30,
  5–600), fails or shows any of these:
  - an unavailable or non-regtest chain RPC;
  - an order that is active, unconfirmed, in progress or `UNCERTAIN`;
  - a settlement that is unverified, not yet stabilized, or dropped;
  - a demo wallet still being created or funded;
  - inconsistent reservations or sales;
  - an order named by any run's evidence but missing from the ledger;
  - any change in tip, orders, wallets, settlements or offers between the
    two probes.

Each attempt records `evidence/cleanup-check-<time>.json`. Only after a
`QUIESCENT` check does it run `docker compose down` (without `-v`) and
`docker volume rm` on each manifest volume, recording
`evidence/cleanup-<time>.json`. Evidence stays.

If the stack is stopped or the RPC is down, cleanup is refused with the
remedy: start the project as in section 4, run `reconcile`, then retry. If
the exchange is still mining (for example sponsor top-up after a run), retry
later.

**Failed prepare.** A project whose prepare never completed, and that never
ran a level, never received a purchase from the harness. For it only:

```sh
$BENCH cleanup $COMMON --confirm-project $P --confirm-manifest-sha256 <sha256> --prepare-failed
```

This is refused if `prepare-result.json` says `PREPARED`, or if any level run,
budget reservation or purchase record exists.

Afterwards, confirm nothing else changed:

```sh
docker ps -a --format '{{.Names}} {{.Status}}' | sort | diff $W-before-ps.txt -
docker volume ls -q | sort | diff $W-before-volumes.txt -
```

Only `$P` resources may differ.

## 6. Reading the evidence

| File | Content |
|---|---|
| `runs/<run>/summary.json` | authoritative; schema `deploy/benchmark/schema/summary.schema.json` |
| `runs/<run>/report.md` | concise Markdown rendering of the summary |
| `runs/<run>/samples.jsonl` | every resource sample; schema `deploy/benchmark/schema/sample.schema.json` |
| `runs/<run>/events.jsonl` | harness events: phases, state observations, stops |
| `runs/<run>/probe-before.json`, `probe-after.json` | read-only ledger, inventory and chain snapshots |
| `runs/<run>/run.json` | exact configuration, thresholds, attestation and container IDs |
| `manifest.json`, `prepare-result.json`, `budget/` | the prepared project and its transaction budget |

`private/bench.env` holds the portal token. Never copy it with the evidence.
The evidence holds txids, block hashes, order and demo account IDs, and no
keys, seeds, session or client hashes, or wallet addresses. Review it before
sharing anyway.

## 7. Refusals you may see

| Message | Cause |
|---|---|
| `refusing protected Compose project` | the name is, or folds to, a protected project |
| `rendered project name ... is not ...` | the rendering resolved to another project (for example `-p` lost) |
| `already has Docker resources` | the project is not fresh; choose a new name |
| `mounts foreign volume` | a service or running container mounts a volume of another project |
| `refusing mainnet` / `not the Zcash regtest genesis` | the chain is not disposable regtest |
| `transaction budget exhausted` | the project's purchases are used up; prepare a new project |
| `level 3 requires ...` / `permitted only after a passing Level 2` | the Level 2 review gate |
| `host checks failed` | not Linux/cgroup v2, or memory or disk already near a threshold |
| `preconditions failed` | active or uncertain orders, inconsistent inventory, or a paused dealer |
| `exchange is not quiescent` / `unresolved purchase(s)` | cleanup's quiescence evidence is missing; reconcile and wait |
| `exchange state is unavailable` | cleanup could not probe the exchange; start the project and reconcile first |
