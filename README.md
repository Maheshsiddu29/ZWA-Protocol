# ZWA Protocol

Privacy-preserving RWA protocol on Zcash for proving asset provenance and investor eligibility before atomic shielded settlement.

Public ledgers expose ownership and transaction information, while regulated assets still need issuer provenance and recipient policy enforcement. ZWA combines an authorized-issuance proof, a private eligibility proof, one exact trade commitment, and an atomic shielded settlement.

```text
PROVE PROVENANCE
        ↓
PROVE ELIGIBILITY
        ↓
VERIFY SAME TRADE
        ↓
ATOMIC SHIELDED SETTLEMENT
        ↓
ZEC MATCHER FEE
```

## Architecture

The Zcash/ZSA layer owns shielded value movement and transaction atomicity. Two Groth16 circuits prove issuance provenance and private recipient eligibility against the same `TradeCommitmentV1`. A compliant matcher authenticates signed roots, verifies both proofs, prevents application-level replay, authenticates recipient control, and constructs settlement only after every gate passes. See [architecture](docs/architecture.md), [trust model](docs/trust-model.md), and [threat model](docs/threat-model.md).

## Current status

Phase 0 demonstrated the experimental ZSA lifecycle, an atomic ZSA-to-ZSA swap, a shielded ZEC matcher payment in that transaction, authorized issuance provenance, exact trade binding, and recipient-specific private eligibility. The migrated sources are a reviewed feasibility baseline. The matcher, RFQ workflow, root authentication, recipient-control mechanism, settlement adapter, and UI have not been implemented in this repository.

> The ZIP-227/ZIP-228 and ZSA capabilities used here are experimental QEDIT branches. They are not generally available production Zcash mainnet features, and this project is not production ready.

## MVP

The demo will use one RWA class, one issuer, one credential authority, and one atomic trade. It will show an unauthorized asset blocked by provenance, an ineligible recipient blocked by eligibility, and a valid private trade settled atomically with a shielded ZEC matcher fee.

## Repository structure

- `circuits/`: migrated provenance and eligibility circuits plus attributed ZK-ORIGIN helpers
- `crates/`: reserved boundaries for protocol, commitment, credential, and Zcash adapter libraries
- `matcher/`: compliant matcher boundary
- `rfq/`: private RFQ and order-intent boundary
- `frontend/`: demo application boundary
- `tests/`: synthetic fixtures and adversarial schema tests
- `docs/`: architecture, trust, threat, security, feasibility, and decision records

## Setup and build

Phase 0H freezes structure and source semantics only. Reproducible product build commands and local development setup belong to Phase 1. Current JavaScript schema tests use the pinned dependencies in `package.json`; circuits require Circom 2.2.2 and must emit generated artifacts outside the repository or into ignored paths.

## Roadmap

1. Protocol Core Foundation: canonical encodings, commitments, root-signature structures, replay state, and proof interfaces.
2. Matcher and RFQ integration.
3. Experimental settlement adapter and deterministic end-to-end demo.
4. Frontend and security hardening.

## Work disclosure

### Pre-existing team technology

ZK-ORIGIN primitives and design work came from [ZKChainForge/zk-origin](https://github.com/ZKChainForge/zk-origin) at commit `eae3f3e19dd63562364149ecb000f2de7296d025`. Reused files retain the MIT license and attribution.

### Phase 0 Colosseum feasibility work

ZSA lifecycle validation, atomic swap validation, ZEC matcher-fee validation, the RWA provenance adapter, `TradeCommitmentV1`, the eligibility proof, recipient binding, and the combined matcher gate.

### ZWA Protocol product work

Clean protocol integration, matcher, RFQ workflow, root authentication, replay protection, settlement adapter, frontend/demo, and production-oriented testing and documentation.

No ZWA Protocol project license has been selected. **PROJECT LICENSE DECISION REQUIRED.**
