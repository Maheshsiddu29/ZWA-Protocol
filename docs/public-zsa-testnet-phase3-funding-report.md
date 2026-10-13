# Phase 3 public issuer faucet funding receipt

Observed 2026-10-13 00:53 UTC on `integration/public-zsa-testnet` at local
HEAD `de35672`. **NEW ZWA PROTOCOL WORK** uses the pre-existing community ZSA
Testnet faucet, Zebra and lightwalletd services. This records one owner-approved
public faucet claim, not a ZSA issuance or transfer.

## Review result: PASS for funding; stop before issuance

The owner explicitly approved **one public testnet faucet claim**. Immediately
before submission, the fresh issuer wallet had zero Orchard, Sapling and
transparent ZEC and no assets. The faucet advertised a healthy service,
0.01 tZEC per claim, a 300-second address cooldown, a 120-second payout batch,
and no pending claims. Its form accepted the locally validated, Orchard-only
unified receiving address for the fresh issuer wallet. The address is omitted
from this shared receipt; it remains owner-only wallet output.

Exactly one claim was submitted through the faucet's human interface. The
faucet first reported `Queued`, then `Sent` with transaction ID:

`f531d5fa5ee97c21ea36edd044312fd91c32095a3aedb854f7878a8ab5f37113`

An independent read-only lightwalletd `GetTransaction` initially returned that
transaction at height zero (mempool), with 9,379 raw bytes and SHA-256
`e78a9c6ece0e1d89ed857db461b11ae3802b986548f9b6fa231a40fdeafc8e50`.
After the next block, the same RPC returned height **202078**. A separate
`GetBlock(202078)` contained the exact internal-byte-order transaction ID at
index **1**, in block
`0298685122810cf643d7f725698a429116b38564f7062179d7dfd8710432571a`.
The independently checked public tip was that same block at height **202078**:
**1 observed confirmation**. This is a chain observation, not finality.

The issuer wallet then scanned through height **202078** and reported:

- Orchard ZEC: **1,000,000 zatoshis = 0.01 tZEC**;
- Sapling ZEC: **0**;
- transparent ZEC: **0**;
- ZSA assets: **none**.

The preflight and root-bound identity checks both matched chain `test`, NU7
branch `77190ad8`, genesis
`03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`,
and public block 1
`01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`.
The final tip hash matched the block above. The public endpoint is
`https://zsa.methyl.cc`. The deployed node does not attest its source commit;
the operator-stated source pin remains
`zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`.

## Isolation and next gate

The claim funded the fresh owner-only public root
`/private/tmp/zwa-pub-public-phase3a-20261012`, whose issuer wallet was created
for this public chain. No protected Phase 4E root, wallet database, key or
witness was used. No wallet secret or receiving address was copied into the
repository or a website response. No public RPC was used for a local lab
broadcast. No issuance or transfer was built or broadcast, and no deployment,
push, merge, PR or publication occurred.

This confirms Orchard faucet compatibility and the current funding balance.
It does **not** establish public acceptance of an experimental ZSA issuance or
transfer. The network has one operator and may reset, reorg or change its
deployed build. Before any issuance, recheck network continuity, wallet scan,
fee and expiry, prepare and inspect an exact transaction, then obtain a
**separate owner approval for that exact txid**. The present faucet approval
does not authorize that broadcast or any additional faucet claim.

No repository code or dependencies changed for this receipt. Verification was
live preflight, root identity, independent transaction and block RPC reads,
issuer scan and balance. No test suite or lint run was necessary for this
documentation-only change. No local commit was created.
