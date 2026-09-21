//! Canonical BN254 scalar field elements.
//!
//! Every field-element-like protocol value (`TradeCommitmentV1`, recipient
//! commitment, policy root, Merkle root, subject secret) is a canonical,
//! validated [`FieldElement`]. The canonical wire form is 32 big-endian bytes;
//! the canonical text form is an unpadded decimal string, which is what the
//! frozen Phase 0 fixtures and `snarkjs` public-signal files use.

use core::fmt;

use crate::error::{FieldEncodingProblem, ProtocolError, Result};

/// Number of bytes in the canonical big-endian encoding of a field element.
pub const FIELD_BYTES: usize = 32;

/// The BN254 scalar field modulus, big-endian.
///
/// `21888242871839275222246405745257275088548364400416034343698204186575808495617`
pub const MODULUS_BE: [u8; FIELD_BYTES] = [
    0x30, 0x64, 0x4e, 0x72, 0xe1, 0x31, 0xa0, 0x29, 0xb8, 0x50, 0x45, 0xb6, 0x81, 0x81, 0x58, 0x5d,
    0x28, 0x33, 0xe8, 0x48, 0x79, 0xb9, 0x70, 0x91, 0x43, 0xe1, 0xf5, 0x93, 0xf0, 0x00, 0x00, 0x01,
];

/// A canonical element of the BN254 scalar field.
///
/// Construction always validates that the value is reduced, so a
/// `FieldElement` can never hold a non-canonical representative.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct FieldElement([u8; FIELD_BYTES]);

impl FieldElement {
    /// The additive identity.
    pub const ZERO: Self = Self([0u8; FIELD_BYTES]);

    /// Builds a field element from its canonical 32-byte big-endian encoding.
    ///
    /// # Errors
    ///
    /// Returns [`FieldEncodingProblem::NotReduced`] if the value is greater
    /// than or equal to the BN254 scalar modulus.
    pub fn from_be_bytes(bytes: [u8; FIELD_BYTES]) -> Result<Self> {
        if bytes >= MODULUS_BE {
            return Err(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::NotReduced,
            });
        }
        Ok(Self(bytes))
    }

    /// Builds a field element from a big-endian slice of exactly 32 bytes.
    ///
    /// # Errors
    ///
    /// Returns [`FieldEncodingProblem::WrongLength`] for any other length, or
    /// [`FieldEncodingProblem::NotReduced`] for an unreduced value.
    pub fn from_be_slice(bytes: &[u8]) -> Result<Self> {
        let fixed: [u8; FIELD_BYTES] =
            bytes
                .try_into()
                .map_err(|_| ProtocolError::InvalidFieldEncoding {
                    reason: FieldEncodingProblem::WrongLength,
                })?;
        Self::from_be_bytes(fixed)
    }

    /// Builds a field element from an unsigned 64-bit integer.
    #[must_use]
    pub fn from_u64(value: u64) -> Self {
        let mut bytes = [0u8; FIELD_BYTES];
        bytes[FIELD_BYTES - 8..].copy_from_slice(&value.to_be_bytes());
        Self(bytes)
    }

    /// Builds a field element from an unsigned 128-bit integer.
    ///
    /// Every `u128` is smaller than the modulus, so this cannot fail. This is
    /// the constructor used for the frozen 128-bit `AssetBase` and Orchard
    /// receiver limbs.
    #[must_use]
    pub fn from_u128(value: u128) -> Self {
        let mut bytes = [0u8; FIELD_BYTES];
        bytes[FIELD_BYTES - 16..].copy_from_slice(&value.to_be_bytes());
        Self(bytes)
    }

    /// Parses the canonical unpadded decimal representation.
    ///
    /// # Errors
    ///
    /// Returns [`FieldEncodingProblem::NotDecimal`] for an empty string or a
    /// non-digit character, [`FieldEncodingProblem::DecimalOverflow`] if the
    /// value exceeds 256 bits, and [`FieldEncodingProblem::NotReduced`] if it
    /// is at least the modulus.
    pub fn from_decimal_str(text: &str) -> Result<Self> {
        if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
            return Err(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::NotDecimal,
            });
        }
        let mut limbs = [0u64; 4];
        for digit in text.bytes().map(|b| u64::from(b - b'0')) {
            mul_add_small(&mut limbs, 10, digit).ok_or(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::DecimalOverflow,
            })?;
        }
        Self::from_be_bytes(limbs_to_be_bytes(&limbs))
    }

    /// Returns the canonical 32-byte big-endian encoding.
    #[must_use]
    pub const fn to_be_bytes(self) -> [u8; FIELD_BYTES] {
        self.0
    }

    /// Returns the canonical unpadded decimal representation.
    #[must_use]
    pub fn to_decimal_string(self) -> String {
        let mut limbs = be_bytes_to_limbs(&self.0);
        if limbs == [0u64; 4] {
            return "0".to_owned();
        }
        let mut digits = Vec::with_capacity(78);
        while limbs != [0u64; 4] {
            let remainder = div_rem_small(&mut limbs, 10);
            digits.push(b'0' + u8::try_from(remainder).unwrap_or(0));
        }
        digits.reverse();
        String::from_utf8(digits).unwrap_or_default()
    }
}

impl fmt::Display for FieldElement {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_decimal_string())
    }
}

impl fmt::Debug for FieldElement {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "FieldElement({})", self.to_decimal_string())
    }
}

/// Multiplies a little-endian 256-bit value by `multiplier` and adds `addend`.
///
/// Returns `None` on 256-bit overflow.
fn mul_add_small(limbs: &mut [u64; 4], multiplier: u64, addend: u64) -> Option<()> {
    let mut carry = u128::from(addend);
    for limb in limbs.iter_mut() {
        let product = u128::from(*limb) * u128::from(multiplier) + carry;
        *limb = product as u64;
        carry = product >> 64;
    }
    if carry == 0 {
        Some(())
    } else {
        None
    }
}

/// Divides a little-endian 256-bit value by `divisor` in place, returning the
/// remainder.
fn div_rem_small(limbs: &mut [u64; 4], divisor: u64) -> u64 {
    let mut remainder = 0u128;
    for limb in limbs.iter_mut().rev() {
        let current = (remainder << 64) | u128::from(*limb);
        *limb = (current / u128::from(divisor)) as u64;
        remainder = current % u128::from(divisor);
    }
    remainder as u64
}

fn be_bytes_to_limbs(bytes: &[u8; FIELD_BYTES]) -> [u64; 4] {
    let mut limbs = [0u64; 4];
    for (index, limb) in limbs.iter_mut().enumerate() {
        let start = FIELD_BYTES - 8 * (index + 1);
        let mut chunk = [0u8; 8];
        chunk.copy_from_slice(&bytes[start..start + 8]);
        *limb = u64::from_be_bytes(chunk);
    }
    limbs
}

fn limbs_to_be_bytes(limbs: &[u64; 4]) -> [u8; FIELD_BYTES] {
    let mut bytes = [0u8; FIELD_BYTES];
    for (index, limb) in limbs.iter().enumerate() {
        let start = FIELD_BYTES - 8 * (index + 1);
        bytes[start..start + 8].copy_from_slice(&limb.to_be_bytes());
    }
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;

    const MODULUS_DECIMAL: &str =
        "21888242871839275222246405745257275088548364400416034343698204186575808495617";

    #[test]
    fn decimal_round_trip_covers_phase0_vectors() {
        // Phase 0F provenance and Phase 0G eligibility golden TradeCommitmentV1.
        for decimal in [
            "0",
            "1",
            "5915971",
            "7409670081847436957289371955571360481923983184454289247710022466448715682310",
            "10187400613857124614980227259922066295752635539032972479692659299555113110306",
        ] {
            let element = FieldElement::from_decimal_str(decimal).unwrap();
            assert_eq!(element.to_decimal_string(), decimal);
        }
    }

    #[test]
    fn byte_and_integer_constructors_agree_with_decimal() {
        assert_eq!(FieldElement::from_u64(0), FieldElement::ZERO);
        assert_eq!(
            FieldElement::from_u64(u64::MAX),
            FieldElement::from_decimal_str("18446744073709551615").unwrap()
        );
        assert_eq!(
            FieldElement::from_u128(u128::MAX),
            FieldElement::from_decimal_str("340282366920938463463374607431768211455").unwrap()
        );
        let element = FieldElement::from_u64(5_915_971);
        assert_eq!(
            FieldElement::from_be_slice(&element.to_be_bytes()).unwrap(),
            element
        );
    }

    #[test]
    fn modulus_and_above_are_rejected() {
        assert_eq!(
            FieldElement::from_decimal_str(MODULUS_DECIMAL),
            Err(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::NotReduced
            })
        );
        assert_eq!(
            FieldElement::from_be_bytes([0xff; FIELD_BYTES]),
            Err(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::NotReduced
            })
        );
        // The largest canonical element is modulus - 1.
        let mut largest = MODULUS_BE;
        largest[FIELD_BYTES - 1] -= 1;
        assert!(FieldElement::from_be_bytes(largest).is_ok());
    }

    #[test]
    fn malformed_encodings_are_rejected() {
        for bad in ["", "12a", " 12", "-1", "1_2"] {
            assert_eq!(
                FieldElement::from_decimal_str(bad),
                Err(ProtocolError::InvalidFieldEncoding {
                    reason: FieldEncodingProblem::NotDecimal
                })
            );
        }
        assert_eq!(
            FieldElement::from_decimal_str(
                "115792089237316195423570985008687907853269984665640564039457584007913129639936"
            ),
            Err(ProtocolError::InvalidFieldEncoding {
                reason: FieldEncodingProblem::DecimalOverflow
            })
        );
        for length in [0usize, 31, 33] {
            assert_eq!(
                FieldElement::from_be_slice(&vec![0u8; length]),
                Err(ProtocolError::InvalidFieldEncoding {
                    reason: FieldEncodingProblem::WrongLength
                })
            );
        }
    }
}
