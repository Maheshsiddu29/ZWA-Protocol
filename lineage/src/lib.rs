//! Production verifier for ZWA rooted lineage evidence.
//!
//! Implements `ENGINEERING_LINEAGE_CONTRACT.md` (provisional engineering
//! baseline, research branch `research/post-h1f-engineering-baseline`) for the
//! frozen relation `NormalLineageStepV2` and Design A:
//!
//! ```text
//! E = (root_cmx, terminal_cmx, [(x_i, π_i)]),   x_i = (cmx_old, nf_old, cmx_new)
//! ```
//!
//! accepted iff `N ≥ 1`, every `x_i` is canonical, every `π_i` verifies under the
//! frozen VK\* and parameters, `cmx_new[i] = cmx_old[i+1]`,
//! `cmx_old[0] = root_cmx` and `cmx_new[N−1] = terminal_cmx`.
//!
//! # Privacy boundary
//!
//! The step proofs hide every note opening (AssetBase, recipient, value, `ρ`,
//! `ψ`, `rcm`, `nk`). Design A does **not** hide the public tuples: the
//! verifier sees every `cmx_old`, `nf_old`, `cmx_new` and `N`, and therefore
//! the on-chain path. Nothing in this crate hides the path.
//!
//! # Status
//!
//! Work in progress: canonical domain model only.

#![cfg_attr(
    test,
    allow(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::panic,
        clippy::indexing_slicing
    )
)]

pub mod chain_ref;
pub mod evidence;
pub mod field;
pub mod verdict;

pub use chain_ref::{ChainRef, ChainRefError};
pub use evidence::{
    CanonicalStepProof, LineagePublicInputs, LineageStepProof, RawLineageEvidence, RawStep,
    RootedLineageEvidence, MAX_STEPS, STEP_PROOF_LEN,
};
pub use field::{CanonicalCmx, CanonicalFieldElement, CanonicalNullifier, CanonicalOrchardRoot};
pub use verdict::{FailureCode, FieldRole, LineageStatus, VerificationFailure};

/// BLAKE2b-256 with a 16-byte personalization.
pub(crate) fn blake2b_256(personal: &[u8; 16], data: &[u8]) -> [u8; 32] {
    let hash = blake2b_simd::Params::new()
        .hash_length(32)
        .personal(personal)
        .hash(data);
    let mut out = [0u8; 32];
    out.copy_from_slice(hash.as_bytes());
    out
}
