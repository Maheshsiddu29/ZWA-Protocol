# ZWA benchmark: level 2 (PASS)

> **Test fixture, not a measurement.** Every number below comes from a synthetic fixture used to exercise the report generator.

- Run: `20261014T120000Z-L2-a1b2c3` on project `zwa-bench-fixture`
- Started 2026-10-14T12:00:00Z, ended 2026-10-14T12:10:00Z
- Source revision: `ffffffffffffffffffffffffffffffffffffffff`
- Engine image: `sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee`; Zebra image: `sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9`
- Concurrency: 2; transactions submitted: 2

## Result

**PASS**: no stop condition triggered

- Acceptance: passed
- Durability: required for PASS
- Monitoring: valid (30 valid samples, 0 errors, largest gap 1.0 s)

## Host

| Measure | Value |
|---|---|
| Total RAM | 8.00 GiB |
| MemAvailable at start / minimum | 5.00 GiB / 5.00 GiB |
| Swap used at start / max (growth) | 0 MiB / 0 MiB (0 MiB) |
| CPU mean / max | 20.0 % / 20.0 % |
| Load average (1 min) max | 0.5 |
| Kernel OOM kills during run | 0 |

30 samples, 0 sampling errors.

## Transactions

| Stage | P1 | P2 |
|---|---|---|
| Outcome | SETTLED_VERIFIED | SETTLED_VERIFIED |
| Final state | RECEIPT_AVAILABLE | RECEIPT_AVAILABLE |
| walletSetup | 4.0 s | 4.0 s |
| walletFunding | 2.0 s | 2.0 s |
| reservationRequest | 0.0 s | 0.0 s |
| quoteReadyIncludingQueue | 0.0 s | 15.0 s |
| quotePreparation | n/a | n/a |
| groth16ProofGeneration | 0.0 s | 0.0 s |
| groth16Verification | 0.0 s | 0.0 s |
| walletReview | 1.0 s | 1.0 s |
| buyerActionGroupProveAndSign | 3.0 s | 1.0 s |
| sellerActionGroupProveAndSign | 2.0 s | 1.0 s |
| coordinatorExecuteLive | n/a | n/a |
| assembleBroadcastInclude | 2.0 s | 1.0 s |
| inclusionReconciliation | 4.0 s | 2.0 s |
| receiptGeneration | 2.0 s | 1.0 s |
| confirmToReceipt | 14.0 s | 7.0 s |
| endToEnd | 15.0 s | 22.0 s |
| endToEndClientObserved | 15.0 s | 22.0 s |
| durability | 0.0 s | 0.0 s |

- P1: txid `5db70e16f90e73afad6b982d58b699c40466efc945b4ce17d0e3e3a9cebc9f3a` at height 401 (block `21cf5f58acd5a487…`), verify-tx included=True, reconciliation SETTLED
- P2: txid `b0bb96bfa457612de70929fd12eb7a00575f133c48f5db53e71f59b00dc53de9` at height 402 (block `0eb6dd3bee81fe23…`), verify-tx included=True, reconciliation SETTLED

## Not measured or approximate

- Halo2 proving and spend-authorization signing are one pinned tx-tool call per role; they are reported together (`*ActionGroupProveAndSign`).
- Broadcast and regtest block inclusion happen inside one coordinator call; they are reported together (`assembleBroadcastInclude`). Confirmation means inclusion in the block the coordinator mines, not consensus finality.
- Processes shorter than the sampling interval may be missed by the per-process sampler; their latency is still bounded by evidence-file times, but their RSS is not.
- Container memory is the cgroup charge (anonymous memory plus page cache and kernel memory); it is not the sum of process RSS.
- Purchases serialize on the single demo dealer wallet; concurrency levels measure concurrent submission and queueing, not parallel proving.
- The public web edge (Caddy, Next.js) is not run; requests use the real portal API inside the benchmark project.
