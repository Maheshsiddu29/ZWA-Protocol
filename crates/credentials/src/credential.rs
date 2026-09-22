//! Phase 1B active credential leaves.
//!
//! A credential authority issues one leaf per approved subject, and the active
//! credential root commits to those leaves. The Phase 1B leaf additionally
//! commits to the one Orchard receiver the authority approved for that subject:
//!
//! ```text
//! SubjectCommitment  = H(SUBJECT1, subjectSecret)
//! CredentialMeta     = H(CREDMETA, investorClass, jurisdiction, credentialExpiry)
//! CredentialLeafV2   = H(CRED_V2, credentialAuthorityCommitment, SubjectCommitment,
//!                        CredentialMeta, credentialNonce, ApprovedReceiverCommitment)
//! ```
//!
//! The approved receiver commitment is the canonical
//! [`zwa_commitments::receiver_commitment`] value — the same
//! `H(RECEIVR1, limb0, limb1, limb2)` the eligibility circuit feeds into the
//! trade's recipient binding. There is deliberately no second receiver
//! encoding: reusing one function is what makes the circuit equality
//! meaningful.
//!
//! # Phase 1B invariant
//!
//! An active credential authorizes exactly the receiver committed into its
//! leaf, so `credential(receiver A) + trade(receiver A)` is valid while
//! `credential(receiver A) + trade(receiver B)` is not. The eligibility circuit
//! enforces this by building the leaf it proves membership for from the very
//! same receiver commitment that produces the trade's recipient commitment.
//!
//! # What this is not
//!
//! Authority approval of a receiver is **not** proof that the trader currently
//! controls that receiver. Live wallet-control authentication is a separate
//! matcher concern and is not implemented here.

use core::fmt;

use zwa_commitments::{poseidon, receiver_commitment, subject_commitment, Domain};
use zwa_protocol::error::Result;
use zwa_protocol::{FieldElement, OrchardReceiverBytes, ReceiverCommitment, SubjectSecret};

use crate::values::{
    CredentialAuthorityCommitment, CredentialExpiry, CredentialNonce, InvestorClass, Jurisdiction,
};

/// `H(CREDMETA, investorClass, jurisdiction, credentialExpiry)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CredentialMeta(FieldElement);

/// A Phase 1B active credential leaf.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CredentialLeaf(FieldElement);

macro_rules! leaf_value {
    ($name:ident) => {
        impl $name {
            /// Returns the underlying canonical field element.
            #[must_use]
            pub const fn value(self) -> FieldElement {
                self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                fmt::Display::fmt(&self.0, f)
            }
        }
    };
}

leaf_value!(CredentialMeta);
leaf_value!(CredentialLeaf);

/// The credential fields a credential authority signs off on.
///
/// The approved receiver is held as canonical raw Orchard bytes so that the
/// receiver commitment can only be produced by the canonical encoder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActiveCredential {
    /// Commitment to the issuing credential authority.
    pub authority_commitment: CredentialAuthorityCommitment,
    /// Private subject secret this credential belongs to.
    pub subject_secret: SubjectSecret,
    /// Investor class, range-constrained to 8 bits by the circuit.
    pub investor_class: InvestorClass,
    /// Jurisdiction, range-constrained to 16 bits by the circuit.
    pub jurisdiction: Jurisdiction,
    /// Credential expiry; the circuit requires it to cover the trade expiry.
    pub credential_expiry: CredentialExpiry,
    /// Nonce distinguishing otherwise identical credentials.
    pub nonce: CredentialNonce,
    /// The one Orchard receiver this credential authorizes.
    pub approved_receiver: OrchardReceiverBytes,
}

impl ActiveCredential {
    /// Returns the canonical commitment to the authority-approved receiver.
    ///
    /// This is the same value the eligibility circuit binds into the trade's
    /// recipient commitment.
    #[must_use]
    pub fn approved_receiver_commitment(&self) -> ReceiverCommitment {
        receiver_commitment(&self.approved_receiver)
    }

    /// Computes `H(CREDMETA, investorClass, jurisdiction, credentialExpiry)`.
    #[must_use]
    pub fn meta(&self) -> CredentialMeta {
        CredentialMeta(hash4(
            Domain::CREDENTIAL_META_V1.as_field(),
            FieldElement::from_u64(self.investor_class.get()),
            FieldElement::from_u64(self.jurisdiction.get()),
            FieldElement::from_u64(self.credential_expiry.get()),
        ))
    }

    /// Computes the Phase 1B active credential leaf.
    #[must_use]
    pub fn leaf(&self) -> CredentialLeaf {
        let inputs = [
            Domain::CREDENTIAL_V2.as_field(),
            self.authority_commitment.value(),
            subject_commitment(self.subject_secret).value(),
            self.meta().value(),
            FieldElement::from_u64(self.nonce.get()),
            self.approved_receiver_commitment().value(),
        ];
        // Arity 6 is inside the supported Poseidon range, so this is total.
        CredentialLeaf(
            poseidon(&inputs)
                .expect("the Phase 1B credential leaf uses the supported Poseidon arity 6"),
        )
    }

    /// Reports whether this credential authorizes `receiver`.
    ///
    /// SECURITY: this is a convenience for credential-authority and fixture
    /// tooling. It is not the enforcement point. Enforcement happens inside the
    /// eligibility proof, which rebuilds the leaf from the receiver actually
    /// used by the trade; a host-side check like this one can be skipped by a
    /// malicious prover and proves nothing on its own.
    #[must_use]
    pub fn authorizes(&self, receiver: &OrchardReceiverBytes) -> bool {
        self.approved_receiver_commitment() == receiver_commitment(receiver)
    }
}

impl CredentialLeaf {
    /// Parses a leaf from its canonical unpadded decimal representation.
    ///
    /// # Errors
    ///
    /// Propagates [`zwa_protocol::ProtocolError::InvalidFieldEncoding`].
    pub fn from_decimal_str(text: &str) -> Result<Self> {
        FieldElement::from_decimal_str(text).map(Self)
    }
}

impl CredentialMeta {
    /// Parses metadata from its canonical unpadded decimal representation.
    ///
    /// # Errors
    ///
    /// Propagates [`zwa_protocol::ProtocolError::InvalidFieldEncoding`].
    pub fn from_decimal_str(text: &str) -> Result<Self> {
        FieldElement::from_decimal_str(text).map(Self)
    }
}

fn hash4(a: FieldElement, b: FieldElement, c: FieldElement, d: FieldElement) -> FieldElement {
    poseidon(&[a, b, c, d]).expect("credential metadata uses the supported Poseidon arity 4")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0G / Phase 1B `credential.subjectSecret`.
    const SUBJECT_SECRET: &str = "77112233445566778899";
    /// Phase 0G / Phase 1B `credential.credentialAuthorityCommitment`.
    const AUTHORITY: &str = "70707070707070";
    /// Phase 0G `receiver.institutionABytes` — the approved receiver.
    const RECEIVER_A: &str =
        "781671f8a41294c866d8161f3bf5f84a8fd2c328f91a2d085a66036acd59439731c36c4f1b99b4d64be233";
    /// Phase 0G `receiver.institutionBBytes` — a receiver the authority never approved.
    const RECEIVER_B: &str =
        "ba5a9b6828e14d720cc41e998917f5996635d1a7fa84448cb118f7b6f65068d380099e5cd54d98dd3917bb";

    fn credential(receiver_hex: &str) -> ActiveCredential {
        ActiveCredential {
            authority_commitment: CredentialAuthorityCommitment::from_decimal_str(AUTHORITY)
                .unwrap(),
            subject_secret: SubjectSecret::from_decimal_str(SUBJECT_SECRET).unwrap(),
            investor_class: InvestorClass::new(3),
            jurisdiction: Jurisdiction::new(840),
            credential_expiry: CredentialExpiry::new(2_050_000_000),
            nonce: CredentialNonce::new(41),
            approved_receiver: OrchardReceiverBytes::from_hex(receiver_hex).unwrap(),
        }
    }

    #[test]
    fn approved_receiver_commitment_is_the_canonical_receiver_commitment() {
        // Phase 1B must not introduce a second receiver encoding: the approved
        // receiver commitment is exactly the canonical one the trade uses.
        let receiver = OrchardReceiverBytes::from_hex(RECEIVER_A).unwrap();
        assert_eq!(
            credential(RECEIVER_A).approved_receiver_commitment(),
            receiver_commitment(&receiver)
        );
    }

    #[test]
    fn approving_a_different_receiver_changes_the_leaf() {
        // The decisive Phase 1B property at the leaf level: everything else is
        // identical, so only the approved receiver can move the leaf.
        let a = credential(RECEIVER_A);
        let b = credential(RECEIVER_B);
        assert_eq!(a.meta(), b.meta(), "only the receiver differs");
        assert_ne!(a.leaf(), b.leaf());
        assert_ne!(
            a.approved_receiver_commitment(),
            b.approved_receiver_commitment()
        );
    }

    #[test]
    fn a_credential_authorizes_only_its_approved_receiver() {
        let a = credential(RECEIVER_A);
        assert!(a.authorizes(&OrchardReceiverBytes::from_hex(RECEIVER_A).unwrap()));
        assert!(!a.authorizes(&OrchardReceiverBytes::from_hex(RECEIVER_B).unwrap()));
    }

    #[test]
    fn every_credential_field_changes_the_leaf() {
        let base = credential(RECEIVER_A);
        let leaf = base.leaf();

        let mut class = base;
        class.investor_class = InvestorClass::new(4);
        let mut juris = base;
        juris.jurisdiction = Jurisdiction::new(841);
        let mut expiry = base;
        expiry.credential_expiry = CredentialExpiry::new(2_050_000_001);
        let mut nonce = base;
        nonce.nonce = CredentialNonce::new(42);
        let mut authority = base;
        authority.authority_commitment =
            CredentialAuthorityCommitment::from_decimal_str("70707070707071").unwrap();
        let mut subject = base;
        subject.subject_secret = SubjectSecret::from_decimal_str("99887766554433221100").unwrap();

        for mutated in [class, juris, expiry, nonce, authority, subject] {
            assert_ne!(mutated.leaf(), leaf);
        }
    }

    #[test]
    fn the_leaf_is_deterministic() {
        assert_eq!(credential(RECEIVER_A).leaf(), credential(RECEIVER_A).leaf());
    }

    #[test]
    fn debug_output_never_discloses_the_subject_secret() {
        // `ActiveCredential` holds private witness material, so the derived
        // `Debug` must inherit the `SubjectSecret` redaction rather than
        // reintroduce the leak a credential authority would otherwise log.
        let rendered = format!("{:?}", credential(RECEIVER_A));
        assert!(!rendered.contains(SUBJECT_SECRET));
        assert!(rendered.contains("SubjectSecret(REDACTED)"));
    }
}
