//! Distinct issuer and credential-authority identifiers.
//!
//! Both wrap [`KeyIdentifier`]. Keeping them as separate types prevents an
//! issuer identifier from being supplied where a credential-authority
//! identifier is required.

use zwa_protocol::error::Result;
use zwa_protocol::KeyIdentifier;

/// Identifier of an approved issuer, not a parsed public key.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct IssuerKeyId(KeyIdentifier);

impl IssuerKeyId {
    /// Wraps a non-empty identifier within the protocol container bound.
    ///
    /// # Errors
    ///
    /// Propagates [`zwa_protocol::ProtocolError::InvalidRootEnvelope`] when the
    /// identifier is empty or too long.
    pub fn new(bytes: &[u8]) -> Result<Self> {
        KeyIdentifier::new(bytes).map(Self)
    }

    /// Returns the identifier bytes.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.0.as_bytes()
    }

    /// Returns the inner protocol identifier.
    #[must_use]
    pub fn inner(&self) -> &KeyIdentifier {
        &self.0
    }
}

/// Identifier of an approved credential authority, not a parsed public key.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct AuthorityKeyId(KeyIdentifier);

impl AuthorityKeyId {
    /// Wraps a non-empty identifier within the protocol container bound.
    ///
    /// # Errors
    ///
    /// Propagates [`zwa_protocol::ProtocolError::InvalidRootEnvelope`] when the
    /// identifier is empty or too long.
    pub fn new(bytes: &[u8]) -> Result<Self> {
        KeyIdentifier::new(bytes).map(Self)
    }

    /// Returns the identifier bytes.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.0.as_bytes()
    }

    /// Returns the inner protocol identifier.
    #[must_use]
    pub fn inner(&self) -> &KeyIdentifier {
        &self.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_identifiers_are_rejected() {
        assert!(IssuerKeyId::new(b"").is_err());
        assert!(AuthorityKeyId::new(b"").is_err());
        assert_eq!(
            IssuerKeyId::new(b"issuer-a").unwrap().as_bytes(),
            b"issuer-a"
        );
        assert_eq!(
            AuthorityKeyId::new(b"authority-a").unwrap().as_bytes(),
            b"authority-a"
        );
    }
}
