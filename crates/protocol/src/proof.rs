//! Proof-verification interfaces for matcher integration.
//!
//! Both traits take the exact [`TradeCommitment`] as a required public input.
//! This crate provides no proving system or SNARK-verification backend. A trait
//! implementation is responsible for parsing and verifying the proof against
//! both supplied public inputs.

use crate::error::{ProofProblem, ProtocolError, Result};
use crate::values::{ActiveCredentialRoot, AuthorizedIssuanceRoot, TradeCommitment};

/// Maximum accepted length of an opaque proof container.
///
/// This is a storage bound, not a proving-system identifier.
pub const OPAQUE_PROOF_MAX_LEN: usize = 16 * 1024;

/// Uninterpreted proof bytes.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct OpaqueProof(Vec<u8>);

impl OpaqueProof {
    /// Wraps non-empty proof bytes of at most [`OPAQUE_PROOF_MAX_LEN`].
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::InvalidProof`] when the container is empty or
    /// larger than the bound.
    pub fn new(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() {
            return Err(ProtocolError::InvalidProof {
                reason: ProofProblem::Empty,
            });
        }
        if bytes.len() > OPAQUE_PROOF_MAX_LEN {
            return Err(ProtocolError::InvalidProof {
                reason: ProofProblem::TooLarge {
                    got: bytes.len(),
                    max: OPAQUE_PROOF_MAX_LEN,
                },
            });
        }
        Ok(Self(bytes.to_vec()))
    }

    /// Returns the uninterpreted bytes.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.0
    }
}

/// Why a proof verification returned invalid.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerificationProblem {
    /// The proof bytes could not be interpreted by the verifier backend.
    ProofMalformed,
    /// A public input did not match the statement the proof claims.
    PublicInputMismatch,
    /// The proof was well-formed but did not verify.
    ProofRejected,
}

/// Outcome of a provenance or eligibility verification.
///
/// Discarding this value silently treats an unverified — or actively rejected —
/// proof as acceptable, so it is `#[must_use]`. Every other fallible protocol
/// operation returns [`Result`](crate::error::Result), which carries the same
/// obligation.
#[must_use = "proof verification results must be checked"]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerificationResult {
    /// The proof verified against the supplied public inputs.
    Valid,
    /// The proof did not verify.
    Invalid {
        /// Why verification failed.
        reason: VerificationProblem,
    },
}

impl VerificationResult {
    /// Reports whether verification succeeded.
    #[must_use]
    pub const fn is_valid(self) -> bool {
        matches!(self, Self::Valid)
    }
}

/// Public inputs of the provenance proof.
///
/// The provenance circuit exposes exactly these two values:
/// `authorizedIssuanceRoot` and `tradeCommitment`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ProvenancePublicInputs {
    /// Issuer-published authorized issuance Merkle root.
    pub authorized_issuance_root: AuthorizedIssuanceRoot,
    /// The exact `TradeCommitmentV1` both compliance proofs must expose.
    pub trade_commitment: TradeCommitment,
}

/// Public inputs of the eligibility proof.
///
/// The eligibility circuit exposes exactly these two values:
/// `activeCredentialRoot` and `tradeCommitment`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EligibilityPublicInputs {
    /// Credential authority's current active-credential Merkle root.
    pub active_credential_root: ActiveCredentialRoot,
    /// The exact `TradeCommitmentV1` both compliance proofs must expose.
    pub trade_commitment: TradeCommitment,
}

/// Verifies an authorized-issuance provenance proof.
///
/// The matcher supplies its authenticated issuance root and the same
/// [`TradeCommitment`] it recomputed from the canonical intent. Implementations
/// must verify both values as public inputs; implementing this trait does not
/// itself establish proof validity.
pub trait ProvenanceVerifier {
    /// Verifies `proof` against the authorized issuance root and the exact
    /// trade commitment.
    fn verify(
        &self,
        authorized_issuance_root: AuthorizedIssuanceRoot,
        trade_commitment: TradeCommitment,
        proof: &OpaqueProof,
    ) -> VerificationResult;
}

/// Verifies a private recipient-eligibility proof.
///
/// The matcher supplies its authenticated active-credential root and the same
/// [`TradeCommitment`] used for provenance. Implementations must verify both
/// values as public inputs; implementing this trait does not itself establish
/// proof validity.
pub trait EligibilityVerifier {
    /// Verifies `proof` against the active credential root and the exact
    /// trade commitment.
    fn verify(
        &self,
        active_credential_root: ActiveCredentialRoot,
        trade_commitment: TradeCommitment,
        proof: &OpaqueProof,
    ) -> VerificationResult;
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0G golden `TradeCommitmentV1`.
    const PHASE_0G: &str =
        "10187400613857124614980227259922066295752635539032972479692659299555113110306";
    /// Phase 0F golden `TradeCommitmentV1`.
    const PHASE_0F: &str =
        "7409670081847436957289371955571360481923983184454289247710022466448715682310";
    /// Phase 0G `issuance.authorizedIssuanceRoot`.
    const ISSUANCE_ROOT: &str =
        "19309979006225485291788213177219598381134511159668519266888323889569746782051";
    /// Phase 0G `credential.activeCredentialRoot`.
    const CREDENTIAL_ROOT: &str =
        "7239536478138432754387625126231950010993505962177483323536139232738771167323";

    /// Test double that records the public inputs it was given.
    ///
    /// It does not run a SNARK. It exists to pin the interface: both verifiers
    /// require the exact [`TradeCommitment`].
    struct RecordingVerifier {
        expected_commitment: TradeCommitment,
    }

    impl ProvenanceVerifier for RecordingVerifier {
        fn verify(
            &self,
            _authorized_issuance_root: AuthorizedIssuanceRoot,
            trade_commitment: TradeCommitment,
            _proof: &OpaqueProof,
        ) -> VerificationResult {
            if trade_commitment == self.expected_commitment {
                VerificationResult::Valid
            } else {
                VerificationResult::Invalid {
                    reason: VerificationProblem::PublicInputMismatch,
                }
            }
        }
    }

    impl EligibilityVerifier for RecordingVerifier {
        fn verify(
            &self,
            _active_credential_root: ActiveCredentialRoot,
            trade_commitment: TradeCommitment,
            _proof: &OpaqueProof,
        ) -> VerificationResult {
            if trade_commitment == self.expected_commitment {
                VerificationResult::Valid
            } else {
                VerificationResult::Invalid {
                    reason: VerificationProblem::PublicInputMismatch,
                }
            }
        }
    }

    fn proof() -> OpaqueProof {
        OpaqueProof::new(&[1, 2, 3, 4]).unwrap()
    }

    #[test]
    fn opaque_proof_rejects_empty_and_oversized() {
        assert_eq!(
            OpaqueProof::new(b""),
            Err(ProtocolError::InvalidProof {
                reason: ProofProblem::Empty
            })
        );
        assert_eq!(
            OpaqueProof::new(&vec![1u8; OPAQUE_PROOF_MAX_LEN + 1]),
            Err(ProtocolError::InvalidProof {
                reason: ProofProblem::TooLarge {
                    got: OPAQUE_PROOF_MAX_LEN + 1,
                    max: OPAQUE_PROOF_MAX_LEN
                }
            })
        );
    }

    #[test]
    fn both_verifiers_require_the_exact_trade_commitment() {
        let expected = TradeCommitment::from_decimal_str(PHASE_0G).unwrap();
        let other = TradeCommitment::from_decimal_str(PHASE_0F).unwrap();
        let issuance = AuthorizedIssuanceRoot::from_decimal_str(ISSUANCE_ROOT).unwrap();
        let credential = ActiveCredentialRoot::from_decimal_str(CREDENTIAL_ROOT).unwrap();
        let verifier = RecordingVerifier {
            expected_commitment: expected,
        };

        assert_eq!(
            ProvenanceVerifier::verify(&verifier, issuance, expected, &proof()),
            VerificationResult::Valid
        );
        assert_eq!(
            EligibilityVerifier::verify(&verifier, credential, expected, &proof()),
            VerificationResult::Valid
        );
        assert_eq!(
            ProvenanceVerifier::verify(&verifier, issuance, other, &proof()),
            VerificationResult::Invalid {
                reason: VerificationProblem::PublicInputMismatch
            }
        );
        assert_eq!(
            EligibilityVerifier::verify(&verifier, credential, other, &proof()),
            VerificationResult::Invalid {
                reason: VerificationProblem::PublicInputMismatch
            }
        );
    }

    #[test]
    fn verification_results_carry_a_must_use_obligation() {
        // `#[must_use]` is enforced by the compiler, not at runtime, so this
        // test pins the behaviour that makes the attribute meaningful: an
        // Invalid outcome is a distinct value a caller has to branch on, never
        // something that silently resembles success.
        let rejected = VerificationResult::Invalid {
            reason: VerificationProblem::ProofRejected,
        };
        assert!(!rejected.is_valid());
        assert!(VerificationResult::Valid.is_valid());
        assert_ne!(rejected, VerificationResult::Valid);
    }

    #[test]
    fn public_input_structs_carry_the_shared_trade_commitment() {
        let commitment = TradeCommitment::from_decimal_str(PHASE_0G).unwrap();
        let provenance = ProvenancePublicInputs {
            authorized_issuance_root: AuthorizedIssuanceRoot::from_decimal_str(ISSUANCE_ROOT)
                .unwrap(),
            trade_commitment: commitment,
        };
        let eligibility = EligibilityPublicInputs {
            active_credential_root: ActiveCredentialRoot::from_decimal_str(CREDENTIAL_ROOT)
                .unwrap(),
            trade_commitment: commitment,
        };
        assert_eq!(provenance.trade_commitment, eligibility.trade_commitment);
    }
}
