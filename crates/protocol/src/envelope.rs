//! Shared primitives for signed-root envelopes.
//!
//! Concrete issuer and credential envelopes live in `zwa-credentials`. This
//! module supplies the pieces those envelopes share: a required authority
//! identifier, an overflow-safe validity window, opaque signature material, and
//! the [`StructurallyValid`] marker.
//!
//! None of this authenticates a root. A future matcher milestone will verify a
//! signature against configured approved keys; no signature algorithm is
//! selected here.

use crate::error::{ProtocolError, Result, RootEnvelopeProblem, TimestampProblem};
use crate::numbers::{RootVersion, UnixSeconds};

/// Maximum accepted length of an issuer or credential-authority identifier.
///
/// This is a container bound, not the size of any particular public key.
pub const KEY_IDENTIFIER_MAX_LEN: usize = 64;

/// Maximum accepted length of opaque signature material.
///
/// This is a container bound. It does not encode a signature scheme.
pub const OPAQUE_SIGNATURE_MAX_LEN: usize = 1024;

/// A required issuer or credential-authority identifier.
///
/// The bytes are an application key identifier, not a parsed public key of a
/// chosen signature algorithm.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct KeyIdentifier(Vec<u8>);

impl KeyIdentifier {
    /// Wraps a non-empty identifier of at most [`KEY_IDENTIFIER_MAX_LEN`] bytes.
    ///
    /// # Errors
    ///
    /// Returns [`RootEnvelopeProblem::MissingAuthorityIdentifier`] when `bytes`
    /// is empty, or [`RootEnvelopeProblem::IdentifierTooLong`] when it exceeds
    /// the container bound.
    pub fn new(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::MissingAuthorityIdentifier,
            });
        }
        if bytes.len() > KEY_IDENTIFIER_MAX_LEN {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::IdentifierTooLong {
                    got: bytes.len(),
                    max: KEY_IDENTIFIER_MAX_LEN,
                },
            });
        }
        Ok(Self(bytes.to_vec()))
    }

    /// Returns the identifier bytes.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.0
    }
}

/// Inclusive `[valid_from, expires_at]` window in unsigned Unix seconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ValidityWindow {
    valid_from: UnixSeconds,
    expires_at: UnixSeconds,
}

impl ValidityWindow {
    /// Builds a window with `valid_from <= expires_at`.
    ///
    /// # Errors
    ///
    /// Returns [`TimestampProblem::WindowInverted`] when `valid_from` is after
    /// `expires_at`.
    pub fn new(valid_from: UnixSeconds, expires_at: UnixSeconds) -> Result<Self> {
        if valid_from > expires_at {
            return Err(ProtocolError::InvalidTimestamp {
                reason: TimestampProblem::WindowInverted,
            });
        }
        Ok(Self {
            valid_from,
            expires_at,
        })
    }

    /// Start of the window, in Unix seconds.
    #[must_use]
    pub const fn valid_from(self) -> UnixSeconds {
        self.valid_from
    }

    /// End of the window, in Unix seconds.
    #[must_use]
    pub const fn expires_at(self) -> UnixSeconds {
        self.expires_at
    }

    /// Rejects `now` if it falls outside the inclusive window.
    ///
    /// # Errors
    ///
    /// Returns [`RootEnvelopeProblem::NotYetValid`] or
    /// [`RootEnvelopeProblem::Expired`].
    pub fn contains(self, now: UnixSeconds) -> Result<()> {
        if now < self.valid_from {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::NotYetValid {
                    valid_from: self.valid_from.get(),
                    now: now.get(),
                },
            });
        }
        if now > self.expires_at {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::Expired {
                    expires_at: self.expires_at.get(),
                    now: now.get(),
                },
            });
        }
        Ok(())
    }
}

/// Versioned freshness metadata carried by every signed-root envelope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RootMetadata {
    version: RootVersion,
    window: ValidityWindow,
}

impl RootMetadata {
    /// Builds metadata with a supported version and a consistent window.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::UnsupportedVersion`] when `version` is below
    /// [`RootVersion::MIN`], or a timestamp error when the window is inverted.
    pub fn new(
        version: RootVersion,
        valid_from: UnixSeconds,
        expires_at: UnixSeconds,
    ) -> Result<Self> {
        if !version.is_valid() {
            return Err(ProtocolError::UnsupportedVersion { got: version.get() });
        }
        Ok(Self {
            version,
            window: ValidityWindow::new(valid_from, expires_at)?,
        })
    }

    /// Envelope version.
    #[must_use]
    pub const fn version(self) -> RootVersion {
        self.version
    }

    /// Inclusive validity window.
    #[must_use]
    pub const fn window(self) -> ValidityWindow {
        self.window
    }

    /// Start of the window.
    #[must_use]
    pub const fn valid_from(self) -> UnixSeconds {
        self.window.valid_from()
    }

    /// End of the window.
    #[must_use]
    pub const fn expires_at(self) -> UnixSeconds {
        self.window.expires_at()
    }

    /// Rejects `now` if it falls outside the inclusive window.
    ///
    /// # Errors
    ///
    /// Propagates [`ValidityWindow::contains`].
    pub fn ensure_current_at(self, now: UnixSeconds) -> Result<()> {
        self.window.contains(now)
    }
}

/// Uninterpreted signature material.
///
/// The bytes are a container. They are not parsed as Ed25519, secp256k1, ECDSA,
/// Schnorr, or any other scheme.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct OpaqueSignature(Vec<u8>);

impl OpaqueSignature {
    /// Wraps non-empty signature material of at most
    /// [`OPAQUE_SIGNATURE_MAX_LEN`] bytes.
    ///
    /// # Errors
    ///
    /// Returns [`RootEnvelopeProblem::SignatureMissing`] when `bytes` is empty,
    /// or [`RootEnvelopeProblem::SignatureTooLong`] when it exceeds the
    /// container bound.
    pub fn new(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::SignatureMissing,
            });
        }
        if bytes.len() > OPAQUE_SIGNATURE_MAX_LEN {
            return Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::SignatureTooLong {
                    got: bytes.len(),
                    max: OPAQUE_SIGNATURE_MAX_LEN,
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

/// Marker that non-cryptographic structural and freshness checks passed.
///
/// This is not cryptographic authentication. A later matcher milestone will
/// verify the opaque signature against configured approved keys.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StructurallyValid<T> {
    inner: T,
}

impl<T> StructurallyValid<T> {
    /// Wraps a value that has already passed structural validation.
    ///
    /// Envelope types call this only after version, window, identifier, and
    /// container checks succeed. The constructor itself does not authenticate
    /// a signature.
    #[must_use]
    pub const fn new(inner: T) -> Self {
        Self { inner }
    }

    /// Returns the structurally validated value.
    #[must_use]
    pub const fn get(&self) -> &T {
        &self.inner
    }

    /// Unwraps the structurally validated value.
    #[must_use]
    pub fn into_inner(self) -> T {
        self.inner
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn key_identifier_rejects_empty_and_oversized() {
        assert_eq!(
            KeyIdentifier::new(b""),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::MissingAuthorityIdentifier
            })
        );
        assert_eq!(
            KeyIdentifier::new(&[0u8; KEY_IDENTIFIER_MAX_LEN + 1]),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::IdentifierTooLong {
                    got: KEY_IDENTIFIER_MAX_LEN + 1,
                    max: KEY_IDENTIFIER_MAX_LEN
                }
            })
        );
        assert_eq!(
            KeyIdentifier::new(b"issuer-1").unwrap().as_bytes(),
            b"issuer-1"
        );
    }

    #[test]
    fn validity_window_is_inclusive_and_rejects_inversion() {
        let from = UnixSeconds::new(10);
        let until = UnixSeconds::new(20);
        let window = ValidityWindow::new(from, until).unwrap();
        assert!(window.contains(from).is_ok());
        assert!(window.contains(until).is_ok());
        assert!(window.contains(UnixSeconds::new(15)).is_ok());
        assert_eq!(
            window.contains(UnixSeconds::new(9)),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::NotYetValid {
                    valid_from: 10,
                    now: 9
                }
            })
        );
        assert_eq!(
            window.contains(UnixSeconds::new(21)),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::Expired {
                    expires_at: 20,
                    now: 21
                }
            })
        );
        assert_eq!(
            ValidityWindow::new(until, from),
            Err(ProtocolError::InvalidTimestamp {
                reason: TimestampProblem::WindowInverted
            })
        );
        assert!(ValidityWindow::new(from, from).is_ok());
    }

    #[test]
    fn root_metadata_rejects_version_zero() {
        assert_eq!(
            RootMetadata::new(
                RootVersion::new(0),
                UnixSeconds::new(1),
                UnixSeconds::new(2)
            ),
            Err(ProtocolError::UnsupportedVersion { got: 0 })
        );
        assert!(RootMetadata::new(
            RootVersion::new(1),
            UnixSeconds::new(1),
            UnixSeconds::new(2)
        )
        .is_ok());
    }

    #[test]
    fn opaque_signature_is_an_uninterpreted_container() {
        assert_eq!(
            OpaqueSignature::new(b""),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::SignatureMissing
            })
        );
        assert_eq!(
            OpaqueSignature::new(&[7u8; OPAQUE_SIGNATURE_MAX_LEN + 1]),
            Err(ProtocolError::InvalidRootEnvelope {
                reason: RootEnvelopeProblem::SignatureTooLong {
                    got: OPAQUE_SIGNATURE_MAX_LEN + 1,
                    max: OPAQUE_SIGNATURE_MAX_LEN
                }
            })
        );
        // 80 uninterpreted bytes: a container bound, not a scheme identifier.
        let material = [0x5au8; 80];
        assert_eq!(
            OpaqueSignature::new(&material).unwrap().as_bytes(),
            &material
        );
    }

    #[test]
    fn structurally_valid_is_not_cryptographically_authenticated() {
        // The marker type exists so callers cannot treat a freshness check as
        // signature verification. There is no Authenticated wrapper in this
        // crate, and no function here verifies a signature.
        let window = ValidityWindow::new(UnixSeconds::new(1), UnixSeconds::new(3)).unwrap();
        let valid = StructurallyValid::new(window);
        assert_eq!(valid.get(), &window);
        assert_eq!(valid.into_inner(), window);
    }
}
