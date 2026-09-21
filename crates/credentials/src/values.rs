//! Canonical credential and eligibility-policy value types.
//!
//! These are the private witness values of the frozen eligibility circuit, and
//! each type carries exactly the range that circuit constrains:
//!
//! | Value | Circuit constraint | Range |
//! |---|---|---|
//! | [`InvestorClass`] | `Num2Bits(8)` | `0..=255` |
//! | [`Jurisdiction`] | `Num2Bits(16)` | `0..=65535` |
//! | [`CredentialNonce`] | `Num2Bits(64)` | `0..=u64::MAX` |
//! | [`CredentialExpiry`] | `Num2Bits(64)` | `0..=u64::MAX` |
//!
//! The class and jurisdiction codes are deliberately narrower than the 64-bit
//! commitment fields, so they are stored in `u8` and `u16` and a wider value is
//! unrepresentable rather than merely discouraged. A value outside these ranges
//! could never satisfy the circuit, so rejecting it here turns a late, opaque
//! constraint failure into a typed protocol error.
//!
//! The protocol still does not freeze a policy language: which `(class,
//! jurisdiction)` tuples are permitted is determined by the asset's policy tree,
//! not by these types.

use core::fmt;

use zwa_protocol::error::{CredentialCodeField, ProtocolError, Result};
use zwa_protocol::{FieldElement, UnixSeconds};

macro_rules! u64_newtype {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(u64);

        impl $name {
            /// Wraps an unsigned 64-bit value.
            #[must_use]
            pub const fn new(value: u64) -> Self {
                Self(value)
            }

            /// Returns the underlying unsigned 64-bit value.
            #[must_use]
            pub const fn get(self) -> u64 {
                self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

u64_newtype! {
    /// Application nonce distinguishing otherwise identical credentials.
    CredentialNonce
}

/// Generates a credential policy code whose width matches its circuit
/// range constraint.
///
/// The narrow integer is the representation, so an out-of-range code cannot be
/// constructed at all through [`new`](InvestorClass::new); `from_u64` is the
/// checked entry point for values arriving as untyped 64-bit integers.
macro_rules! policy_code {
    ($(#[$meta:meta])* $name:ident, $repr:ty, $field:expr) => {
        $(#[$meta])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name($repr);

        impl $name {
            /// Bit width the frozen eligibility circuit constrains this code to.
            pub const BITS: u32 = <$repr>::BITS;

            /// Largest code the frozen eligibility circuit accepts.
            pub const MAX: u64 = <$repr>::MAX as u64;

            /// Wraps a code that is in range by construction.
            #[must_use]
            pub const fn new(value: $repr) -> Self {
                Self(value)
            }

            /// Checks and wraps a code supplied as an unsigned 64-bit integer.
            ///
            /// This is the entry point for fixtures, wire formats, and any
            /// other source that has not already narrowed the value.
            ///
            /// # Errors
            ///
            /// Returns [`ProtocolError::CredentialCodeOutOfRange`] when `value`
            /// exceeds [`MAX`](Self::MAX), because such a code could never
            /// satisfy the eligibility circuit.
            pub fn from_u64(value: u64) -> Result<Self> {
                if value > Self::MAX {
                    return Err(ProtocolError::CredentialCodeOutOfRange {
                        code: $field,
                        bits: Self::BITS,
                    });
                }
                Ok(Self(value as $repr))
            }

            /// Returns the code as the unsigned 64-bit value the circuit hashes.
            #[must_use]
            pub const fn get(self) -> u64 {
                self.0 as u64
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

policy_code! {
    /// Private investor-class code committed inside a credential leaf.
    ///
    /// The eligibility circuit constrains this with `Num2Bits(8)`, so the
    /// valid range is `0..=255`.
    InvestorClass, u8, CredentialCodeField::InvestorClass
}

policy_code! {
    /// Private jurisdiction code committed inside a credential leaf.
    ///
    /// The eligibility circuit constrains this with `Num2Bits(16)`, so the
    /// valid range is `0..=65535`. ISO 3166-1 numeric codes fit comfortably.
    Jurisdiction, u16, CredentialCodeField::Jurisdiction
}

/// Expiry of a credential, in unsigned Unix seconds.
///
/// The eligibility circuit requires `credentialExpiry >= trade expiry`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CredentialExpiry(UnixSeconds);

impl CredentialExpiry {
    /// Wraps an expiry given in Unix seconds.
    #[must_use]
    pub const fn new(seconds: u64) -> Self {
        Self(UnixSeconds::new(seconds))
    }

    /// Returns the expiry instant.
    #[must_use]
    pub const fn instant(self) -> UnixSeconds {
        self.0
    }

    /// Returns the expiry as unsigned Unix seconds.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }

    /// Reports whether this credential still covers `trade_expiry`.
    #[must_use]
    pub const fn covers_trade_expiry(self, trade_expiry: UnixSeconds) -> bool {
        self.0.get() >= trade_expiry.get()
    }
}

impl fmt::Display for CredentialExpiry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Commitment to the configured credential authority.
///
/// This is a BN254 scalar inside the credential leaf, not a signature key.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CredentialAuthorityCommitment(FieldElement);

impl CredentialAuthorityCommitment {
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
    /// Propagates [`ProtocolError::InvalidFieldEncoding`].
    pub fn from_decimal_str(text: &str) -> Result<Self> {
        FieldElement::from_decimal_str(text).map(Self)
    }
}

impl fmt::Display for CredentialAuthorityCommitment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(&self.0, f)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0G `credential.investorClass` / `codes.investorClass.QUALIFIED_INSTITUTION`.
    const QUALIFIED_INSTITUTION: u64 = 3;
    /// Phase 0G `credential.jurisdiction` / ISO 3166-1 numeric US.
    const US: u64 = 840;
    /// Phase 0G `credential.credentialExpiry`.
    const CREDENTIAL_EXPIRY: u64 = 2_050_000_000;
    /// Phase 0G `trade.expiry`.
    const TRADE_EXPIRY: u64 = 2_000_000_000;

    #[test]
    fn phase0g_credential_codes_are_representable() {
        // The Phase 0G fixture values must keep working unchanged, including
        // through the checked u64 entry point a fixture reader would use.
        assert_eq!(
            InvestorClass::from_u64(QUALIFIED_INSTITUTION)
                .unwrap()
                .get(),
            3
        );
        assert_eq!(Jurisdiction::from_u64(US).unwrap().get(), 840);
        assert_eq!(InvestorClass::new(3).get(), 3);
        assert_eq!(Jurisdiction::new(840).get(), 840);
        assert_eq!(CredentialNonce::new(41).get(), 41);
        assert_eq!(
            CredentialAuthorityCommitment::from_decimal_str("70707070707070")
                .unwrap()
                .to_string(),
            "70707070707070"
        );
    }

    #[test]
    fn investor_class_is_bounded_by_the_circuit_num2bits_8_constraint() {
        assert_eq!(InvestorClass::BITS, 8);
        assert_eq!(InvestorClass::MAX, 255);
        assert_eq!(InvestorClass::from_u64(0).unwrap().get(), 0);
        assert_eq!(InvestorClass::from_u64(255).unwrap().get(), 255);
        for rejected in [256, 1_000, u64::MAX] {
            assert_eq!(
                InvestorClass::from_u64(rejected),
                Err(ProtocolError::CredentialCodeOutOfRange {
                    code: CredentialCodeField::InvestorClass,
                    bits: 8
                }),
                "investor class {rejected} cannot satisfy Num2Bits(8)"
            );
        }
    }

    #[test]
    fn jurisdiction_is_bounded_by_the_circuit_num2bits_16_constraint() {
        assert_eq!(Jurisdiction::BITS, 16);
        assert_eq!(Jurisdiction::MAX, 65_535);
        assert_eq!(Jurisdiction::from_u64(0).unwrap().get(), 0);
        assert_eq!(Jurisdiction::from_u64(65_535).unwrap().get(), 65_535);
        for rejected in [65_536, 1_000_000, u64::MAX] {
            assert_eq!(
                Jurisdiction::from_u64(rejected),
                Err(ProtocolError::CredentialCodeOutOfRange {
                    code: CredentialCodeField::Jurisdiction,
                    bits: 16
                }),
                "jurisdiction {rejected} cannot satisfy Num2Bits(16)"
            );
        }
    }

    #[test]
    fn credential_nonce_keeps_the_full_64_bit_range() {
        // Unlike the policy codes, the nonce really is Num2Bits(64).
        assert_eq!(CredentialNonce::new(u64::MAX).get(), u64::MAX);
    }

    #[test]
    fn credential_expiry_covers_the_phase0g_trade_expiry() {
        let credential = CredentialExpiry::new(CREDENTIAL_EXPIRY);
        assert!(credential.covers_trade_expiry(UnixSeconds::new(TRADE_EXPIRY)));
        assert!(credential.covers_trade_expiry(UnixSeconds::new(CREDENTIAL_EXPIRY)));
        assert!(!credential.covers_trade_expiry(UnixSeconds::new(CREDENTIAL_EXPIRY + 1)));
    }
}
