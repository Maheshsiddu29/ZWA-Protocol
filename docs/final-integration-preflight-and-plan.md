# Final integration checkpoint: Phase 3 preflight and Phases 4–8 plan

Observed 2026-10-13 UTC on `integration/public-zsa-testnet` after
Phase 2 commit `ed07b27`. **NEW ZWA PROTOCOL WORK**; the community node,
lightwalletd and Zkool fork are **PRE-EXISTING community tooling**. This is a
read-only public-network check and local preparation (client change `c9e5ea8`),
not a public transaction or hosted service. The Phase 4E experimental regtest
chain and its wallets were not opened or changed.

## Review verdict

**PARTIAL — Phase 3 preflight passed for chain identity and local wallet
isolation.** Public transaction acceptance, funding, and Orchard faucet payout
remain untested. Stop before faucet use. The three separate future decisions
are (1) faucet request, (2) exact issuance txid broadcast, and (3) exact
transfer txid broadcast. This prompt authorizes none of them.

## Exact read-only observations

Run the rootless command before every public preparation session:

```sh
/private/tmp/zwa-pub-build/tool-target/release/zwa-zsa-pub preflight
```

It reads `GetLightdInfo`, block 1, `GetLatestBlock`, then the tip by height
from the fixed TLS endpoint `https://zsa.methyl.cc`. It checks the compiled
chain pins, refuses an inconsistent tip, and creates no root or wallet. The
2026-10-13 observation returned:

- Chain name `test`; Zebra subversion `/Zebra:5.0.0/`; consensus branch
  `77190ad8` (NU7 / ZSA).
- Genesis `03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`;
  public block 1 `01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`.
  The lab block 1 is different: `0235e4311200276186238a8441c5bf44d4027055cb374da10b75f86fc0166d21`.
- Consistent live tip: height 202,063, hash
  `02ac41fab843b65b3588da23d94a48bab7ee2ce3ae87300fbd26d6cbe3157777`.
  The root binding a moment later observed height 202,064. Heights are
  observations, not stability or finality guarantees.
- The [network operator's source statement](https://forum.zcashcommunity.com/t/zsa-testnet/56884/16)
  identified `zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`
  on 2026-09-01. The live endpoint does **not** attest its build commit. The
  local node parser and lab were built from that exact revision.
- A read-only `GET https://faucet.zsa.methyl.cc/api/info` returned
  `healthy: true`, height 202,059, `amount: "0.01"`, 300-second address
  cooldown, 120-second batch interval, daily cap `10.0`, zero pending and
  zero dispensed today. These values can change. The [faucet page](https://faucet.zsa.methyl.cc/)
  lists `uregtest1…`, `zregtestsapling1…`, and `tm…` recipient formats. An
  actual Orchard-address payout has **not** been tested.

The pinned Zebra source calculates a 5,000-zatoshi marginal ZIP-317 fee with
two grace actions. The lab's exact public-compatible issuance and transfer
paid 20,000 and 15,000 zatoshis, respectively. The public endpoint has no
read-only API that proves it would currently accept a newly built transaction;
the source pin statement, live chain identity, and isolated consensus run
support compatibility, but only a separately approved public submission can
establish current live acceptance. No claim of live transaction-format
acceptance is made here.

### Validation of this checkpoint

- `CARGO_TARGET_DIR=/private/tmp/zwa-pub-build/tool-target cargo build --release --locked`
  passed for the public client.
- `CARGO_TARGET_DIR=/private/tmp/zwa-pub-build/tool-target cargo test --locked`
  passed: 14 tests, including the new rootless-preflight CLI regression.
- `CARGO_TARGET_DIR=/private/tmp/zwa-pub-build/tool-target cargo clippy --locked --all-targets -- -D warnings`
  passed.
- `npm run lint` (TypeScript) and `npm run build` passed in `website/`.
- A live rootless `preflight` passed. Supplying `--root` to `preflight`
  failed before connecting, as intended. `git diff --check` passed.
- `cargo fmt --all -- --check` remains unsuccessful: 908 lines of formatting
  diff across pre-existing public-client sources. This baseline was documented
  in the Phase 2 report; the newly added lines were checked against rustfmt's
  suggestions without reformatting unrelated code. Phase 8 owns the reviewed
  formatting cleanup.

No backend or protected-chain execution suite was rerun because neither
backend nor protected Phase 4E files were changed in this checkpoint.

## Fresh identity and funding boundary

After the chain pin check, `init --profile public` bound a new local root at
`/private/tmp/zwa-pub-public-phase3a-20261012` at height 202,064. Two separate
new wallets, `issuer` and `recipient`, were created there with different
database paths. The root and role directories are mode `0700`; both wallet
databases are mode `0600`. Each wallet's first sync reported zero Orchard,
Sapling and transparent ZEC and no assets. No address, seed, viewing key,
witness, wallet database or pending transaction was copied into the repository
or displayed on the website. The protected Phase 4E identities were not used.

The faucet's advertised 0.01 tZEC equals 1,000,000 zatoshis. The lab spent
35,000 on issuance plus transfer, leaving 965,000 from a directly funded
Orchard note. This is a **budget estimate**, not a public fee quote. Before
any issue plan, the owner must establish that an approved faucet claim really
funded the issuer's Orchard wallet; a transparent-only payout would require
a separately reviewed shield-only path. The current client has no public
shielding command. Do not prepare issuance on an unfunded root.

After a **separate owner faucet approval**, the owner can run locally:

```sh
ZWA_TOOL=/private/tmp/zwa-pub-build/tool-target/release/zwa-zsa-pub
ZWA_ROOT=/private/tmp/zwa-pub-public-phase3a-20261012
"$ZWA_TOOL" preflight
"$ZWA_TOOL" --root "$ZWA_ROOT" identity
"$ZWA_TOOL" --root "$ZWA_ROOT" address --role issuer --kind orchard
# Owner uses the faucet's human interface only after approving this claim.
"$ZWA_TOOL" --root "$ZWA_ROOT" balance --role issuer
```

The address is private owner output: do not paste it into an issue, chat,
receipt, log, or website. Following observed Orchard funding, the exact
prepare/inspect, exact-txid issuance broadcast, independent inclusion,
recipient scan, and separate exact-txid transfer broadcast commands are in
`docs/public-zsa-testnet-phase2-report.md`. Every preflight, plan, and
broadcast must recheck the live network and expiry. Issuance and transfer
are **not** a public atomic swap. There are no public ZWA txids yet.

## Existing integration audit

- **Buyer and seller:** The Next.js exchange has authenticated role sessions,
  a local CSRF and origin gate, buyer browse/Buy/quote confirmation, a seller
  offer and price desk, participant-scoped order and receipt routes, and a
  wallet pairing UI. `exchange.portal` checks chain identity, scan freshness,
  inventory and session-bound pairing. `exchange.order_service` advances a
  durable SQLite ledger through real proof verification, independent wallet
  approvals, submission, chain inclusion and reconciliation. The operator
  service does not sign. These are the Phase 4E local experimental regtest
  paths; recorded judge runs are explicitly historical.
- **Wallet trust:** `exchange.wallet_companion` is a separate local terminal
  signer. Pairing proves a local code was claimed in a session, not standard
  cryptographic browser-wallet ownership. The browser holds no spending key.
  The current site provisions fixed buyer and seller account IDs through
  environment variables; it is not a hosted multi-user wallet service.
- **Issuer and public transfer:** `/issuer` is a protected catalog page with
  no browser issuance action. The public ZSA client is an owner CLI with local
  signing and exact interactive broadcast gate. `/testnet` renders verified
  isolated-lab receipts and no public receipt. There is no browser transfer
  request, issuer signer API, or public transaction coordinator.
- **Proof and lineage:** The local checkout uses the pinned Groth16 native-ZEC
  authorization and checks exact quote inputs. Rooted Halo2 verification
  exists for pinned snapshot evidence, but the actual Phase 4E purchased note
  has no demonstrated authenticated issuance-root-to-terminal linkage. Its
  provenance must remain `UNSUPPORTED` until that exact link is proven.
- **RFQ and hosting:** `rfq/` is a reserved directory, not a private-room
  service. Local HTTP writes accept loopback `http` origins only; the session
  cookie is `secure: false`; the BFF spawns a local Python bridge tied to a
  `/private/tmp/zwa4e-*` root. This is intentional for the local demo and is
  unsuitable for public HTTPS deployment without a separate security design.
  No fresh hosted chain, judge sandbox, Docker Compose stack, or backup/restart
  runbook exists yet.

## Implementation sequence and acceptance gates

### Phase 4 — browser and backend integration

1. Preserve the existing buyer/seller ledger and independent human signer
   state machine. Add authenticated issuer and public-transfer *prepare* APIs
   against a separate public root, scoped to exact role, asset, amount,
   recipient, network, fee and expiry. Keep signing and broadcast behind
   explicit owner policy; no session ID may authorize them by itself.
2. Bind browser confirmation to the server plan digest and exact txid. Show
   chain, asset label, quantity, receiver, fee and expiry. Move UI state only
   from backend events; produce receipts only after node-byte, block and
   private scan checks. Keep exchange purchase and public transfer distinct.
3. Design disposable judge sessions with fresh wallet and chain identities,
   TTL, quotas, amount/destination/fee limits and explicit custodial
   disclosure. Retain read-only judge verification if safe signing is not
   approved. Test two isolated sessions, replay, cross-role access, expiry,
   proof rejection and concurrent reservations before a signing-policy gate.

### Phase 5 — institutional RFQ rooms

Build a separate regtest-only room ledger with authenticated institutions,
invites, optional read-only auditor, private quotes/counterquotes and exact
final-terms commitment. Changes invalidate approvals. Reuse the supported
Groth16 and two-signer atomic settlement path; keep room evidence
participant-scoped. Test guessed IDs, cross-room access, concurrent offers,
expiry and changed terms. Do not claim Community ZSA Testnet swap support.

### Phase 6 — rooted Halo2 purchased-note lineage

Map the actual Phase 4E output note commitment to authenticated issuance-root
evidence, ordered step proofs and a pinned chain view. Check root issuer,
AssetBase, terminal Action proof/anchor, network, finality, replay and
unsupported transitions. Only return `VERIFIED` for the exact purchased note
when every obligation passes; otherwise report `REJECTED`,
`PENDING_FINALITY` or `UNSUPPORTED` precisely. Add positive and adversarial
fixtures for the actual purchase path. Until then, retain the unsupported
claim on the UI and receipts.

### Phase 7 — hosting preparation, then separate deployment gate

Threat-model the BFF/API boundary and measure node, proof and rescan workload.
Prepare a local VM/Compose manifest with a fresh disposable regtest, private
RPC, persistent volumes, TLS termination, separate signer workers, secret
storage, rate limits, health checks, restart recovery and bounded sponsor
funding. Redesign origin/cookie policy for HTTPS and use a private authenticated
service interface instead of exposing the local subprocess bridge or Zebra
RPC. Test two guest sessions, concurrent purchases, failure recovery,
backup/restore and privacy before requesting billable infrastructure or
public-deployment approval.

### Phase 8 — release hardening

After functional gates, remove only confirmed scratch/debug artifacts,
preserve chain fixtures and history, audit ignored/generated files and
dependencies, fix formatting in narrow reviewed edits, and run the full Rust,
Python, circuit, website and integration suites. Audit public claims against
chain evidence. Update README, network matrix, API, custody, proof,
reproducibility, operations, limitations and the two demo scripts. No release
or publishing is implied by local test success.

## Current gate and risks

No public faucet request, issuance, transfer, mainnet operation, deployment,
push, merge or PR occurred. Public-network resets, a source-pin change,
Orchard faucet incompatibility, miner availability and transaction expiry
remain material risks. The next action requiring owner approval is a **single
faucet claim** to the fresh issuer identity. Issuance and transfer submissions
each need their own later exact-txid approvals.
