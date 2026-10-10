//! The frozen R2B-L1 step verifier (contract §B1, §B2, M1).
//!
//! The circuit, `Params::new(11)`, `keygen_vk` and `verify` are the frozen
//! research code (`r2c-h1d/r2b-l1/src/prover.rs`), called unmodified. This
//! module only adds what the contract asks of the wrapper: the identity check
//! of the verifier's own key and parameters at load time, exact-length proofs
//! (enforced by the type it accepts), one `SingleVerifier` call per step, and
//! typed results. A panic anywhere inside the frozen code is caught and
//! becomes an internal error, never a verdict (§F2).
//!
//! Callers cannot supply a key, parameters, a circuit or a relation version:
//! the verifier owns its configuration.

use core::fmt;
use std::panic::{catch_unwind, AssertUnwindSafe};

use halo2_proofs::plonk::VerifyingKey;
use halo2_proofs::poly::commitment::Params;
use pasta_curves::vesta;
use sha2::{Digest, Sha256};
use zwa_lineage_r2b_l1::prover;
use zwa_lineage_r2b_l1::public_inputs::{PublicInputsV2, PublicInstance};

use crate::evidence::{LineageStepProof, STEP_PROOF_LEN};

/// The supported lineage relation.
pub const RELATION: &str = "NormalLineageStepV2";
/// Stable identifier of this verifier configuration (relation V2, circuit
/// R2B-L1).
pub const VERIFIER_ID: &str = "lineage-v2-l1";
/// Proof system the frozen verifier runs.
pub const PROOF_SYSTEM: &str =
    "halo2_proofs QED-it ef3d0ba (IPA/Vesta), plonk::verify_proof, SingleVerifier, Blake2bRead";
/// `k` of the frozen parameters, `Params::new(11)`.
pub const FROZEN_PARAMS_K: u32 = 11;

/// VK\* identity: sha256 of `format!("{:?}", vk.pinned())`
/// (`a2b349d7…36b3`, research `proofs.rs` `R2B_L1_VK`).
pub const FROZEN_VK_ID: [u8; 32] = [
    0xa2, 0xb3, 0x49, 0xd7, 0x47, 0x05, 0x04, 0xb6, 0xb5, 0xde, 0x32, 0x14, 0xf1, 0x78, 0x3b, 0xc0,
    0x6d, 0x28, 0xc2, 0xf4, 0xe7, 0x31, 0x11, 0xf9, 0x56, 0x86, 0x52, 0xd0, 0x61, 0x5b, 0x36, 0xb3,
];

/// Parameter identity: sha256 of the frozen `params()` (`Params::new(11)`)
/// serialized with `Params::write` (`1eab6f93…a0f7`). A production pin,
/// derived in integration Phase 1 from the frozen code with
/// `examples/frozen_identity.rs`; the research recorded `k` only.
pub const FROZEN_PARAMS_SHA256: [u8; 32] = [
    0x1e, 0xab, 0x6f, 0x93, 0xa0, 0x80, 0xce, 0x41, 0xb9, 0x08, 0xd9, 0x35, 0xc0, 0x4b, 0xd2, 0xe3,
    0xed, 0x1a, 0xc2, 0x3f, 0x27, 0x7c, 0x15, 0xd1, 0x1c, 0x49, 0x9d, 0x56, 0xd2, 0x8f, 0xa0, 0xf7,
];

/// Everything that identifies the accepted verifier. Carried in every result
/// so that a demo run or a later receipt is reproducible.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct VerifierIdentity {
    /// [`VERIFIER_ID`].
    pub verifier_id: &'static str,
    /// [`RELATION`].
    pub relation: &'static str,
    /// [`PROOF_SYSTEM`].
    pub proof_system: &'static str,
    /// VK\* identity.
    pub vk_id: [u8; 32],
    /// `k` of the parameters.
    pub params_k: u32,
    /// sha256 of the serialized parameters.
    pub params_sha256: [u8; 32],
    /// Exact step proof length.
    pub step_proof_len: usize,
}

impl VerifierIdentity {
    /// The frozen identity this build accepts.
    pub const FROZEN: Self = Self {
        verifier_id: VERIFIER_ID,
        relation: RELATION,
        proof_system: PROOF_SYSTEM,
        vk_id: FROZEN_VK_ID,
        params_k: FROZEN_PARAMS_K,
        params_sha256: FROZEN_PARAMS_SHA256,
        step_proof_len: STEP_PROOF_LEN,
    };
}

/// Why the verifier refused to start. Every variant is fail-closed: no
/// verifier, no verdicts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerifierInitError {
    /// The derived key is not VK\*.
    VkIdentityMismatch {
        /// Identity of the key that was derived.
        got: [u8; 32],
    },
    /// The derived parameters are not the pinned ones.
    ParamsIdentityMismatch {
        /// sha256 of the parameters that were derived.
        got: [u8; 32],
    },
    /// The parameters could not be serialized for their identity check.
    ParamsSerialization,
    /// The frozen key derivation panicked.
    KeyDerivationPanicked,
}

impl fmt::Display for VerifierInitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::VkIdentityMismatch { got } => {
                write!(f, "derived step VK {} is not VK*", hex::encode(got))
            }
            Self::ParamsIdentityMismatch { got } => {
                write!(
                    f,
                    "derived params {} are not the pinned params",
                    hex::encode(got)
                )
            }
            Self::ParamsSerialization => f.write_str("params could not be serialized"),
            Self::KeyDerivationPanicked => f.write_str("frozen key derivation panicked"),
        }
    }
}

impl std::error::Error for VerifierInitError {}

/// Result of one step verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum StepCheck {
    /// The proof verified for exactly this tuple under VK\*.
    Valid,
    /// The proof did not verify.
    Invalid,
    /// The verifier could not decide (caught panic, or the frozen instance
    /// parser disagreed with production canonicalization).
    Internal,
}

/// The frozen R2B-L1 verifier with its identity-checked key.
pub struct FrozenStepVerifier {
    params: Params<vesta::Affine>,
    vk: VerifyingKey<vesta::Affine>,
    identity: VerifierIdentity,
}

impl fmt::Debug for FrozenStepVerifier {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("FrozenStepVerifier")
            .field("identity", &self.identity)
            .finish_non_exhaustive()
    }
}

/// sha256 of `format!("{:?}", vk.pinned())`, as every research stage computes it.
#[must_use]
pub fn vk_identity(vk: &VerifyingKey<vesta::Affine>) -> [u8; 32] {
    Sha256::digest(format!("{:?}", vk.pinned()).as_bytes()).into()
}

/// sha256 of the serialized parameters.
///
/// # Errors
///
/// [`VerifierInitError::ParamsSerialization`] if serialization fails.
pub fn params_identity(params: &Params<vesta::Affine>) -> Result<[u8; 32], VerifierInitError> {
    let mut buf = Vec::new();
    params
        .write(&mut buf)
        .map_err(|_| VerifierInitError::ParamsSerialization)?;
    Ok(Sha256::digest(&buf).into())
}

impl FrozenStepVerifier {
    /// Derives the frozen parameters and key and checks both identities
    /// (contract §B2). Takes on the order of a second.
    ///
    /// # Errors
    ///
    /// Any identity mismatch or failure; the caller must then treat every
    /// request as `INTERNAL_ERROR`.
    pub fn load() -> Result<Self, VerifierInitError> {
        Self::load_checked(&FROZEN_VK_ID, &FROZEN_PARAMS_SHA256)
    }

    pub(crate) fn load_checked(
        expected_vk_id: &[u8; 32],
        expected_params_sha256: &[u8; 32],
    ) -> Result<Self, VerifierInitError> {
        let (params, vk) = catch_unwind(|| {
            let params = prover::params();
            let vk = prover::keygen_vk(&params);
            (params, vk)
        })
        .map_err(|_| VerifierInitError::KeyDerivationPanicked)?;
        let params_sha256 = params_identity(&params)?;
        if &params_sha256 != expected_params_sha256 {
            return Err(VerifierInitError::ParamsIdentityMismatch { got: params_sha256 });
        }
        let vk_id = vk_identity(&vk);
        if &vk_id != expected_vk_id {
            return Err(VerifierInitError::VkIdentityMismatch { got: vk_id });
        }
        Ok(Self {
            params,
            vk,
            identity: VerifierIdentity {
                vk_id,
                params_sha256,
                ..VerifierIdentity::FROZEN
            },
        })
    }

    /// The identity of the loaded verifier (equal to [`VerifierIdentity::FROZEN`]).
    #[must_use]
    pub const fn identity(&self) -> &VerifierIdentity {
        &self.identity
    }

    /// Verifies one step with the frozen `prover::verify` (one
    /// `SingleVerifier` call, no batching).
    pub(crate) fn verify_step(&self, step: &LineageStepProof) -> StepCheck {
        let x = PublicInputsV2 {
            cmx_old: *step.public_inputs.cmx_old.as_bytes(),
            nf_old: *step.public_inputs.nf_old.as_bytes(),
            cmx_new: *step.public_inputs.cmx_new.as_bytes(),
        };
        // The frozen parser must agree with production canonicalization. If it
        // ever does not, nothing is decided.
        let Ok(instance) = PublicInstance::parse(&x) else {
            return StepCheck::Internal;
        };
        let proof = step.proof.as_bytes();
        match catch_unwind(AssertUnwindSafe(|| {
            prover::verify(&self.params, &self.vk, proof, &instance)
        })) {
            Ok(true) => StepCheck::Valid,
            Ok(false) => StepCheck::Invalid,
            Err(_) => StepCheck::Internal,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_with_the_frozen_identity() {
        let v = FrozenStepVerifier::load().unwrap();
        assert_eq!(*v.identity(), VerifierIdentity::FROZEN);
    }

    #[test]
    fn refuses_to_start_on_any_identity_mismatch() {
        let mut wrong = FROZEN_VK_ID;
        wrong[0] ^= 1;
        assert!(matches!(
            FrozenStepVerifier::load_checked(&wrong, &FROZEN_PARAMS_SHA256),
            Err(VerifierInitError::VkIdentityMismatch { got }) if got == FROZEN_VK_ID
        ));
        let mut wrong = FROZEN_PARAMS_SHA256;
        wrong[31] ^= 1;
        assert!(matches!(
            FrozenStepVerifier::load_checked(&FROZEN_VK_ID, &wrong),
            Err(VerifierInitError::ParamsIdentityMismatch { got }) if got == FROZEN_PARAMS_SHA256
        ));
    }
}
