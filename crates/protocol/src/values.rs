//! Canonical field-element-valued protocol values.
//!
//! Each of these is a distinct newtype over [`FieldElement`] so that a policy
//! root can never be passed where a trade commitment is expected, even though
//! both are BN254 scalars on the wire.

use core::fmt;

use crate::error::Result;
use crate::field::{FieldElement, FIELD_BYTES};
use crate::numbers::ZatoshiAmount;

macro_rules! field_newtype {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(FieldElement);

        impl $name {
            /// Wraps an already-validated canonical field element.
            #[must_use]
            pub const fn new(value: FieldElement) -> Self {
                Self(value)
            }

            /// Returns the underlying canonical field element.
            #[must_use]
            pub const fn value(self) -> FieldElement {
                self.0
            }

            /// Parses the canonical unpadded decimal representation.
            ///
            /// # Errors
            ///
            /// Propagates [`crate::error::ProtocolError::InvalidFieldEncoding`]
            /// for a malformed or unreduced value.
            pub fn from_decimal_str(text: &str) -> Result<Self> {
                FieldElement::from_decimal_str(text).map(Self)
            }

            /// Builds the value from its canonical 32-byte big-endian encoding.
            ///
            /// # Errors
            ///
            /// Propagates [`crate::error::ProtocolError::InvalidFieldEncoding`]
            /// for an unreduced value.
            pub fn from_be_bytes(bytes: [u8; FIELD_BYTES]) -> Result<Self> {
                FieldElement::from_be_bytes(bytes).map(Self)
            }

            /// Returns the canonical 32-byte big-endian encoding.
            #[must_use]
            pub const fn to_be_bytes(self) -> [u8; FIELD_BYTES] {
                self.0.to_be_bytes()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                fmt::Display::fmt(&self.0, f)
            }
        }
    };
}

field_newtype! {
    /// `H(ASSETV1, assetHi, assetLo)` over a canonical `AssetBase`.
    AssetCommitment
}

field_newtype! {
    /// A credential subject's private secret scalar.
    ///
    /// This is private witness material. It must never be logged or published.
    SubjectSecret
}

field_newtype! {
    /// `H(SUBJECT1, subjectSecret)`.
    SubjectCommitment
}

field_newtype! {
    /// `H(RECEIVR1, receiverLimb0, receiverLimb1, receiverLimb2)` over a
    /// canonical 43-byte raw Orchard payment address.
    ReceiverCommitment
}

field_newtype! {
    /// The recipient commitment bound into `TradeCommitmentV1`.
    ///
    /// Phase 0G derives it as `H(RCPBIND1, SubjectCommitment,
    /// ReceiverCommitment)`. It pins the exact intended receiver to the trade
    /// but does not prove spending-key control.
    RecipientCommitment
}

field_newtype! {
    /// `H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment)`.
    FeeCommitment
}

field_newtype! {
    /// `H(TRDA_V1, OfferedAssetCommitment, offeredAmount, RequestedAssetCommitment)`.
    TradePartA
}

field_newtype! {
    /// `H(TRDB_V1, requestedAmount, recipientCommitment, policyRoot)`.
    TradePartB
}

field_newtype! {
    /// `H(TRDM_V1, FeeCommitment, nonce, expiry)`.
    TradeMeta
}

field_newtype! {
    /// The frozen version-1 trade commitment.
    ///
    /// `TradeCommitmentV1 = H(TRADE_V1, TradePartA, TradePartB, TradeMeta)`.
    /// Both compliance proofs expose this exact public value, and matcher
    /// replay state is keyed by it. Field order and domain separation are ZWA
    /// protocol invariants defined by `zwa-commitments`.
    TradeCommitment
}

field_newtype! {
    /// The Merkle root of the allowed `(investorClass, jurisdiction)` policy
    /// tuples that the asset requires.
    PolicyRoot
}

field_newtype! {
    /// The issuer-published Merkle root of authorized issuance leaves.
    ///
    /// This is a public input of the provenance proof and a trusted value the
    /// matcher must authenticate outside the circuit.
    AuthorizedIssuanceRoot
}

field_newtype! {
    /// The credential authority's current Merkle root of active credentials.
    ///
    /// This is a public input of the eligibility proof. Revocation happens by
    /// root rotation, so freshness is a matcher responsibility.
    ActiveCredentialRoot
}

/// The native ZEC matcher fee committed by `TradeCommitmentV1`.
///
/// Both components enter the commitment through
/// `H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct MatcherFee {
    /// Fee amount in zatoshis of native ZEC.
    pub amount: ZatoshiAmount,
    /// Commitment to the matcher's fee receiver.
    pub recipient_commitment: RecipientCommitment,
}

impl MatcherFee {
    /// Builds a matcher fee from its committed components.
    #[must_use]
    pub const fn new(amount: ZatoshiAmount, recipient_commitment: RecipientCommitment) -> Self {
        Self {
            amount,
            recipient_commitment,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0G `trade.tradeCommitment`, the mandatory golden vector.
    const GOLDEN_TRADE_COMMITMENT: &str =
        "10187400613857124614980227259922066295752635539032972479692659299555113110306";

    #[test]
    fn decimal_round_trips_through_the_newtype() {
        let commitment = TradeCommitment::from_decimal_str(GOLDEN_TRADE_COMMITMENT).unwrap();
        assert_eq!(commitment.to_string(), GOLDEN_TRADE_COMMITMENT);
        assert_eq!(
            TradeCommitment::from_be_bytes(commitment.to_be_bytes()).unwrap(),
            commitment
        );
    }

    #[test]
    fn distinct_newtypes_do_not_unify() {
        // A policy root and a trade commitment can hold the same scalar yet
        // remain different protocol types; this is a compile-time property
        // exercised by constructing both from the same element.
        let element = FieldElement::from_u64(42);
        assert_eq!(PolicyRoot::new(element).value(), element);
        assert_eq!(TradeCommitment::new(element).value(), element);
    }
}
