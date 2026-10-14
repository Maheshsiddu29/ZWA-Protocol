# ZWA benchmark: level 2 (PASS)

> **Test fixture, not a measurement.** Every number below comes from a synthetic fixture used to exercise the report generator.

- Run: `20261014T120000Z-L2-a1b2c3` on project `zwa-bench-fixture`
- Started 2026-10-14T12:00:00Z, ended 2026-10-14T12:10:00Z
- Source revision: `ffffffffffffffffffffffffffffffffffffffff`
- Engine image: `sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee`; Zebra image: `sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9`
- Concurrency: 2; transactions submitted: 2

## Result

**PASS**: no stop condition triggered

## Host

| Measure | Value |
|---|---|
| Total RAM | 8.00 GiB |
| MemAvailable at start / minimum | 5.00 GiB / 5.00 GiB |
| Swap used at start / max (growth) | 0 MiB / 0 MiB (0 MiB) |
| CPU mean / max | 20.0 % / 20.0 % |
| Load average (1 min) max | 0.5 |
| Kernel OOM kills during run | 0 |

3 samples, 0 sampling errors.

## Transactions

| Stage | P1 | P2 |
|---|---|---|
| Outcome | SETTLED_VERIFIED | SETTLED_VERIFIED |
| Final state | RECEIPT_AVAILABLE | RECEIPT_AVAILABLE |
| walletSetup | 4.0 s | 4.0 s |
| walletFunding | 2.0 s | 1.0 s |
| reservationRequest | 0.0 s | 0.0 s |
| quoteReadyIncludingQueue | 0.0 s | 15.0 s |
| quotePreparation | n/a | n/a |
| groth16ProofGeneration | 0.0 s | 0.0 s |
| groth16Verification | 0.0 s | 0.0 s |
| walletReview | 2.0 s | 1.0 s |
| buyerActionGroupProveAndSign | 2.0 s | 1.0 s |
| sellerActionGroupProveAndSign | 2.0 s | 1.0 s |
| coordinatorExecuteLive | n/a | n/a |
| assembleBroadcastInclude | 2.0 s | 1.0 s |
| inclusionReconciliation | 4.0 s | 2.0 s |
| receiptGeneration | 2.0 s | 1.0 s |
| confirmToReceipt | 14.0 s | 7.0 s |
| endToEnd | 15.0 s | 22.0 s |
| endToEndClientObserved | 15.0 s | 22.0 s |
| durability | n/a | n/a |

- P1: txid `71a2120bfadf768a64669faf02e0a442875a21a93aa5fcd4245bdc2301d7189f` at height 401 (block `84996859fe552a7b…`), verify-tx included=True, reconciliation SETTLED
- P2: txid `dde9bd8ba8cae7d5294447a5007cbbbfd77b9d18a2c4900e9a2b86af46c5df69` at height 402 (block `39049f86d81e4f56…`), verify-tx included=True, reconciliation SETTLED

## Not measured or approximate

- Halo2 proving and spend-authorization signing are one pinned tx-tool call per role; they are reported together (`*ActionGroupProveAndSign`).
- Broadcast and regtest block inclusion happen inside one coordinator call; they are reported together (`assembleBroadcastInclude`). Confirmation means inclusion in the block the coordinator mines, not consensus finality.
- Processes shorter than the sampling interval may be missed by the per-process sampler; their latency is still bounded by evidence-file times, but their RSS is not.
- Container memory is the cgroup charge (anonymous memory plus page cache and kernel memory); it is not the sum of process RSS.
- Purchases serialize on the single demo dealer wallet; concurrency levels measure concurrent submission and queueing, not parallel proving.
- The public web edge (Caddy, Next.js) is not run; requests use the real portal API inside the benchmark project.
