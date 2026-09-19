# ADR 0001: ZK-ORIGIN reuse

- **Status:** Accepted
- **Classification:** PRE-EXISTING TEAM TECHNOLOGY
- **Source:** https://github.com/ZKChainForge/zk-origin
- **Source commit:** `eae3f3e19dd63562364149ecb000f2de7296d025`
- **Applicable license:** MIT; the original notice is preserved at `circuits/shared/zk-origin/LICENSE`.

## Decision

Reuse only the small, already-proven Circom helper set needed by the Phase 0F/0G adapters. Do not migrate the EVM, Uniswap, Nova, recursive lineage, or unrelated authorization system.

## Reused files

- `circuits/src/lib/poseidon.circom` → `circuits/shared/zk-origin/poseidon.circom`
  - Source SHA-256: `bce02458040e3469729924a14bf17fc1fa1a0337cc978144173d4fcc159e3dae`
  - Destination SHA-256: `f8cd9fcf1fe916eab3979bf6d8e6c5f2f9e3e315bc5aa8408a2f0e465ccece82`
  - Purpose: Poseidon wrapper templates.
  - Modified: **YES**; import path relocation only.
- `circuits/src/lib/merkle.circom` → `circuits/shared/zk-origin/merkle.circom`
  - Source SHA-256: `e9e1f34ebbe0b5a944531d72887b5e1e96584ea1947f91ec6676d193db670c5c`
  - Destination SHA-256: `e9e1f34ebbe0b5a944531d72887b5e1e96584ea1947f91ec6676d193db670c5c`
  - Purpose: Merkle membership verifier.
  - Modified: **NO**.
- `circuits/src/lib/comparators.circom` → `circuits/shared/zk-origin/comparators.circom`
  - Source SHA-256: `2562fa414e05150e1984c75524d0a186590471ec4602a500f08eb23d21f4592a`
  - Destination SHA-256: `5eddb0f2bdc7bb9154e66268c3d18d966e21cac6f43daf43bb3d2d19d7b6c6d6`
  - Purpose: bounded comparison helper.
  - Modified: **YES**; import path relocation only.
- `circuits/src/lib/validators.circom` → `circuits/shared/zk-origin/validators.circom`
  - Source SHA-256: `22a5c62566e5acbdbf0f6d27192cb6c8630bcb628ab06cf26b7bd0f919298dcc`
  - Destination SHA-256: `1fb023d030b9ef75920557e8c269bbcb5334778eedcccb8826443825ace46de8`
  - Purpose: shared validation templates.
  - Modified: **YES**; import path relocation only.
- `circuits/src/lib/constants.circom` → `circuits/shared/zk-origin/constants.circom`
  - Source SHA-256: `5015178b6df285aee5676f9c0b33785d74e3f6fa030f3a81a86521d0f56966bf`
  - Destination SHA-256: `5015178b6df285aee5676f9c0b33785d74e3f6fa030f3a81a86521d0f56966bf`
  - Purpose: shared circuit constants.
  - Modified: **NO**.
- `LICENSE` → `circuits/shared/zk-origin/LICENSE`
  - Source SHA-256: `211c7da635445461d20de385f62ee437ab2c03583e79d34e1e8728ae0e44e5f5`
  - Destination SHA-256: `211c7da635445461d20de385f62ee437ab2c03583e79d34e1e8728ae0e44e5f5`
  - Purpose: MIT license notice.
  - Modified: **NO**.

## Attribution and work-period disclosure

ZK-ORIGIN is pre-existing ZKChainForge technology and is not represented as Colosseum-period work. The Phase 0 adapters and the clean ZWA integration are separately identified. Files whose destination hashes differ have only dependency import paths relocated; their templates, constraints, constants, and behavior are unchanged.
