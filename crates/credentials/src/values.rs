//! Canonical credential and eligibility-policy value types.
//!
//! These are the private witness values the eligibility circuit range-constrains
//! with `Num2Bits(64)`. The protocol does not freeze a policy language: any
//! unsigned 64-bit class or jurisdiction code is representable. Which tuples
//! are permitted is determined by the asset's policy tree, not by these types.

use core::fmt;

use zwa_protocol::error::Result;
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
    /// Private investor-class code committed inside a credential leaf.
    InvestorClass
}

u64_newtype! {
    /// Private jurisdiction code committed inside a credential leaf.
    Jurisdiction
}

u64_newtype! {
    /// Application nonce distinguishing otherwise identical credentials.
    CredentialNonce
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
        assert_eq!(InvestorClass::new(QUALIFIED_INSTITUTION).get(), 3);
        assert_eq!(Jurisdiction::new(US).get(), 840);
        assert_eq!(CredentialNonce::new(41).get(), 41);
        assert_eq!(
            CredentialAuthorityCommitment::from_decimal_str("70707070707070")
                .unwrap()
                .to_string(),
            "70707070707070"
        );
    }

    #[test]
    fn credential_expiry_covers_the_phase0g_trade_expiry() {
        let credential = CredentialExpiry::new(CREDENTIAL_EXPIRY);
        assert!(credential.covers_trade_expiry(UnixSeconds::new(TRADE_EXPIRY)));
        assert!(credential.covers_trade_expiry(UnixSeconds::new(CREDENTIAL_EXPIRY)));
        assert!(!credential.covers_trade_expiry(UnixSeconds::new(CREDENTIAL_EXPIRY + 1)));
    }
}
