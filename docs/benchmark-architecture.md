# Cryptographic workload benchmark: architecture

**Status: implemented and verified locally. Not yet run on AWS.** No number in
this repository is an AWS measurement. The only report committed here,
[`benchmark-sample-report.md`](benchmark-sample-report.md), is rendered from a
labelled test fixture.

The benchmark measures what real regtest settlements cost on one host: memory,
swap, CPU, disk and per-stage latency, for one purchase and for two or three
purchases submitted at the same time. It reuses the deployed exchange unchanged
and never mocks proving or signing. Everything it does is fail-closed: anything
it cannot verify, it refuses.

Operator commands are in [`benchmark-runbook.md`](benchmark-runbook.md).

## 1. Audit of the real execution path (Phase A)

### 1.1 Lifecycle in the hosted stack

The benchmark targets the hosted judge stack (`deploy/compose.yaml`,
`CUSTODIAL_DEMO_POLICY`), because it is the only path where one command can
create a buyer and complete a purchase without a person approving in a
terminal. Each step below is existing code.

| Step | Trigger | Code | Expensive work |
|---|---|---|---|
| Create disposable buyer wallet | portal `demo-start` | `demo_ledger.request_wallet` → `demo_wallets.create_requested` | `zcash_tx_tool wallet-signer init` and `address` (Landlock) |
| Fund wallet | order-service tick, only while no quote is live | `demo_wallets.fund_created` → `confirm_funding` | `purchase-coordinator fund-address` (shielded funding transaction, proofs, mines a block), `mine-empty` up to 110 blocks, checkpoint, one wallet scan per asset |
| Prepare exchange and reserve inventory | portal `buy` | `order_ledger.request_purchase` (atomic `offers.reserved += quantity`, idempotent per buyer and key) | none (ledger write) |
| Quote | order-service tick | `order_service.prepare_quote` | buyer and seller `wallet-signer balances` scans, `purchase-coordinator prepare-sale` |
| Buyer confirmation | portal `confirm` with the quote digest | `order_ledger.confirm_quote` | none |
| Groth16 authorization proof | order-service tick | `order_service.prove` | `node checkout/authorization/native_zec_v2.js generate` (snarkjs), then `verify` |
| Wallet review | order-service tick | `order_service.authorize` | `wallet-signer review` for buyer and seller |
| Build, prove and sign each Action Group | demo signer loop | `demo_signer.approve` | `wallet-signer authorize-live` per role: Halo2 proving and spend-authorization signing in one call |
| Assemble, re-verify, broadcast, mine | coordinator started by the order service | `tools.spawn_execute_live` | `purchase-coordinator execute-live`: re-runs the Groth16 verifier, assembles both groups, broadcasts, mines the block |
| Confirm inclusion | order-service tick | `order_service.settle` (SUBMITTED → INCLUDED) | RPC `getblock`, `getrawtransaction` |
| Verify settlement | order-service tick | `settle` (INCLUDED → RECONCILED) | buyer and seller after-scans; exact unit and zatoshi deltas |
| Receipt | order-service tick | `settle` (RECONCILED → RECEIPT_AVAILABLE) | writes `order-receipt.json` |
| Durability | order-service tick when nothing is committed | `stabilize_if_due` | `mine-empty 110`, checkpoint, rescans |

### 1.2 Interfaces

- **Portal API** (`exchange/portal.py`, served by `exchange/portal_server.py` on
  the internal network, bearer token): `demo-start`, `wallet`, `market`, `buy`,
  `order`, `confirm`, `cancel`, `verify-tx`. This is what the website calls and
  what the benchmark uses.
- **Order service** (`exchange/order_service.py serve`): one locked process; a
  2 s tick advances every live order.
- **Demo signer** (`exchange/demo_signer.py serve`): one locked process; a 1 s
  loop signs under policy.
- **Operator CLIs**: `exchange.demo_bootstrap` (fresh root), `exchange.config`,
  `deploy/scripts/zebra-runtime-attest.py` (pinned image of the running Zebra).

### 1.3 Concurrency controls (these shape what "concurrency" means)

- **One order service and one demo signer** per root (`fcntl` locks). Steps run
  sequentially inside each loop.
- **One live quote per wallet pair.** `order_ledger.wallets_committed` blocks a
  new quote while another order holds the buyer's or the seller's notes. The
  hosted stack has one dealer wallet, so **settlements are serialized**. A
  second purchase waits in `BUYER_REQUESTED` until the first one leaves the
  committed states.
- **Funding and durability mining happen only while no quote is live**,
  because each quote is bound to the next block height.
- **Admission limits** (`exchange/config.py DEMO_LIMITS`): 40 live wallets, 60
  wallets per hour, 3 per client per hour, 5 orders per wallet, 5 units and
  0.01 ZEC per order, 60 sponsored settlements per hour.

So Levels 2 and 3 measure concurrent *submission*: overlapping wallet
provisioning, queueing, polling and the memory held while orders wait. They do
not measure parallel proving, because the application never proves two
settlements at once.

### 1.4 Existing timeouts

| Where | Value |
|---|---|
| Wallet tool call (`Tools.signer`) | 900 s |
| Coordinator tool call (`Tools.coordinator`) | 1800 s |
| Groth16 generate or verify (`Tools.authorization`) | 300 s |
| Quote validity, demo | 90 s |
| Coordinator approval window, demo | 60 s; signer `authorize-live` timeout 120 s |
| Companion presence | 15 s |
| Wallet creation request | 600 s |
| Durability blocks | 110 |

### 1.5 What can be measured from outside

| Measurement | Source | Resolution |
|---|---|---|
| Host RAM, swap, CPU, load, OOM kills, PSI | `/proc/meminfo`, `/proc/stat`, `/proc/loadavg`, `/proc/vmstat`, `/proc/pressure/memory` | 1 s |
| Disk free | `statvfs` on the workdir and on the mount holding Docker's root | 1 s |
| Container memory, peak, limit, OOM events, CPU, throttling | cgroup v2 files of each container | 1 s |
| Restarts, exit status, `OOMKilled` | `docker inspect` (every 5 s, and at once when a cgroup or init process disappears) | ≤5 s |
| Per-process RSS and peak RSS, CPU time, exact start time | `/proc/<pid>/status` and `/proc/<pid>/stat` | 1 s sampling; start time exact |
| Stage ends | modification times of the order's evidence files | nanoseconds |
| Ledger transitions | `order_events.occurred_at` | 1 s |
| Client view | the harness's own requests and polls | poll interval (1 s) |

**Not separable from outside:**

- Halo2 proving and spend-authorization signing are one `authorize-live` call
  per role.
- Broadcast and block inclusion are one `execute-live` call.

The pinned tool is not modified to split them.

## 2. Components

```
operator ── deploy/scripts/zwa-bench.py ──▶ benchmark/cli.py
                                              │
          ┌───────────────────────────────────┼─────────────────────────────────┐
          │ host (Linux, cgroup v2)            │                                 │
          │  ResourceMonitor 1 Hz ── /proc, /sys/fs/cgroup/<container>           │
          │  Watchdog ──▶ StopController (gate)                                  │
          │  Workers (1..3 threads) ── PortalRelay ──┐                           │
          │  probe() ────────────────────────────────┤ docker compose exec -T    │
          └──────────────────────────────────────────┼───────────────────────────┘
                                                     ▼
   project zwa-bench-<name> (internal network only, new volumes)
     portal ── relay (python3 -c RELAY) ──▶ http://127.0.0.1:8790/v1/portal
            └─ probe (python3 -c PROBE, read-only)
     order-service ── tx-tool, node (Groth16)        demo-signer ── tx-tool
     zebra (pinned image, attested)
```

| Module | Responsibility |
|---|---|
| `benchmark/safety.py` | Project naming, protected projects, rendered-configuration validation, mainnet prohibition, levels, budget, workdir rules |
| `benchmark/docker.py` | The only Docker access: an allowlist of command shapes with a minimal environment |
| `benchmark/metrics.py` | Host, cgroup and process readers; process classification |
| `benchmark/watchdog.py` | Thresholds, stop codes, the submission gate |
| `benchmark/engine.py` | The relay and probe programs, the relay client |
| `benchmark/workload.py` | Monitor thread, purchase workers, level orchestration |
| `benchmark/analysis.py` | Inventory, receipt and chain checks, reconciliation, stage latencies, resource summary |
| `benchmark/evidence.py` | Workdir layout, write-once files, redaction, secret scan, budget ledger |
| `benchmark/report.py` | Markdown report |
| `benchmark/cli.py` | Commands |
| `deploy/benchmark/compose.bench.yaml` | Override that removes the web edge |
| `deploy/benchmark/schema/*.json` | Output schemas |

Nothing under `benchmark/` is copied into an image. The relay and probe are
passed as `python3 -c` programs to the benchmark project's own portal
container, so the engine image and its verified files stay exactly as
reviewed.

## 3. Isolation (Phase B)

- **Separate project.** `--project` must match `zwa-bench-[a-z0-9][a-z0-9-]*`.
  `zwa-demo`, `zwa-restore-audit`, `zwa-prov-stage`, `zwa-prov-stage-r2` and
  `zwa-prov-preflight` are refused by name, including case and whitespace
  variants. Every Compose command passes `-p <project>`, which replaces the
  base file's `name: zwa-demo`. `plan` and `prepare` render the configuration
  and refuse unless the rendered `name` is the benchmark project.
- **New state only.** The rendered volumes must be exactly `<project>_zwa-root`,
  `<project>_zwa-params` and `<project>_zebra-state`, non-external, default
  driver. `plan` refuses if any container, volume or network of the project
  already exists, by label or by name prefix. `exchange.demo_bootstrap`
  refuses an initialized root or a chain with blocks, so the dealer, sponsor
  and coordinator seeds, test assets and buyer wallets are all new.
- **No foreign mounts.** Services may mount only those volumes, the pinned
  `deploy/zebra/regtest-config.toml` (read-only, into Zebra) and the
  parameters directory (read-only, into the one-shot `params` service).
  Running containers are re-checked through `docker inspect` before and
  during every level.
- **No exposure.** The override moves `web` and `caddy` to a profile the
  harness never enables, removes their env file and published ports. No
  service may set `ports`, `network_mode`, `privileged`, `cap_add`, devices
  or the like; the only network is the project's internal one.
- **Regtest only.** The Zebra configuration must be byte-identical to the
  pinned regtest configuration. The live chain's genesis must be the Zcash
  regtest genesis; mainnet and testnet genesis hashes are refused by name.
  The quote's network must be `regtest`, and the Zebra image must be the one
  pinned for the host platform, attested with the existing tool after start.
- **Nothing destructive.** `docker.py` cannot express `prune`, `down -v`,
  `rm`, `kill`, `--force`, `--volumes` or compose services outside the
  benchmark set. `stop` keeps everything. `cleanup` is a separate command
  that needs the project name and the manifest's sha256, refuses volumes it
  did not create or that carry another project's label, and removes exactly
  the manifest's volumes after `compose down` (without `-v`). Evidence is
  never deleted.
- **Provenance preserved.** The checkout must be clean (or `--allow-dirty`,
  recorded). The manifest records the source revision, harness file hashes,
  Compose file hashes, the rendered sha256, engine and Zebra image IDs, and
  the tx-tool revision. Each level refuses if the running images differ from
  the manifest.

## 4. Instrumentation (Phase C)

At least one sample per second (`--sample-interval` 0.25–1.0 s) goes to
`samples.jsonl`. Three memory quantities are recorded separately and never
added together:

1. **Host memory**: `MemAvailable`, swap used and its growth since the run
   started.
2. **Container cgroup memory**: `memory.current`, `memory.peak` (kernel 5.19+),
   `memory.max`, anonymous and file memory. This includes page cache and
   kernel memory charged to the container.
3. **Process RSS**: `VmRSS` and `VmHWM` (peak) for every process in each
   container, with a fixed label such as `groth16:generate` or
   `tx-tool:signer:authorize-live:buyer`. The command line is read only to
   choose the label and is never stored.

Cryptographic-operation latency is derived after the run (section 6).

## 5. Levels and stop conditions (Phases D and E)

| Level | Purchases | Gate |
|---|---|---|
| 0 | none; idle sampling (default 300 s) | prepared project |
| 1 | 1 (gold reference, 1 unit) | prepared project |
| 2 | 2, submitted together (gold, treasury) | prepared project |
| 3 | 3, submitted together (gold, treasury, credit) | `--level2-summary` and its sha256: a passing Level 2 run of the same project |

Each level is a separate operator command, and no code path calls another
level. A project has a transaction budget fixed at prepare time (default 6,
hard maximum 12). Each level reserves its purchases in `evidence/budget/`
before it submits anything, and the reservation counts even if the run fails.
Concurrency is fixed by the level, never above 3.

Every purchase also re-sends its `buy` once with the same idempotency key,
which must return the same order. Afterwards the probe proves that reserved
units equal what live orders hold, sold units equal settled orders, nothing
exceeds capacity or the dealer's scanned units, and this run's settlements
account for the change in `sold`.

| Code | Default | Source |
|---|---|---|
| `MEM_AVAILABLE_LOW` | MemAvailable < 1.5 GiB | host |
| `SWAP_GROWTH` | swap used grew > 256 MiB since the run started | host |
| `HOST_OOM_KILL` | `/proc/vmstat oom_kill` increased | host |
| `CONTAINER_OOM_KILL` | cgroup `oom_kill` increased or `OOMKilled` | container |
| `CONTAINER_RESTARTED` | restart count, start time or init process changed | container |
| `CONTAINER_NOT_RUNNING` | not running (exit code recorded) | container |
| `DISK_FREE_LOW` | < 10 GiB on the workdir or Docker filesystem | host |
| `SAMPLER_FAILED` | 5 consecutive sampling failures | harness |
| `WALLET_READY_TIMEOUT` | 600 s | worker |
| `QUOTE_TIMEOUT` | 240 s + 900 s per earlier purchase in the queue | worker |
| `SETTLEMENT_TIMEOUT` | 900 s after confirmation | worker |
| `LEVEL_TIMEOUT` | 3600 s | level |
| `ORDER_FAILED` | any terminal failure state, failed wallet, refused confirmation or mismatched quote | worker |
| `RECEIPT_MISSING`, `RECEIPT_INCONSISTENT` | no live receipt; proof verdicts, approval modes, wallet effects, quantity or payment differ | worker and probe |
| `TX_VERIFICATION_FAILED` | public `verify-tx` or the probe's RPC does not find the transaction in its block | worker and probe |
| `INVENTORY_INCONSISTENT` | reservation or sale accounting differs | probe |
| `DUPLICATE_SUBMISSION_MISMATCH` | a retried `buy` created a different order | worker |
| `PORTAL_REFUSED`, `HARNESS_ERROR`, `OPERATOR_INTERRUPT` | repeated refusals, an internal error, Ctrl-C | harness |

Thresholds are command-line options with bounds (for example MemAvailable
0.5–6 GiB, swap growth 0–2 GiB, disk 2–500 GiB), so a typo cannot disable a
condition. The first condition is the run's `stopReason`; all of them are
listed in `stopReasons`.

**Safe cancellation.** When the gate closes:

- No new session, order or confirmation is sent.
- An order not yet confirmed (`BUYER_REQUESTED`, `QUOTE_READY`) is cancelled
  through the portal's own pre-signing `cancel`, which releases its
  reservation inside one ledger transaction.
- A confirmed order is never cancelled and no process is signalled. The
  harness keeps polling it until it settles or fails on its own, or the drain
  window (`--drain-timeout`, default 1200 s) ends. The exchange's own state
  machine decides the outcome: an unapproved trade fails and releases its
  reservation; a trade whose coordinator stopped after both approvals becomes
  `UNCERTAIN` and keeps it, until the order service proves non-execution after
  the expiry height.

The run is then `STOPPED` if every order is terminal, or `PARTIAL` otherwise.
`reconcile` re-reads the orders later.

## 6. Latency model

Each stage records its two boundaries and its method in `summary.json`:

| Stage | Boundaries |
|---|---|
| `walletSetup`, `walletFunding` | client polls: request → READY; FUNDING first seen → READY |
| `reservationRequest` | `buy` round trip (the ledger reservation) |
| `quoteReadyIncludingQueue` | submit → `quote/terms.json` |
| `quotePreparation` | buyer-scan process start → `quote/terms.json` |
| `groth16ProofGeneration` | `groth16:generate` process start → `proof-generation.log`, else an upper bound from the confirmation |
| `groth16Verification` | `proof-generation.log` → `preflight-authorization.json` |
| `walletReview` | `preflight-authorization.json` → `seller-review.log` |
| `buyerActionGroupProveAndSign`, `sellerActionGroupProveAndSign` | `authorize-live` process start → `<role>-signer.log`, else a labelled upper bound |
| `coordinatorExecuteLive` | `execute-live` process start → `receipt.json` |
| `assembleBroadcastInclude` | `seller-signer.log` → `receipt.json` |
| `inclusionReconciliation` | `receipt.json` → `seller-after.log` |
| `receiptGeneration` | `seller-after.log` → `order-receipt.json` |
| `confirmToReceipt`, `endToEnd`, `endToEndClientObserved` | confirmation or submission → receipt file, or receipt observed |
| `durability` | `order-receipt.json` → `stabilization.json` |

Process start times come from `/proc/<pid>/stat` and are exact; file
modification times have nanosecond resolution on the same host clock. Peak RSS
and CPU seconds of each matched process are kept under `operations`. If two
boundaries overlap, the stage is reported as `null` with a note rather than as
a negative or invented number.

## 7. Evidence and secrets

`<workdir>/private/bench.env` (0600) holds the generated portal token; it is
never evidence. Everything under `<workdir>/evidence/` is created exclusively
and never overwritten. Before each record is written, the redactor removes
values of keys that name secrets (session, token, client hash, seed, password,
key material) and every registered secret value: the token and each buyer's
session and client hash. These exist only in memory. After writing, the run
directory is re-scanned for registered secrets and Zcash key encodings; a
finding exits 4 and is recorded. The probe never opens a wallet home, and it
prints no session or client hash, wallet address or wallet ID.

## 8. Not measured, and limitations

- Halo2 proving and signing are reported together per role; broadcast and
  inclusion together. Confirmation means inclusion in the block the
  coordinator mines on regtest, not consensus finality.
- Settlements are serialized by the application (section 1.3). Levels 2–3 do
  not measure parallel proving.
- Processes shorter than the sampling interval can be missed by the process
  sampler (latency is still bounded by evidence files; RSS is not).
- Container memory includes page cache; process RSS counts shared pages in
  each process.
- The relay and probe run inside the benchmark's portal container and add a
  small, labelled load there (`bench:portal-relay`, `bench:probe`).
- The web edge (Caddy, Next.js, TLS) is not exercised.
- Linux with cgroup v2 only. On macOS, `plan` works but `prepare` and `run`
  refuse, because the host and cgroups cannot be read.
- `docker inspect` state is sampled every 5 s; OOM and restarts are also
  caught at 1 Hz from cgroup counters and the container's init process.
- One host, one run per level. These are observations of this deployment,
  not statistical performance claims.

## 9. Verification performed locally

- 106 unit and adversarial tests (`benchmark/test_safety.py` 25,
  `test_metrics.py` 23, `test_workload.py` 26, `test_cli.py` 32). They run on
  fixture `/proc` and cgroup trees, a fake Docker host that records every
  argv, and a simulated exchange with injected faults.
- 5 integration tests (`benchmark/test_integration.py`). The real relay and
  probe processes run against the real `portal_server`, portal, schema-4
  ledger, `OrderService` and `DemoSigner`; only the tx-tool, node and Zebra
  are the existing exchange test doubles. These tests found one defect,
  fixed in the same commit: an absent dealer-policy row was read as
  not-ACTIVE.
- A guard mutation pass: 29 single-line mutations of the safety, Docker,
  watchdog, gate, inventory, receipt, redaction, cleanup, Level 3 and
  read-only-probe checks. All 29 are detected: 28 by the full suite, and one
  (ignoring terminal failure states) by the unit tests; the integration suite
  would only time out on it after 900 s, and even then reports
  `SETTLEMENT_TIMEOUT` rather than success.
- `mypy` (default mode), `pyflakes` and `flake8` at 120 columns are clean for
  `benchmark/` and the entry script.
- A read-only `plan` against the real local Docker (Compose 5.3.1, arm64
  pinned Zebra image, the real rendering, the existing attestation tool) with
  only the parameter checksum stubbed. No Docker resource changed.
- No real cryptographic settlement was executed for this work. The
  full-stack path (Groth16, Halo2, broadcast) has not yet run under the
  harness.
