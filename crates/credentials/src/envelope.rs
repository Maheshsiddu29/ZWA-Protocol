//! Canonical issuer and credential root envelopes.
//!
//! Each envelope is the application-layer signed-root object the matcher will
//! later authenticate. The unsigned payload has a deterministic byte encoding
//! that is the data-to-be-signed. Signature material is an opaque, validated
//! byte container: no signature algorithm is selected in Phase 1.
//!
//! Passing [`validate_structure_at`](IssuerRootEnvelope::validate_structure_at)
//! produces [`StructurallyValid`]. It does not produce cryptographic
//! authentication.

use zwa_protocol::error::Result;
use zwa_protocol::{
    ActiveCredentialRoot, AuthorizedIssuanceRoot, OpaqueSignature, RootMetadata, RootVersion,
    StructurallyValid, UnixSeconds,
};

use crate::ids::{AuthorityKeyId, IssuerKeyId};

/// Domain tag written at the front of every canonical root payload.
const PAYLOAD_MAGIC: &[u8; 8] = b"ZWA1ROOT";

/// Kind tag for an issuer authorized-issuance root payload.
const KIND_ISSUER: u8 = 1;

/// Kind tag for a credential-authority active-root payload.
const KIND_CREDENTIAL: u8 = 2;

/// Unsigned issuer-root payload: the data that will later be signed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuerRootPayload {
    root: AuthorizedIssuanceRoot,
    issuer_id: IssuerKeyId,
    metadata: RootMetadata,
}

impl IssuerRootPayload {
    /// Builds a payload from already-validated components.
    ///
    /// # Errors
    ///
    /// Propagates version and window errors from [`RootMetadata::new`].
    pub fn new(
        root: AuthorizedIssuanceRoot,
        issuer_id: IssuerKeyId,
        version: RootVersion,
        valid_from: UnixSeconds,
        expires_at: UnixSeconds,
    ) -> Result<Self> {
        Ok(Self {
            root,
            issuer_id,
            metadata: RootMetadata::new(version, valid_from, expires_at)?,
        })
    }

    /// Authorized issuance Merkle root.
    #[must_use]
    pub const fn root(&self) -> AuthorizedIssuanceRoot {
        self.root
    }

    /// Issuer identifier.
    #[must_use]
    pub const fn issuer_id(&self) -> &IssuerKeyId {
        &self.issuer_id
    }

    /// Version and freshness metadata.
    #[must_use]
    pub const fn metadata(&self) -> RootMetadata {
        self.metadata
    }

    /// Deterministic serialization of the data-to-be-signed.
    ///
    /// Encoding, big-endian integers:
    /// `ZWA1ROOT || kind=1 || version || valid_from || expires_at || id_len || id || root`.
    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        encode_payload(
            KIND_ISSUER,
            self.metadata.version().get(),
            self.metadata.valid_from().get(),
            self.metadata.expires_at().get(),
            self.issuer_id.as_bytes(),
            self.root.to_be_bytes(),
        )
    }

    /// Non-cryptographic version, window, and identifier checks at `now`.
    ///
    /// # Errors
    ///
    /// Returns an unsupported-version, inverted-window, not-yet-valid, or
    /// expired error. Success is [`StructurallyValid`], not authentication.
    pub fn validate_structure_at(&self, now: UnixSeconds) -> Result<StructurallyValid<&Self>> {
        self.metadata.ensure_current_at(now)?;
        Ok(StructurallyValid::new(self))
    }
}

/// Issuer root envelope: unsigned payload plus opaque signature material.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuerRootEnvelope {
    payload: IssuerRootPayload,
    signature: OpaqueSignature,
}

impl IssuerRootEnvelope {
    /// Combines a payload with uninterpreted signature material.
    #[must_use]
    pub const fn new(payload: IssuerRootPayload, signature: OpaqueSignature) -> Self {
        Self { payload, signature }
    }

    /// Unsigned payload.
    #[must_use]
    pub const fn payload(&self) -> &IssuerRootPayload {
        &self.payload
    }

    /// Opaque signature bytes. Not verified here.
    #[must_use]
    pub const fn signature(&self) -> &OpaqueSignature {
        &self.signature
    }

    /// Canonical bytes of the unsigned payload.
    #[must_use]
    pub fn canonical_payload_bytes(&self) -> Vec<u8> {
        self.payload.canonical_bytes()
    }

    /// Structural and freshness validation. Does not authenticate the root.
    ///
    /// # Errors
    ///
    /// Propagates [`IssuerRootPayload::validate_structure_at`].
    pub fn validate_structure_at(&self, now: UnixSeconds) -> Result<StructurallyValid<&Self>> {
        self.payload.validate_structure_at(now)?;
        Ok(StructurallyValid::new(self))
    }
}

/// Unsigned credential-root payload: the data that will later be signed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CredentialRootPayload {
    root: ActiveCredentialRoot,
    authority_id: AuthorityKeyId,
    metadata: RootMetadata,
}

impl CredentialRootPayload {
    /// Builds a payload from already-validated components.
    ///
    /// # Errors
    ///
    /// Propagates version and window errors from [`RootMetadata::new`].
    pub fn new(
        root: ActiveCredentialRoot,
        authority_id: AuthorityKeyId,
        version: RootVersion,
        valid_from: UnixSeconds,
        expires_at: UnixSeconds,
    ) -> Result<Self> {
        Ok(Self {
            root,
            authority_id,
            metadata: RootMetadata::new(version, valid_from, expires_at)?,
        })
    }

    /// Active credential Merkle root.
    #[must_use]
    pub const fn root(&self) -> ActiveCredentialRoot {
        self.root
    }

    /// Credential-authority identifier.
    #[must_use]
    pub const fn authority_id(&self) -> &AuthorityKeyId {
        &self.authority_id
    }

    /// Version and freshness metadata.
    #[must_use]
    pub const fn metadata(&self) -> RootMetadata {
        self.metadata
    }

    /// Deterministic serialization of the data-to-be-signed.
    ///
    /// Encoding, big-endian integers:
    /// `ZWA1ROOT || kind=2 || version || valid_from || expires_at || id_len || id || root`.
    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        encode_payload(
            KIND_CREDENTIAL,
            self.metadata.version().get(),
            self.metadata.valid_from().get(),
            self.metadata.expires_at().get(),
            self.authority_id.as_bytes(),
            self.root.to_be_bytes(),
        )
    }

    /// Non-cryptographic version, window, and identifier checks at `now`.
    ///
    /// # Errors
    ///
    /// Returns an unsupported-version, inverted-window, not-yet-valid, or
    /// expired error. Success is [`StructurallyValid`], not authentication.
    pub fn validate_structure_at(&self, now: UnixSeconds) -> Result<StructurallyValid<&Self>> {
        self.metadata.ensure_current_at(now)?;
        Ok(StructurallyValid::new(self))
    }
}

/// Credential root envelope: unsigned payload plus opaque signature material.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CredentialRootEnvelope {
    payload: CredentialRootPayload,
    signature: OpaqueSignature,
}

impl CredentialRootEnvelope {
    /// Combines a payload with uninterpreted signature material.
    #[must_use]
    pub const fn new(payload: CredentialRootPayload, signature: OpaqueSignature) -> Self {
        Self { payload, signature }
    }

    /// Unsigned payload.
    #[must_use]
    pub const fn payload(&self) -> &CredentialRootPayload {
        &self.payload
    }

    /// Opaque signature bytes. Not verified here.
    #[must_use]
    pub const fn signature(&self) -> &OpaqueSignature {
        &self.signature
    }

    /// Canonical bytes of the unsigned payload.
    #[must_use]
    pub fn canonical_payload_bytes(&self) -> Vec<u8> {
        self.payload.canonical_bytes()
    }

    /// Structural and freshness validation. Does not authenticate the root.
    ///
    /// # Errors
    ///
    /// Propagates [`CredentialRootPayload::validate_structure_at`].
    pub fn validate_structure_at(&self, now: UnixSeconds) -> Result<StructurallyValid<&Self>> {
        self.payload.validate_structure_at(now)?;
        Ok(StructurallyValid::new(self))
    }
}

fn encode_payload(
    kind: u8,
    version: u64,
    valid_from: u64,
    expires_at: u64,
    identifier: &[u8],
    root: [u8; 32],
) -> Vec<u8> {
    // Identifier length is already bounded to 64 by `KeyIdentifier`.
    let id_len = identifier.len() as u8;
    let mut out = Vec::with_capacity(8 + 1 + 8 * 3 + 1 + identifier.len() + 32);
    out.extend_from_slice(PAYLOAD_MAGIC);
    out.push(kind);
    out.extend_from_slice(&version.to_be_bytes());
    out.extend_from_slice(&valid_from.to_be_bytes());
    out.extend_from_slice(&expires_at.to_be_bytes());
    out.push(id_len);
    out.extend_from_slice(identifier);
    out.extend_from_slice(&root);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use zwa_protocol::error::{ProtocolError, RootEnvelopeProblem, TimestampProblem};

    /// Phase 0G `issuance.authorizedIssuanceRoot`.
    const ISSUANCE_ROOT: &str =
        "19309979006225485291788213177219598381134511159668519266888323889569746782051";
    /// Phase 0G `credential.activeCredentialRoot`.
    const CREDENTIAL_ROOT: &str =
        "7239536478138432754387625126231950010993505962177483323536139232738771167323";

    fn dummy_signature() -> OpaqueSignature {
        // 80 uninterpreted bytes. The length is a container example, not a
        // signature-scheme identifier.
        OpaqueSignature::new(&[0x5a; 80]).unwrap()
    }

    fn issuer_payload() -> IssuerRootPayload {
        IssuerRootPayload::new(
            AuthorizedIssuanceRoot::from_decimal_str(ISSUANCE_ROOT).unwrap(),
            IssuerKeyId::new(b"issuer-atlas").unwrap(),
            RootVersion::new(1),
            UnixSeconds::new(1_900_000_000),
            UnixSeconds::new(2_100_000_000),
        )
        .unwrap()
    }

    fn credential_payload() -> CredentialRootPayload {
        CredentialRootPayload::new(
            ActiveCredentialRoot::from_decimal_str(CREDENTIAL_ROOT).unwrap(),
            AuthorityKeyId::new(b"cred-auth-1").unwrap(),
            RootVersion::new(1),
            UnixSeconds::new(1_900_000_000),
            UnixSeconds::new(2_100_000_000),
        )
        .unwrap()
    }

    #[test]
    fn canonical_payload_bytes_are_deterministic_and_kind_tagged() {
        let issuer = issuer_payload();
        let credential = credential_payload();
        assert_eq!(issuer.canonical_bytes(), issuer.canonical_bytes());
        assert_eq!(credential.canonical_bytes(), credential.canonical_bytes());
        assert_ne!(issuer.canonical_bytes(), credential.canonical_bytes());
        assert_eq!(&issuer.canonical_bytes()[..8], b"ZWA1ROOT");
        assert_eq!(issuer.canonical_bytes()[8], KIND_ISSUER);
        assert_eq!(credential.canonical_bytes()[8], KIND_CREDENTIAL);
        assert_eq!(
            &issuer.canonical_bytes()[issuer.canonical_bytes().len() - 32..],
            &issuer.root().to_be_bytes()
        );
    }

    #[test]
    fn mutating_any_signed_field_changes_the_canonical_bytes() {
        let base = issuer_payload().canonical_bytes();
        let different_version = IssuerRootPayload::new(
            issuer_payload().root(),
            IssuerKeyId::new(b"issuer-atlas").unwrap(),
            RootVersion::new(2),
            UnixSeconds::new(1_900_000_000),
            UnixSeconds::new(2_100_000_000),
        )
        .unwrap();
        let different_id = IssuerRootPayload::new(
            issuer_payload().root(),
            IssuerKeyId::new(b"issuer-other").unwrap(),
            RootVersion::new(1),
            UnixSeconds::new(1_900_000_000),
            UnixSeconds::new(2_100_000_000),
        )
        .unwrap();
        let different_window = IssuerRootPayload::new(
            issuer_payload().root(),
            IssuerKeyId::new(b"issuer-atlas").unwrap(),
            RootVersion::new(1),
            UnixSeconds::new(1_900_000_001),
            UnixSeconds::new(2_100_000_000),
        )
        .unwrap();
        assert_ne!(base, different_version.canonical_bytes());
        assert_ne!(base, different_id.canonical_bytes());
        assert_ne!(base, different_window.canonical_bytes());
    }

    #[test]
    fn structural_validation_accepts_the_inclusive_window() {
        let envelope = IssuerRootEnvelope::new(issuer_payload(), dummy_signature());
        assert!(envelope
            .validate_structure_at(UnixSeconds::new(1_900_000_000))
            .is_ok());
        assert!(envelope
            .validate_structure_at(UnixSeconds::new(2_000_000_000))
            .is_ok());
        assert!(envelope
            .validate_structure_at(UnixSeconds::new(2_100_000_000))
            .is_ok());
        let valid = envelope
            .validate_structure_at(UnixSeconds::new(2_000_000_000))
            .unwrap();
        assert_eq!(valid.get().payload().root().to_string(), ISSUANCE_ROOT);
    }

    #[test]
    fn structural_validation_rejects_stale_and_future_windows() {
        let envelope = CredentialRootEnvelope::new(credential_payload(), dummy_signature());
        assert_eq!(
            envelope.validate_structure_at(UnixSeconds::new(1_899_999_999)),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::NotYetValid {
                    valid_from: 1_900_000_000,
                    now: 1_899_999_999
                }
            })
        );
        assert_eq!(
            envelope.validate_structure_at(UnixSeconds::new(2_100_000_001)),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::Expired {
                    expires_at: 2_100_000_000,
                    now: 2_100_000_001
                }
            })
        );
    }

    #[test]
    fn construction_rejects_version_zero_and_inverted_windows() {
        assert_eq!(
            IssuerRootPayload::new(
                AuthorizedIssuanceRoot::from_decimal_str(ISSUANCE_ROOT).unwrap(),
                IssuerKeyId::new(b"issuer-atlas").unwrap(),
                RootVersion::new(0),
                UnixSeconds::new(1),
                UnixSeconds::new(2),
            ),
            Err(ProtocolError::UnsupportedVersion { got: 0 })
        );
        assert_eq!(
            CredentialRootPayload::new(
                ActiveCredentialRoot::from_decimal_str(CREDENTIAL_ROOT).unwrap(),
                AuthorityKeyId::new(b"cred-auth-1").unwrap(),
                RootVersion::new(1),
                UnixSeconds::new(5),
                UnixSeconds::new(4),
            ),
            Err(ProtocolError::InvalidTimestamp {
                reason: TimestampProblem::WindowInverted
            })
        );
    }

    #[test]
    fn structural_validity_is_not_cryptographic_authentication() {
        let envelope = IssuerRootEnvelope::new(issuer_payload(), dummy_signature());
        let _structurally_valid = envelope
            .validate_structure_at(UnixSeconds::new(2_000_000_000))
            .unwrap();
        // The dummy signature bytes were never interpreted. There is no
        // CryptographicallyAuthenticated type and no signature verification
        // function in this crate.
        assert_eq!(envelope.signature().as_bytes().len(), 80);
        assert_eq!(
            envelope.canonical_payload_bytes(),
            issuer_payload().canonical_bytes()
        );
    }
}
