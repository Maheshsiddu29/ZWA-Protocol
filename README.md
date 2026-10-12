# ZWA Protocol

### Private assets. Verifiable origins.

**Zero-knowledge asset provenance, private policy verification, and experimental shielded settlement on Zcash.**

ZWA Protocol develops cryptographic infrastructure for assets whose origin must be verifiable without disclosing their underlying private note data. It combines a **rooted Halo2 lineage verifier**, earlier **issuer and recipient policy proofs**, and a separately demonstrated **ZSA-for-shielded-ZEC exchange**.

For tokenized real-world assets, recording a balance is not enough. An application also needs evidence of authorized issuance, valid provenance, and applicable transaction rules. ZWA addresses those requirements while preserving privacy at the proof-witness and settlement layers.

## 1. Overview

ZWA's architecture is organized around three capabilities:

| Capability | Question answered | Technical approach |
|---|---|---|
| **Verify Origin** | Does this supported asset-note lineage descend from an authorized issuance root? | Halo2 step proofs, authenticated root and terminal, pinned chain context |
| **Check Private Rules** | Is this issuance and recipient eligible for the exact intended trade? | Circom/Groth16 membership proofs, signed roots, `TradeCommitmentV1` |
| **Settle Privately** | Can independent parties exchange an experimental shielded asset for shielded ZEC atomically? | Separately approved ZSA Action Groups, local coordinator, experimental Zebra |

These are **complementary workstreams**. The rooted-lineage verifier and experimental purchase have been validated separately; the full provenance-to-policy-to-purchase pipeline is an integration objective.

## 2. Architecture

![ZWA Protocol Architecture](docs/images/zwa_architecture.svg)

The architecture separates cryptographic statements, application trust decisions, and transaction execution:

- **Proof layer:** validates supported note ancestry and, in the earlier policy track, hidden issuance or credential membership.
- **Verification layer:** checks proof identity, canonical evidence, authorized roots, chain-observed terminals, and finality/checkpoint policy. An isolated valid proof is not sufficient for acceptance.
- **Policy layer:** binds provenance and eligibility to an exact transaction intent, with issuer and credential-authority trust enforced by the application.
- **Settlement layer:** constructs an experimental shielded-asset exchange using separately controlled signers and a locally trusted coordinator.

The shared protocol direction is **Verify Origin → Check Private Rules → Settle Privately**. The arrows describe the target composition, not a claim that the accepted checkout transaction already passed both proof systems.

## 3. Rooted Provenance Verification

### 3.1 Halo2 lineage relation

The frozen `NormalLineageStepV2` circuit proves a supported Orchard/ZSA normal-lineage transition without revealing the private note openings. Each step has the public tuple:

```text
x_i = (cmx_old, nf_old, cmx_new)

Private witness: old/new note openings, AssetBase,
                 note/nullifier secrets including nk
Core relation:   rho_new = nf_old
```

The circuit checks the relevant note-commitment and nullifier consistency constraints. It covers a **specific non-split, nonzero-value, non-native ZSA ρ-linked relation**; it does not establish arbitrary asset flow, wallet ownership, unspentness, or lineage through swaps.

### 3.2 Design A + Layer B

Two independent sets of conditions determine the final verdict:

```text
DESIGN A — CRYPTOGRAPHIC CONTINUITY
Verify each frozen Halo2 step proof
    + require cmx_new[i] == cmx_old[i+1]
    + require first cmx_old == requested root
    + require last cmx_new == requested terminal

LAYER B — AUTHENTICATED CHAIN CONTEXT
Authenticate authorized issuance root
    + locate terminal Orchard-ZSA Action output
    + validate pinned chain view and history
    + apply finality and checkpoint-trust policy

=> VERIFIED only if all required obligations pass
```

Each frozen normal-step proof is **5,120 bytes**. The verifier uses a pinned identity (`lineage-v2-l1`), strict evidence parsing, and a verifier-owned key/configuration; it does not adopt a caller's supplied proof authority. Public `cmx`/`nf` tuples and path length remain visible. This is **per-step verification with public joins**, not recursion over a hidden path.

### 3.3 Fail-closed verdicts

| Evidence | Result |
|---|---|
| Valid rooted N=3 lineage under A7 chain policy | `VERIFIED` |
| Valid path, insufficient confirmation depth (A4) | `PENDING_FINALITY` |
| Valid path, historical checkpoint needs configured trust (CKN) | `CHECKPOINT_TRUST_REQUIRED` |
| Altered proof, invalid root or terminal | `REJECTED` / other non-success result |
| Invalidated chain observation | `INVALIDATED_BY_REORG` |

The Phase 2 validation record reports **236 classified differential cases, with zero unexplained divergences**. This measures bounded regression behavior, not independent security assurance.

## 4. Private Policy Verification

The earlier Circom/Groth16 workstream provides additional proof primitives for transaction-level rules:

- **Issuer provenance:** prove membership in an issuer-authorized issuance Merkle root without disclosing the private issuance leaf.
- **Recipient eligibility:** prove membership in an authority-approved credential set, with investor-class, jurisdiction, expiration, and receiver-binding conditions kept within the proof witness.
- **Exact trade binding:** both proof statements reference `TradeCommitmentV1`, which commits to offered/requested assets, amounts, recipient, policy, matcher fee, nonce, and expiration.

At the application boundary, signed-root authentication, freshness, wallet control, and replay checks remain mandatory. The shared commitment is designed to prevent reuse of a provenance proof with eligibility evidence from a different proposed trade.

**Scope:** these proofs were demonstrated in the historical feasibility/core track. Eligibility was **not evaluated** in the recorded purchase in Section 5, and these proofs are not Zcash consensus-level transfer restrictions.

## 5. Experimental Private Settlement

ZWA separately demonstrated a manually authorized **7-unit ZSA purchase for 10,000 zatoshis** (0.0001 shielded ZEC) on pinned experimental Zebra regtest software.

### 5.1 Transaction design

1. Buyer and seller independently review identical transaction terms, with terminal approval defaulting to reject.
2. Separate local signer processes approve and sign their own experimental Action Groups.
3. The one-shot coordinator combines the signed groups with a sponsor-funded network-fee group.
4. Experimental Zebra accepts the assembled transaction; independent wallet scans confirm the observed asset/payment changes.

| Observed effect | Recorded amount |
|---|---:|
| Buyer receives | **7 ZSA units** |
| Buyer pays | **10,000 zatoshis** |
| Seller receives | **10,000 zatoshis** |
| Seller delivers | **7 ZSA units** |
| Sponsor-funded network fee | **5,000 zatoshis** |
| Matcher fee in this run | **0 zatoshis** |

**Recorded regtest evidence**

```text
Transaction: aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd
Block:       103
Block hash:  7ece7eadb0fda860e9d2f49add753174da9a04d4e64a4a1c3ced90e15f0cd13a
```

Two subsequent local blocks were observed after acceptance. The original disposable chain is no longer a live source of finality; this is **recorded test evidence**, not a current mainnet transaction.

### 5.2 Authorization boundary

On the tested experimental branch, Action Group signatures do **not** cover all eventual outer-transaction fields, and group expiry was **not consensus-enforced**. ZWA therefore used human-approved, one-shot local execution rather than standing orders or production wallet authorization. The acquired swap output has **not** been established as satisfying the frozen lineage relation.

## 6. Demo & Validation

ZWA exposes two distinct evidence-driven demos, rather than simulating a single fully integrated trade.

### Demo A — ZWA Verify

Use the real verifier service to inspect a supported lineage statement and its exact verdict:

| Demo case | What to show |
|---|---|
| **Valid rooted lineage** | `valid-rooted-n3` → authenticated proof path and `VERIFIED` |
| **Proof tampering** | Modified proof → rejection without a false positive |
| **Root/terminal substitution** | Incorrect trust anchor or chain terminal → rejection |
| **Finality boundary** | A4 → `PENDING_FINALITY` rather than premature approval |
| **Checkpoint/reorg boundary** | Explicit checkpoint trust requirement or invalidated chain observation |

The backend's available scenario identifiers should be read from `GET /v1/demo/scenarios`; the listed names reflect reported fixtures, not guaranteed availability on every checkout.

### Demo B — ZWA Checkout

Inspect the **recorded** independently approved ZSA-for-ZEC exchange: quote, buyer/seller authorization, accepted txid, wallet-scan observations, block reference, and read-only receipt. The checkout API distinguishes historical execution evidence from current chain availability.

**Important:** Demo A verifies a supported rooted-lineage relation. Demo B shows a separate accepted experimental exchange. Demo B does **not** certify lineage or private recipient eligibility for the purchased note.

### Validation inventory

| Area | Evidence |
|---|---|
| Lineage verification | Frozen Halo2 proof and canonical evidence checks; Phase 2 snapshot and adversarial comparisons |
| Differential testing | 236 classified Phase 2 cases; zero unexplained divergences (recorded) |
| Checkout | Accepted human-approved transaction; independently observed buyer/seller wallet changes |
| API testing | Owner-reported Phase 3/3.5 local HTTP and negative-path checks |

## 7. Developer Interfaces

The interfaces below describe **reported local services**, not public hosted endpoints. Confirm the routes and schemas against the current checkout before integration.

| Method | Endpoint | Function |
|---|---|---|
| `GET` | `/health/ready` | Verifier readiness |
| `GET` | `/v1/verifier/info` | Pinned verifier identity and configuration |
| `POST` | `/v1/lineage/verify` | Canonical evidence verification and chain-trust verdict |
| `POST` | `/v1/trade/authorize` | Application authorization verdict; **not** a broadcast |
| `GET` | `/v1/demo/scenarios` | Available deterministic backend scenarios |
| `POST` | `/v1/demo/run` | Execute a selected verifier fixture |
| `GET` | `/v1/checkout/sample-quote` | Example ZSA/ZEC terms and fee attribution |
| `GET` | `/v1/checkout/sessions/recorded-phase35h` | Recorded checkout session |
| `GET` | `/v1/checkout/sessions/recorded-phase35h/receipt` | Historical transaction receipt |

Example local checkout evidence inspection (when the `checkout/` module is present):

```bash
python3 -m checkout.api --demo --port 18745 --rpc-port 18255

# In another terminal:
curl -sS http://127.0.0.1:18745/v1/checkout/sample-quote
curl -sS http://127.0.0.1:18745/v1/checkout/sessions/recorded-phase35h/receipt
```

This API is **read-only**. It does not initiate a browser purchase or expose private signer keys. Full request/response contracts, where available, belong in `docs/phase3-api.md` and `docs/checkout-ui-api-contract.md`.

## 8. Repository & Development

| Directory | Responsibility |
|---|---|
| [`crates/`](crates/) | Rust protocol types, canonical commitments, credential structures |
| [`circuits/`](circuits/) | Earlier Circom/Groth16 provenance and eligibility work |
| [`lineage/`](lineage/) | Separate frozen Halo2 proof/verifier workspace, where published |
| [`matcher/`](matcher/) · [`rfq/`](rfq/) | Application matcher and private-intent design |
| [`docs/`](docs/) | Architecture, proofs, security and experimental evidence |
| [`checkout/`](checkout/) | Experimental checkout/evidence service, where published |

Protocol-core checks from the repository root:

```bash
git clone https://github.com/Maheshsiddu29/ZWA-Protocol.git
cd ZWA-Protocol
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The Halo2 lineage workspace maintains **separate frozen dependencies and tooling**. Where available, follow `lineage/README.md` and `lineage/scripts/check.sh` rather than substituting arbitrary upstream dependency versions.

**Primary stack:** Rust, Halo2, Orchard note primitives, Circom/Groth16, Poseidon, experimental QEDIT ZSA/Zebra tooling, Python read-only checkout services, and Next.js UI work.

## 9. Development Roadmap

ZWA is being developed alongside the evolution of Zcash's privacy-preserving asset infrastructure. The protocol already has working building blocks in rooted provenance verification, earlier private-policy proofs, and experimental asset settlement. The next phase focuses on connecting these capabilities as the underlying Zcash/ZSA stack and its transaction interfaces mature.

| Direction | Development focus |
|---|---|
| **Unified verification experience** | Bring authenticated lineage evidence, reproducible adversarial cases, and precise verifier verdicts into a developer-facing workflow. |
| **Private policy integration** | Extend issuer- and credential-root authentication, exact-trade binding, and application-level eligibility checks into the evolving transaction flow. |
| **Private asset settlement** | Evolve the independently approved ZSA-for-shielded-ZEC experiment toward stronger wallet authorization and settlement interfaces as Zcash asset primitives mature. |
| **Broader asset coverage** | Extend supported lineage relations beyond the current normal-step class, including evaluation of swap outputs, split flows, and additional asset structures. |
| **Ecosystem interoperability** | Align proof formats, verifier interfaces, asset tooling, and operational safeguards with emerging Zcash/ZSA capabilities and developer standards. |

The objective is a modular foundation for verifying asset origins and enforcing application-level transaction rules without requiring the public disclosure of sensitive witness data.

## 10. Protocol Maturity & Ecosystem Alignment

ZWA's current technical results span three independently validated workstreams: a frozen Halo2 rooted-lineage verifier with chain-context authentication, earlier zero-knowledge issuer/eligibility proof components, and a recorded human-approved ZSA-for-shielded-ZEC exchange on an experimental regtest stack.

These components establish concrete foundations for ZWA's architecture. Their integration will advance in parallel with the Zcash ecosystem's evolving support for custom shielded assets, transaction authorization, and developer infrastructure.

**Current implementation scope:** the lineage verifier proves a specified normal-step ancestry relation, not arbitrary ownership or value history; the recorded checkout is a separate settlement experiment and does not itself establish lineage or investor eligibility for its purchased note. The tested experimental Action Group authorization and expiry semantics require further protocol-level evolution before supporting reusable production approvals. Cryptographic review, deployment-specific security engineering, and applicable asset/custody requirements remain part of any production rollout.

ZWA is designed to remain compatible with that progression: preserve the proven verification core, expand supported relationships and policies, and adopt stronger Zcash asset-settlement primitives as they become available.

**Acknowledgment:** Earlier ZK-ORIGIN-derived work is attributed to [ZKChainForge/zk-origin](https://github.com/ZKChainForge/zk-origin) (source commit `eae3f3e19dd63562364149ecb000f2de7296d025`); existing attribution and licensing notices are retained.
