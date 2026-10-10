# Rooted lineage verifier: production integration (Phase 1)

> Historical Phase 1 design record. Root, terminal, chain history, and finality
> authentication were implemented in Phase 2. Phase 3 packages R2B-L1 locally
> and adds the fixture-backed HTTP API. For the current architecture and
> application contract, see [Phase 3 rooted-lineage API](phase3-api.md).

```text
PRODUCTION ZWA INTEGRATION PHASE 1: CORE LINEAGE VERIFIER
ROOT, TERMINAL AND CHAIN AUTHENTICATION: INTERFACES ONLY (NOT WIRED)
INDEPENDENT HUMAN REVIEW OF THE RESEARCH: PENDING
```

`lineage/` is the production verifier for ZWA rooted-lineage evidence. It implements `ENGINEERING_LINEAGE_CONTRACT.md` of the lineage research and nothing else. This page summarizes the boundary. The research artifacts are cited, not copied.

## 1. What is integrated

| Part | Status | Module |
|---|---|---|
| Canonical domain model (field elements, `cmx`, `nf`, tuples, 5,120-byte proofs, evidence) | done | `field.rs`, `evidence.rs` |
| Request container v2 (strict decode, `vk_id`, statement-keyed id) | done | `container.rs` |
| Frozen step verifier (R2B-L1, VK\*, `Params::new(11)`, identity check at load) | done, frozen code reused unmodified | `frozen.rs` |
| Design A (per-step single verification, joins, endpoints) | done | `design_a.rs` |
| `chain_ref` v1 (parse, encode, id) | done (ported encoding) | `chain_ref.rs` |
| Chain-context authentication (CR-1 … CR-9: view, network, block, root, finality) | **interface only, not wired** | `auth.rs`, `finality.rs` |
| Root authentication (R1 … R8) | **interface only, not wired** | `auth.rs` |
| Terminal authentication (T1 … T7) | **interface only, not wired** | `auth.rs` |
| Orchestration and verdicts | done | `service.rs`, `verdict.rs` |

Nothing in the protocol-core workspace (`crates/`) changed. The matcher hook (only `VERIFIED` may move a trade to `Verified`) is `LineageResult::permits_trade_verification()`; wiring it into the matcher belongs to Phase 2.

## 2. Authoritative research baseline

| Item | Value |
|---|---|
| Research repository / branch | `ZWA-Lineage-Research`, `research/post-h1f-engineering-baseline` (local, unpushed) |
| Research head | `3e7115d0e4c2f1cf36355f028447fc1b58290583` |
| Contract | `post-h1f/engineering-baseline/ENGINEERING_LINEAGE_CONTRACT.md` (also `INTEGRATION_BOUNDARY.md`, `HACKATHON_INTEGRATION_SCOPE.md`) |
| Frozen research baseline | `scripts/baseline-hash.sh`: 1,699 files, aggregate `7c05d1e9b3884e30fc901535f0d8ca95f9430204a379f0182c248416760ca4bc` |
| Freeze package | `post-h1f/freeze/`, git tree `2554af47101877b11ce24958397f9237e016c176` |
| Source pins | `post-h1f/freeze/SOURCE_PINS.json`, sha256 `2478e182…9585` |
| Proof stack | halo2 `ef3d0ba`, Orchard `cf801a5`, librustzcash `c5c232d`, `zcash_unstable="nu7"`, rustc 1.94.0 |

The research tree is read-only for production. `lineage/` reuses it through Cargo path dependencies, so the research checkout must sit next to this repository (`../ZWA-Lineage-Research`) and builds use the pinned offline cargo home (`../zsa-feasibility/.cargo-home`). `lineage/Cargo.lock` is the research Layer-B lock plus the `zwa-lineage` entry only, and every package of R2B-L1's own lock is present at the same version and source.

## 3. Supported relation and privacy boundary

One step is the frozen **`NormalLineageStepV2`** with public tuple `x_i = (cmx_old, nf_old, cmx_new)`, three strictly canonical 32-byte little-endian Pallas base-field elements. The private witness holds the old and new note openings and `nk`. The circuit proves `ρ_new = nf_old`, recomputes both commitments and the nullifier, and requires one hidden AssetBase in both openings. Only non-split, non-zero-value, non-native ZSA steps are provable (contract §A2, §A7).

**Hidden:** AssetBase, recipient, value, `ρ`, `ψ`, `rcm`, `nk` and every other note-opening value.

**Not hidden:** Design A gives the verifier every public tuple, so it sees each `cmx_old`, `nf_old`, `cmx_new`, the number of steps `N` and therefore the on-chain path. The issuance root note is public, and so is the terminal. Design A does not hide the transaction path. There is no recursion in this milestone.

The ρ-backbone is not value flow. A `VERIFIED` lineage says the terminal is on the ρ-backbone rooted at an authorized issuance. It does not establish ownership, unspentness, spend authority, value provenance or anything about a settlement spend (contract §A8, §I).

## 4. Design A

```text
E = (root_cmx, terminal_cmx, [(x_i, π_i)] for i in 0..N),   1 ≤ N ≤ 1024
```

Accepted (`LINEAGE_PROOF_VALID`) iff, in this order:

1. every field is canonical and every proof is exactly 5,120 bytes (at parse);
2. every `π_i` verifies for its own `x_i` under VK\* with one `SingleVerifier` call per step (no batch path);
3. `cmx_new[i] == cmx_old[i+1]` for every adjacent pair;
4. `cmx_old[0] == root_cmx`;
5. `cmx_new[N−1] == terminal_cmx`.

The first failure is returned with its step index. Steps are not reordered or deduplicated. A Design-A pass is **not** `VERIFIED`: it authenticates neither the root, nor the terminal, nor any chain.

## 5. Root versus terminal

| | Root | Terminal |
|---|---|---|
| What it must be | an issue note of an issue bundle in the verifier's chain, issuer signature re-verified, AssetBase derived, value > 0, listed as active in the verifier's provenance policy (contract §A3) | the `cmx` of an Orchard-ZSA **Action output** in the verifier's chain, bundle proof re-verified under the ZSA Action key, anchor an earlier final root (§A4) |
| Interface | `RootAuthenticator::authenticate_root(root_cmx, &AuthenticatedChainContext)` | `TerminalAuthenticator::authenticate_terminal(terminal_cmx, &AuthenticatedChainContext)` |
| Phase 1 adapter | `NotWiredRootAuthenticator` → `Unavailable(NotWired)` | `NotWiredTerminalAuthenticator` → `Unavailable(NotWired)` |

Both take an `AuthenticatedChainContext`, which only a chain-context authenticator can produce, so neither can run against an unauthenticated `chain_ref`. Neither a raw transaction nor a caller assertion is evidence.

Authentication successes (`AuthenticatedChainContext`, `AuthenticatedRoot`, `AuthenticatedTerminal`, `FinalizedReference`) carry a crate-private seal. Code outside `zwa-lineage` cannot construct them (enforced by `compile_fail` doctests), so an external adapter can report failures but never an authentication. In this build no adapter reports one, and the rooted path cannot return `VERIFIED`.

## 6. Verifier identity

The verifier owns its configuration. Callers cannot supply a key, parameters, a circuit or a relation version.

| Field | Value |
|---|---|
| `verifier_id` | `lineage-v2-l1` |
| relation | `NormalLineageStepV2` (circuit R2B-L1) |
| VK\* identity (sha256 of `Debug(vk.pinned())`) | `a2b349d7470504b6b5de3214f1783bc06d28c2f4e73111f9568652d0615b36b3` (equals research `R2B_L1_VK`) |
| parameters | `Params::new(11)`, sha256 of `Params::write` = `1eab6f93a080ce41b908d935c04bd2e3ed1ac23f277c15d11c499d56d28fa0f7` (production pin derived in Phase 1 from the frozen `params()`; the research recorded `k` only) |
| step proof length | 5,120 bytes |

`FrozenStepVerifier::load()` derives both from the frozen code and refuses to start on any mismatch. Every result carries this identity.

## 7. Wire format and identifiers

Request container v2:

```text
"ZWA-LINEAGE-REQ:" ‖ 0x02 ‖ vk_id(32) ‖ chain_ref(117) ‖ root_cmx(32) ‖ terminal_cmx(32) ‖ N u16 LE
  ‖ N × ( cmx_old ‖ nf_old ‖ cmx_new ‖ proof_len u16 LE ‖ proof )
```

Framing errors (tag, lengths, `N ∉ [1, 1024]`, proof field `∉ [1, 16384]`, trailing bytes) are `MALFORMED`. An unknown version is `UNSUPPORTED`. A `vk_id` other than VK\* is `DA_FOREIGN_VK`. A proof that is not exactly 5,120 bytes is `DA_NONCANONICAL_PROOF(i)`. Root and terminal authentication evidence are not in v2: their layout comes with the Layer-B port in Phase 2, as a new container version.

`chain_ref` is the research v1 encoding, unchanged (`"ZWA-LB-CHAINREF:" ‖ 0x01 ‖ network_id ‖ height u32 LE ‖ block_hash ‖ orchard_root`, 117 bytes, `id = BLAKE2b-256("ZWA_LB_ChainRefH", ·)`). It is Layer-B chain context only. It has no meaning inside the circuit.

`lineage_statement_id = BLAKE2b-256("ZWA_LineageStmt1", chain_ref ‖ root_cmx ‖ terminal_cmx ‖ N u16 LE ‖ tuples)`. It covers the statement and never the proof bytes (contract §G3).

## 8. Verdicts and failure semantics

| Status | Meaning |
|---|---|
| `VERIFIED` | every obligation passed: chain context, root, Design A, terminal, height context, finality. Unreachable in this build |
| `LINEAGE_PROOF_VALID` | Design-A-only mode passed |
| `REJECTED` | the evidence is false or malformed |
| `PENDING_AUTHENTICATION` | everything implemented passed; a required authentication layer is not wired in this build |
| `PENDING_FINALITY` | everything passed except finality |
| `INVALIDATED_BY_REORG` | the reference was reorganized away (`INVALIDATED`, `FINALITY_VIOLATION`) |
| `CHECKPOINT_TRUST_REQUIRED` | checkpoint-committed history with CP mode off |
| `UNSUPPORTED` | unknown version or mode, unsupported terminal |
| `INTERNAL_ERROR` | no verdict: identity mismatch, run-time dependency failure, panic |

Every failure carries a stable production code (`INVALID_STEP_PROOF`, `BROKEN_CONTINUITY`, …), the research-compatible code the contract requires (`DA_PROOF(1)`, `DA_JOIN(0)`, `ROOT_MALFORMED`, …) and the step index. Messages contain public data only.

Fail-closed rules: parse errors, missing data, unknown versions and modes, an unwired dependency, a run-time dependency failure, a panic in the frozen verifier or in any adapter, and an internal inconsistency never become `VERIFIED` or `LINEAGE_PROOF_VALID`. A definitive rejection is not masked by a pending dependency: a false proof is `REJECTED` even while authentication is unwired.

**`PENDING_AUTHENTICATION` is a Phase 1 addition to the contract's §H status list**, requested by the integration brief. It is produced only by `Unavailable(NotWired)`. A wired dependency that fails at run time keeps the contract's mapping (`VIEW_UNAVAILABLE` → `INTERNAL_ERROR`). Once Phase 2 wires all adapters, it is unreachable. Owner review should confirm this extension.

## 9. Differences from the research verifier

Checked by `tests/differential.rs` against the live frozen reference (`r3e` `design_a::verify`, `Mode::Single`) and the recorded rows. All other shared vectors agree exactly.

| Vector | Research | Production | Reason |
|---|---|---|---|
| proof with trailing bytes (DA-06) | `ACCEPT` | `DA_NONCANONICAL_PROOF(i)` | contract §G1/§G2 (finding A2AM-F4) |
| proof shorter than 5,120 bytes | `DA_PROOF(i)` | `DA_NONCANONICAL_PROOF(i)` | contract §G2 |
| non-canonical `root_cmx` (CB-15j) | Design-A reference: `DA_ROOT` | `ROOT_MALFORMED` | equals the research rooted object (CB-15j) |
| non-canonical `terminal_cmx` | `DA_TERMINAL` (after all proofs) | `TERM_MALFORMED` (at parse) | the brief requires terminal canonicalization before proof checks; same status |

All four reject on both sides. Production is never more permissive.

Observation, research not modified: in the A2a-M conformance output, rows `DA-01`…`DA-06` are labelled "frozen rooted N=3 fixture" but `a2am_conf.rs` builds them from the CKN proofs (`h1-rooted/fixtures/ckn/proofs`). The differential test compares them on the CKN lineage.

## 10. Fixtures

`lineage/tests/fixtures/` holds byte-for-byte copies of the frozen research step proofs and their public tuples only (no witnesses, no note openings), pinned by sha256 in `fixtures.json` and `SHA256SUMS`. A test checks every copy against its research original.

| Lineage | Source | Production result |
|---|---|---|
| N = 3 rooted fixture `x0 → out1 → out2 → out3` | `freeze/FROZEN_ROOTED_FIXTURE.md` | Design A: `LINEAGE_PROOF_VALID`; rooted: `PENDING_AUTHENTICATION` |
| CKN `x2 → X → C2 → C3` | `freeze/CKN_CHECKPOINT_COUNTEREXAMPLE.md` | Design A: `LINEAGE_PROOF_VALID` (as the research recorded); rooted: `PENDING_AUTHENTICATION`, never `VERIFIED` |
| branch B, F-FOREIGN | Layer-B fixtures | Design A: `LINEAGE_PROOF_VALID` |

CKN is a Layer-B counterexample: its proofs and joins are valid, and the research rejects it only through chain authentication (`CHECKPOINTED_HISTORY` with CP off, `UNKNOWN_FUTURE` on the full-validation view). Phase 1 cannot reproduce those codes. It guarantees only that CKN is not `VERIFIED`.

## 11. Build and test

```sh
lineage/scripts/check.sh            # fmt, clippy -D warnings, all tests, fixture pins, lock unchanged
cd lineage && CARGO_HOME=../../zsa-feasibility/.cargo-home \
  cargo run --offline --locked --release --example measure_n3
```

The protocol-core CI (`.github/workflows/protocol-core.yml`) does not build `lineage/`, because it needs the sibling research checkout and the pinned cargo home.

## 12. Known limitations

- Root, terminal and chain-context authentication, the chain view, the finality tracker and the provenance policy are not implemented. The rooted path stops at `PENDING_AUTHENTICATION` (Phase 2: contract M3–M7).
- Request container v2 carries no root or terminal authentication evidence yet.
- The build depends on the sibling research checkout and pinned cargo home; it is not in CI.
- The parameter digest is a production pin; the research recorded only `k = 11`.
- `PENDING_AUTHENTICATION` is an extension of the contract's status list (section 8).
- The research is **not independently human-reviewed** (RV-1 open). Every `VERIFIED` will be conditional on the research premises (contract preamble).
- No ZSA mainnet availability is implied.

## 13. Phase 2: snapshot-backed rooted path

`lineage::phase2` adds a verifier-owned `SnapshotChainView`. Each shipped view
(A4, A7, CPA, B8, CKN) is pinned by SHA-256. Loading checks the outer hash,
then the frozen Layer-B loader re-derives the network identity, header hashes,
previous-block links, transaction Merkle roots, ordered Orchard leaves and
roots from block and transaction bytes. A mismatch stops startup. The selected
view ID is attached to each verification result. These fixtures are regtest
snapshots; there is no live Zebra client in this phase.

The v3 request inserts fixed-length root evidence (1,169 bytes) and terminal
evidence (1,028 bytes) between the v2 endpoints and step count. It retains
the v2 VK identity, chain reference, public tuples and exact 5,120-byte step
proof rule. `decode(encode(request))` is byte-exact, and decoding rejects
trailing data, unknown versions, foreign VKs, missing evidence and malformed
lengths. v2 remains decodable with its Phase 1 semantics. History mode and
provenance policy are operator configuration, never request fields.

The rooted orchestrator keeps the engineering contract's order: chain_ref
identity, root, Design A, terminal, height context, and finality/history
policy. The chain reference binds network ID, height, block hash and Orchard
root to the selected view. Root authentication re-verifies the issue bundle,
including authorization, issued-ρ semantics and the operator-pinned public
provenance policy. Terminal authentication requires a ZSA Action output,
its Merkle path, valid Action proof and an earlier final anchor. These checks
use the immutable view; caller assertions cannot create authentication
successes.

The frozen research fixture policy is depth `K = 3`. This is a ZWA policy,
not consensus finality; a deeper consensus-valid reorg was demonstrated in
the research. The in-memory tracker carries observed references across view
changes. Accepted references become finalized at depth 3; a reference lost
after acceptance becomes `INVALIDATED_BY_REORG`, and loss after finalization
raises `FINALITY_VIOLATION` under that status. An unrelated tip extension
preserves the reference. Tracker state is not persisted across restarts.

FV requires the view's node checkpoint maximum to be zero. CP trust is off by
default. A checkpointed view with trust off returns
`CHECKPOINT_TRUST_REQUIRED`; an operator may explicitly opt into CKPT-R for
checkpoint-covered history. That trust asserts Action proof validity, anchor
history and nullifier uniqueness for covered blocks; endpoint checks do not
establish it. The known CKN counterexample cannot be configured as trusted.

Only `VERIFIED` permits execution through `TradePermission::from_result`.
Every other status denies. `VERIFIED` means canonical v3, the pinned VK and
parameters, all Design-A proofs and joins, authenticated chain reference,
authorized issuance root, valid ZSA terminal, satisfied history mode and
finality. It does not prove ownership, unspentness, settlement spend or value
flow, and the research still awaits independent human review.

The production adapter currently links the byte-identical sibling research
Layer-B crate, so the sibling checkout and pinned Cargo home remain build
requirements. The snapshots and policy fixture are public evidence only.
Deployment packaging, a persistent tracker, live node supervision, mainnet
policy and API orchestration remain outside this phase.
