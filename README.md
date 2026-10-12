# ZWA Protocol

### Private assets. Verifiable origins.

**Prove where an asset came from. Verify the rules that matter. Explore private exchange on Zcash.**

A token can be transferred in seconds. That does not prove it was legitimately issued, that its history is authentic, or that its recipient satisfies the right conditions. And answering those questions should not require publishing every private note opening or credential.

**ZWA turns asset provenance into verifiable evidence.** It combines zero-knowledge lineage verification, chain-authenticated trust checks, earlier private-policy proof primitives, and an independently demonstrated experimental ZSA-for-ZEC checkout.

## The architecture

![ZWA Protocol Architecture](docs/images/zwa_architecture.svg)

**01 · Verify origin** — Follow supported private-note ancestry back to an authenticated issuance root.  
**02 · Check private rules** — Bind issuer provenance and recipient eligibility to one exact intended trade (earlier Groth16 workstream).  
**03 · Settle privately** — Explore independently authorized asset-for-ZEC exchange on an experimental shielded ledger.

> These are complementary capabilities, **not yet a single production-integrated trade pipeline**. The rooted-lineage verifier and recorded checkout are separate demonstrated workstreams.

## 01 / An asset needs a verifiable origin

**A valid proof is not necessarily a valid asset.** ZWA's rooted-lineage verifier answers a narrower, stronger question: *does this supported note lineage connect an authorized issuance root to a real terminal chain output under a configured chain-trust policy?*

The frozen Halo2 relation, `NormalLineageStepV2`, proves each supported transition using public inputs:

```text
xᵢ = (cmx_old, nf_old, cmx_new)

Private witness: note openings, AssetBase, note/nullifier secrets
Circuit constraint: ρ_new = nf_old
```

**Design A** verifies each proof and joins adjacent public commitments. **Layer B** then authenticates the issuance root, terminal Orchard-ZSA Action, pinned chain view, and finality/checkpoint policy.

```text
Halo2 step proofs ──> Public commitment joins ──> Authorized root
                                                    │
VERIFIED <── Finality / checkpoint policy <── Chain view + real terminal
```

A cryptographically correct proof with an untrusted root is **not** `VERIFIED`. An authenticated root without sufficient finality is **not** `VERIFIED` either. The verifier fails closed instead of upgrading missing evidence into success.

**Under the hood:** Halo2 · Rust · Orchard note primitives · canonical evidence v3 · frozen verifier identity (`lineage-v2-l1`) · **5,120-byte proofs per normal step**. This is a constrained, non-recursive ρ-linked lineage proof; public path tuples remain visible, while private note openings are hidden. Arbitrary swaps, splits and general ownership histories are not covered by this relation.

### Real verdicts, not a green checkmark animation

| Evidence / chain state | Verifier outcome |
|---|---|
| Supported rooted lineage with authenticated chain context (A7 fixture) | `VERIFIED` |
| Valid evidence, insufficient required chain depth (A4) | `PENDING_FINALITY` |
| Historical context requiring explicit checkpoint trust (CKN) | `CHECKPOINT_TRUST_REQUIRED` |
| Tampered proof, forged root, false terminal or invalidated chain | Rejected / non-success |

The Phase 2 validation report records **236 classified differential cases with zero unexplained divergences**. This is engineering regression evidence, not an independent audit.

## 02 / Prove eligibility without publishing the credential

Provenance answers *where the asset came from*. Policy answers *whether this particular recipient and trade satisfy issuer-defined conditions*.

ZWA's earlier **Circom / Groth16** work demonstrates two private statements:

- **Authorized issuance:** a hidden issuance leaf belongs to an issuer-approved Merkle root.
- **Recipient eligibility:** a hidden credential belongs to an authority-approved set and satisfies class, jurisdiction, validity and receiver-binding constraints.

Both statements use the same canonical **`TradeCommitmentV1`**, binding the exact assets, amounts, recipient, policy, matcher fee, nonce and expiration. That design prevents proof-splicing across unrelated trade intents. The application still has to authenticate signed roots, receiver control and replay state: **a ZK proof cannot replace those trust checks**.

This is a separate, historically demonstrated policy-proof foundation; it has **not** been evaluated as part of the recorded ZSA-for-ZEC purchase below.

## 03 / Two parties. One experimental private exchange.

ZWA has also demonstrated a **human-approved ZSA-for-shielded-ZEC purchase** on a pinned experimental Zebra regtest. The buyer and seller approved the same terms in separate signer processes, signed their own Action Groups, and a local coordinator assembled one accepted transaction. A sponsor independently paid the network fee.

| Recorded exchange | Observed result |
|---|---:|
| Buyer received | **7 ZSA units** |
| Buyer paid | **10,000 zatoshis** (0.0001 ZEC) |
| Seller received | **10,000 zatoshis** |
| Sponsor paid network fee | **5,000 zatoshis** |
| Accepted transaction | Block **103**, experimental regtest |

**Recorded transaction ID**

```text
aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd
```

Buyer and seller wallet scans showed the corresponding balance changes. Two additional local blocks were observed after acceptance. This is **historical evidence from a disposable test network**, not live mainnet finality or an active browser checkout.

**Important signer boundary:** on this pinned experimental stack, Action Group signatures do not bind every outer transaction field, and group expiry was not consensus-enforced. The demonstrated flow therefore uses explicit human approval and a one-shot, locally controlled coordinator—not reusable production spending authorization.

## What to inspect

ZWA is designed to make claims **falsifiable**. A verifier should give a precise negative result when proof or chain trust fails; a checkout receipt should say exactly what happened and when.

| Interface | Purpose |
|---|---|
| `POST /v1/lineage/verify` | Verify canonical evidence under pinned proof and chain policy |
| `GET /v1/verifier/info` | Inspect verifier identity and configuration |
| `GET /v1/demo/scenarios` · `POST /v1/demo/run` | Run backend-supported proof fixtures, when enabled |
| `GET /v1/checkout/sample-quote` | Inspect fee-inclusive experimental quote |
| `GET /v1/checkout/sessions/recorded-phase35h/receipt` | Inspect the recorded human-approved purchase receipt |

The Phase 3 verifier and checkout HTTP APIs were **owner-reported local implementations** in the October 11 sourcebook; their availability and current schemas must be checked in the running checkout. These endpoints are **not** claims of a public hosted service.

### Reproduce the available protocol core

```bash
# From the repository root
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The historical Circom circuits use **Circom 2.2.2** and pinned JavaScript dependencies. The Halo2 lineage workspace has a separate frozen toolchain and dependencies; see its own setup instructions when that workspace is available.

**Repository map:** [`crates/`](crates/) · [`circuits/`](circuits/) · [`tests/`](tests/) · [`docs/`](docs/) · [`matcher/`](matcher/) · [`rfq/`](rfq/) · [`frontend/`](frontend/)

For deeper design boundaries, see [architecture](docs/architecture.md), [trust model](docs/trust-model.md), and [threat model](docs/threat-model.md).

## Where ZWA goes next

**Prove origin reliably → enforce private rules at the application boundary → connect secure authorization to settlement.**

The next milestone is to integrate authenticated roots, the real verifier verdict, recipient/wallet authorization, and a reviewed settlement adapter into a reproducible end-to-end application. Supporting swap-output ancestry, splits, broader asset classes and production-grade authorization requires new work rather than a relabeling of the current proof.

The opportunity is larger than a single RWA demo: **an asset ecosystem where provenance can be challenged, rules can be checked, and sensitive financial details need not be made public by default.**

---

**Experimental status.** The ZSA/ZIP-227/ZIP-228 settlement work uses pinned QEDIT experimental branches, **not generally available production Zcash mainnet functionality**. The protocol has no independent security audit or established regulated-asset deployment. Lineage verification does not itself prove legal title, unspentness, or recipient compliance, and the recorded checkout has not been proven to satisfy the rooted-lineage or eligibility relation.

**Attribution.** Earlier ZK-ORIGIN components derive from [ZKChainForge/zk-origin](https://github.com/ZKChainForge/zk-origin) at commit `eae3f3e19dd63562364149ecb000f2de7296d025`; reused files retain their attribution and MIT notices. No project-wide ZWA license has been selected.

**ZWA Protocol — Private assets. Verifiable origins.**
