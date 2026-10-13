//! Strictly canonical `F_{q_P}` encodings (contract §A1, §B6).
//!
//! Every public lineage value is a 32-byte little-endian encoding of a Pallas
//! base-field element. A value `>= q_P` is rejected, never reduced. The check
//! is `pallas::Base::from_repr`, the same one the frozen R2B-L1 instance
//! parser (`PublicInstance::parse`) and Orchard's `ExtractedNoteCommitment`
//! use, so byte equality of two canonical values is field equality.

use core::fmt;

use ff::PrimeField;
use pasta_curves::pallas;

/// Length of every canonical field encoding.
pub const FIELD_LEN: usize = 32;

/// Why a field encoding was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FieldError {
    /// The encoding was not exactly 32 bytes.
    Length {
        /// Supplied length.
        got: usize,
    },
    /// The 32 bytes encode an integer `>= q_P`.
    NonCanonical,
}

impl fmt::Display for FieldError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Length { got } => write!(f, "expected 32 bytes, got {got}"),
            Self::NonCanonical => f.write_str("not a canonical Pallas base-field encoding"),
        }
    }
}

/// A canonical little-endian Pallas base-field element.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct CanonicalFieldElement([u8; FIELD_LEN]);

impl CanonicalFieldElement {
    /// Parses exactly 32 canonical bytes.
    ///
    /// # Errors
    ///
    /// [`FieldError::Length`] for any other length, [`FieldError::NonCanonical`]
    /// for an integer `>= q_P`.
    pub fn from_slice(bytes: &[u8]) -> Result<Self, FieldError> {
        let array: [u8; FIELD_LEN] = bytes
            .try_into()
            .map_err(|_| FieldError::Length { got: bytes.len() })?;
        Self::from_array(array)
    }

    /// Parses a 32-byte array.
    ///
    /// # Errors
    ///
    /// [`FieldError::NonCanonical`] for an integer `>= q_P`.
    pub fn from_array(bytes: [u8; FIELD_LEN]) -> Result<Self, FieldError> {
        if bool::from(pallas::Base::from_repr(bytes).is_some()) {
            Ok(Self(bytes))
        } else {
            Err(FieldError::NonCanonical)
        }
    }

    /// Returns the canonical encoding.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; FIELD_LEN] {
        &self.0
    }
}

impl fmt::Debug for CanonicalFieldElement {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&hex::encode(self.0))
    }
}

macro_rules! field_newtype {
    ($(#[$doc:meta])* $name:ident) => {
        $(#[$doc])*
        #[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
        pub struct $name(CanonicalFieldElement);

        impl $name {
            /// Parses exactly 32 canonical bytes.
            ///
            /// # Errors
            ///
            /// See [`CanonicalFieldElement::from_slice`].
            pub fn from_slice(bytes: &[u8]) -> Result<Self, FieldError> {
                CanonicalFieldElement::from_slice(bytes).map(Self)
            }

            /// Parses a 32-byte array.
            ///
            /// # Errors
            ///
            /// See [`CanonicalFieldElement::from_array`].
            pub fn from_array(bytes: [u8; FIELD_LEN]) -> Result<Self, FieldError> {
                CanonicalFieldElement::from_array(bytes).map(Self)
            }

            /// Returns the canonical encoding.
            #[must_use]
            pub const fn as_bytes(&self) -> &[u8; FIELD_LEN] {
                self.0.as_bytes()
            }

            /// Returns the underlying field element.
            #[must_use]
            pub const fn element(&self) -> CanonicalFieldElement {
                self.0
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}({:?})", stringify!($name), self.0)
            }
        }
    };
}

field_newtype!(
    /// An extracted Orchard note commitment `cmx` (public).
    CanonicalCmx
);
field_newtype!(
    /// An Orchard nullifier `nf` (public).
    CanonicalNullifier
);
field_newtype!(
    /// An Orchard note-commitment tree root (canonical LE, as in `chain_ref`).
    CanonicalOrchardRoot
);

#[cfg(test)]
mod tests {
    use super::*;

    /// `q_P` (the Pallas base-field modulus), little-endian.
    const Q_P_LE: [u8; 32] = {
        let mut b = [0u8; 32];
        // 0x40000000000000000000000000000000224698fc094cf91b992d30ed00000001
        let be: [u8; 32] = [
            0x40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x22, 0x46, 0x98, 0xfc, 0x09, 0x4c,
            0xf9, 0x1b, 0x99, 0x2d, 0x30, 0xed, 0x00, 0x00, 0x00, 0x01,
        ];
        let mut i = 0;
        while i < 32 {
            b[i] = be[31 - i];
            i += 1;
        }
        b
    };

    #[test]
    fn rejects_modulus_and_accepts_modulus_minus_one() {
        assert_eq!(
            CanonicalFieldElement::from_array(Q_P_LE),
            Err(FieldError::NonCanonical)
        );
        let mut below = Q_P_LE;
        below[0] = 0x00;
        assert!(CanonicalFieldElement::from_array(below).is_ok());
        assert_eq!(
            CanonicalFieldElement::from_array([0xff; 32]),
            Err(FieldError::NonCanonical)
        );
    }

    #[test]
    fn rejects_wrong_lengths() {
        assert_eq!(
            CanonicalFieldElement::from_slice(&[0u8; 31]),
            Err(FieldError::Length { got: 31 })
        );
        assert_eq!(
            CanonicalFieldElement::from_slice(&[0u8; 33]),
            Err(FieldError::Length { got: 33 })
        );
        assert_eq!(
            CanonicalFieldElement::from_slice(&[]),
            Err(FieldError::Length { got: 0 })
        );
    }
}
