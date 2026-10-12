# Phase 4D owner review: multi-asset private RWA exchange

## Delivered scope

**NEW ZWA PROTOCOL WORK:** A local exchange with public `/exchange`, `/exchange/assets`, and individual asset pages; authenticated buyer portfolio, orders, and activity; isolated seller, issuer, and auditor areas; a judge page; three separately issued synthetic instruments; typed market, NAV, and private-quote adapters; and a repeatable Phase 4C.1 checkout runner for each asset. The public marketing site and its brand system remain. **PRE-EXISTING ZK-ORIGIN:** Poseidon and Merkle encodings referenced by the unchanged Phase 4C.1 native-ZEC authorization adapter retain their original attribution. No frozen circuit, proving key, tx-tool source, or verifier baseline was changed.

The supported experimental categories are **precious metals**, **government-bond-style fixed income**, and **private credit**. The registry type allows future commodities, Treasury bills, corporate bonds, funds, real estate, equities, trade finance, and separately classified RWAs, but no offer in those categories is claimed. The three listings explicitly disclaim underlying legal rights, backing, redemptions, regulated eligibility, and third-party sponsorship.

## Actual issuance and purchases

Each of three completed scripted test runs started a fresh disposable chain and issued all three instruments with **distinct AssetBases** and 100 seller-wallet units each. The selected instrument then passed the unchanged Phase 4C.1 Groth16 gate, separate buyer and seller test-signer authorization, shielded native-ZEC settlement, independent block/transaction checks, depth-three observation, and reconciled wallet scans. The three selected purchases were:

- Gold reference: 7 units for 10,000 zatoshis, txid `9287c3be6995d08896d1d8558b88081bb48d1fbb9f88528a00c6fc0679f855a2`; buyer 0 → 7 units, seller 100 → 93 units and 0 → 10,000 zatoshis.
- Treasury style: 5 units for 12,500 zatoshis, txid `58e342d0c717161cce299d2b8bcb6d798cce29d83311ebb39afec07e8238961d`; buyer 0 → 5, seller 100 → 95 and 0 → 12,500 zatoshis.
- Credit participation: 3 units for 8,500 zatoshis, txid `8140b4e68ef0e8b9487222de9acc892c602cea4196dec85df291a4ae07dac208`; buyer 0 → 3, seller 100 → 97 and 0 → 8,500 zatoshis.

The sanitized [recorded evidence](../exchange/recorded-runs.json) includes each actual AssetBase, issuance txid, quote digest, block hash, and scan effects. All three are **historical scripted test evidence**. Their nodes have stopped; current finality is unknown. No new human-approved Phase 4D transaction was performed. The human launcher requires direct owner input.

## Pricing and source trust

- **Gold:** Pyth Core XAU/USD feed ID `765d2ba906dbc32ca17cc11f5310a89e9ee1f6420508c63861f2f8ba4ee34bb2`, interpreted as USD per troy ounce. The adapter validates feed ID, positive integer mantissa, exponent, confidence, observation time, and a one-hour age limit. It uses Pyth's authenticated HTTPS API when `PYTH_API_KEY` is configured. No API key or outbound feed was available during these runs, so no live market number was shown. This path does not verify a signed update; it is indicative only.
- **Treasury-style:** An issuer NAV adapter verifies a pinned issuer HMAC, unit, time, nonce, and expiry. An NAV is not a Treasury yield. No issuer NAV was published in these purchase runs, and their fixed ZEC amounts were not derived from NAV.
- **Credit:** A private seller-quote adapter verifies a pinned seller HMAC, exact AssetBase, quantity, native-ZEC total, nonce, and short expiry. The completed scripted purchases used asset-specific fixed ZEC amounts chosen by the operator and independently approved by seller test signers. The adapter is not yet a live offer service.

The current checkout's QuoteV1 and combined proof bind AssetBase, quantity, exact native-ZEC payment, receivers, sponsor fee, height expiry, and nonce through the quote digest. They do **not** commit a market snapshot reference, a wall-clock quote timestamp, spread, or an authenticated NAV/private-quote record. There is no trusted ZEC/USD conversion path in this phase. Thus no price is advertised as oracle-executable. The new pricing adapters are provenance-aware read/reference components, not a claim of price-to-settlement attestation.

## Milestone status

- **M1:** Complete for this demo: three separately issued AssetBases and actual seller wallet inventory in every validation chain.
- **M2:** Adapter and negative tests complete; a live Pyth fetch was unavailable without an API key, and no real issuer NAV was published.
- **M3:** Asset-specific exact native-ZEC QuoteV1 amounts are executable; authenticated price snapshot to settlement binding remains open.
- **M4:** Complete in scripted test mode for all three asset classes, each using the same Phase 4C.1 Groth16 gate with newly generated issuance evidence.
- **M5:** Current-session buyer scan, seller scan, order, activity, and portfolio views exist. Persistent multi-asset holdings across several purchases on the same chain remain open.
- **M6:** Four server-authorized role areas exist; issuer issuance/NAV controls and transaction-specific auditor privacy are not implemented.
- **M7:** Optional private RFQ rooms are not implemented.
- **M8:** Judge sequence, sanitized real txids, and responsive screenshots are complete for historical scripted test runs. No new human-approved Phase 4D purchase or live market-feed screenshot is claimed.

## Access and privacy

Portal accounts are operator-provisioned with scrypt password hashes and a separate 256-bit session secret. Four-hour HttpOnly, SameSite=Strict cookies carry a server-verified role; role pages check the role on every request. The loopback-only login/logout endpoints reject cross-origin requests and cannot grant a role by URL parameter. Auditor portal receipt visibility requires an explicit txid allowlist. A live HTTP test exercised anonymous denial, wrong-origin denial, all four positive roles, cross-role denial, and auditor scope. The allowlisted records are also deliberately published as sanitized historical test evidence on the public judge page; this check does **not** establish privacy for individual investor transactions.

Portal access is **not wallet pairing**. It does not grant issuer mint authority, custody, or wallet signing. The buyer/seller wallet processes still own their keys and approve independently in the Phase 4C.1 path. The legacy `/demo` and checkout read-only API publish public local test evidence; they are not a private investor transaction API. Private deal rooms and participant-specific wallet scan isolation are not implemented.

## Tests and visual verification

- Three complete Phase 4D scripted regtest integrations: all passed with three distinct issuance identities in each run, one actual selected purchase per run, proof verification, independent txid inclusion, wallet reconciliation, and no wallet-secret leak findings.
- Existing Phase 4C.1 adversarial verifier: one positive plus 16 negative cases passed on fresh disposable Phase 4D terms. Cases include forged/stale roots, wrong AssetBase, receiver substitution, invalid proof, changed amount/fees, wrong network, nonce replay, and unrelated historical proof.
- Python checkout plus exchange tests: 16 passed, including stale/future/wrong-feed/low-confidence oracle rejection, NAV/quote tampering and expiry, duplicate AssetBase rejection, and existing read-only API security.
- Next.js TypeScript check and production build passed. Shell syntax checks passed.
- Desktop and mobile full-page screenshots are saved in `website/docs/screenshots/phase4d/`. They show the local exchange in a captured regtest session; any live-status claim expires when that disposable node stops.

## Research references and limits

The product patterns were taken from verified primary documentation, not copied interfaces: [Paxos PAXG documentation](https://docs.paxos.com/guides/stablecoin/paxg) separates gold ownership, custody, redemption, and transparency; [Ondo OUSG](https://ondo.finance/ousg) shows qualification and mint/redemption access; [Centrifuge pricing](https://docs.centrifuge.io/user/concepts/pricing/) distinguishes published share NAV from underlying asset pricing and settlement timing; [Chainlink data-feed guidance](https://docs.chain.link/data-feeds/overview) emphasizes heartbeat and freshness; [Pyth Core feed docs](https://docs.pyth.network/price-feeds/core/fetch-price-updates) define feed IDs and timestamped mantissa/exponent updates. None of those firms sponsors or backs these ZWA instruments.

Remaining required work for a production-like exchange: signed oracle verification or independently trusted conversion and exact price-to-proof commitment; persistent multi-order portfolio scans on one chain; issuer-controlled issuance and NAV publication with separate authority keys; seller offer management; verified local wallet pairing beyond portal login; transaction-specific private access; optional RFQ rooms; and current-chain receipt revalidation across sessions. The Phase 4C.1 development setup, synthetic authority credentials, matcher-enforced policy scope, and Action Group signing limitations remain unchanged.
