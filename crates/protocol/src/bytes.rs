//! Fixed-size byte representations used by the protocol.
//!
//! These types enforce protocol lengths and keep text encodings out of
//! commitment inputs. They do not parse Orchard points or addresses; callers
//! must obtain the bytes from the canonical Orchard APIs.

use crate::error::{ProtocolError, Result};

/// Length of a canonical `orchard::note::AssetBase` encoding.
pub const ASSET_BASE_LEN: usize = 32;

/// Length of a canonical raw Orchard payment address.
pub const ORCHARD_RECEIVER_LEN: usize = 43;

/// Length of the diversifier prefix of a raw Orchard payment address.
pub const ORCHARD_DIVERSIFIER_LEN: usize = 11;

/// Length of the diversified transmission key suffix of a raw Orchard payment
/// address.
pub const ORCHARD_TRANSMISSION_KEY_LEN: usize = 32;

/// Canonical 32-byte representation of an Orchard `AssetBase`.
///
/// This is the compressed Pallas point produced by `pallas::Point::to_bytes()`,
/// not a ZIP-227 asset id. Construction enforces the byte length; it does not
/// decode the point.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct AssetBaseBytes([u8; ASSET_BASE_LEN]);

impl AssetBaseBytes {
    /// Wraps an exactly 32-byte canonical encoding.
    #[must_use]
    pub const fn new(bytes: [u8; ASSET_BASE_LEN]) -> Self {
        Self(bytes)
    }

    /// Wraps a slice that must be exactly 32 bytes long.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::InvalidAssetBase`] for any other length.
    pub fn from_slice(bytes: &[u8]) -> Result<Self> {
        let fixed: [u8; ASSET_BASE_LEN] = bytes
            .try_into()
            .map_err(|_| ProtocolError::InvalidAssetBase { got: bytes.len() })?;
        Ok(Self(fixed))
    }

    /// Parses a 64-character lowercase-or-uppercase hexadecimal encoding.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::InvalidAssetBase`] if the text is not exactly
    /// 32 bytes of hexadecimal.
    pub fn from_hex(text: &str) -> Result<Self> {
        let bytes = decode_hex(text).ok_or(ProtocolError::InvalidAssetBase {
            got: text.len() / 2,
        })?;
        Self::from_slice(&bytes)
    }

    /// Returns the canonical bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; ASSET_BASE_LEN] {
        &self.0
    }

    /// Returns the lowercase hexadecimal encoding.
    #[must_use]
    pub fn to_hex(&self) -> String {
        encode_hex(&self.0)
    }
}

/// The canonical 43-byte raw Orchard payment address produced by
/// `orchard::Address::to_raw_address_bytes()`.
///
/// The layout is an 11-byte diversifier followed by a 32-byte diversified
/// transmission key. The protocol primitive is these raw bytes; the
/// human-readable unified-address text encoding is never the protocol value.
/// Construction enforces the byte length; it does not parse the address.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct OrchardReceiverBytes([u8; ORCHARD_RECEIVER_LEN]);

impl OrchardReceiverBytes {
    /// Wraps an exactly 43-byte canonical raw address.
    #[must_use]
    pub const fn new(bytes: [u8; ORCHARD_RECEIVER_LEN]) -> Self {
        Self(bytes)
    }

    /// Wraps a slice that must be exactly 43 bytes long.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::InvalidReceiver`] for any other length.
    pub fn from_slice(bytes: &[u8]) -> Result<Self> {
        let fixed: [u8; ORCHARD_RECEIVER_LEN] = bytes
            .try_into()
            .map_err(|_| ProtocolError::InvalidReceiver { got: bytes.len() })?;
        Ok(Self(fixed))
    }

    /// Parses an 86-character hexadecimal encoding.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::InvalidReceiver`] if the text is not exactly 43
    /// bytes of hexadecimal.
    pub fn from_hex(text: &str) -> Result<Self> {
        let bytes = decode_hex(text).ok_or(ProtocolError::InvalidReceiver {
            got: text.len() / 2,
        })?;
        Self::from_slice(&bytes)
    }

    /// Returns the canonical raw bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; ORCHARD_RECEIVER_LEN] {
        &self.0
    }

    /// Returns the 11-byte diversifier prefix.
    #[must_use]
    pub fn diversifier(&self) -> [u8; ORCHARD_DIVERSIFIER_LEN] {
        let mut out = [0u8; ORCHARD_DIVERSIFIER_LEN];
        out.copy_from_slice(&self.0[..ORCHARD_DIVERSIFIER_LEN]);
        out
    }

    /// Returns the 32-byte diversified transmission key suffix.
    #[must_use]
    pub fn transmission_key(&self) -> [u8; ORCHARD_TRANSMISSION_KEY_LEN] {
        let mut out = [0u8; ORCHARD_TRANSMISSION_KEY_LEN];
        out.copy_from_slice(&self.0[ORCHARD_DIVERSIFIER_LEN..]);
        out
    }

    /// Returns the lowercase hexadecimal encoding.
    #[must_use]
    pub fn to_hex(&self) -> String {
        encode_hex(&self.0)
    }
}

fn decode_hex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    for pair in bytes.chunks_exact(2) {
        let hi = nibble(pair[0])?;
        let lo = nibble(pair[1])?;
        out.push((hi << 4) | lo);
    }
    Some(out)
}

fn nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn encode_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = Vec::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[usize::from(byte >> 4)]);
        out.push(DIGITS[usize::from(byte & 0x0f)]);
    }
    String::from_utf8(out).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0F `assetIdentity.offeredAssetBase`.
    const OFFERED_ASSET_BASE: &str =
        "4889ad11564115f3655f7e434bffb23074d42aafd58cfecae32a5b5eafaf5301";
    /// Phase 0G `receiver.institutionABytes`.
    const INSTITUTION_A: &str =
        "781671f8a41294c866d8161f3bf5f84a8fd2c328f91a2d085a66036acd59439731c36c4f1b99b4d64be233";

    #[test]
    fn asset_base_hex_round_trips() {
        let asset = AssetBaseBytes::from_hex(OFFERED_ASSET_BASE).unwrap();
        assert_eq!(asset.to_hex(), OFFERED_ASSET_BASE);
        assert_eq!(AssetBaseBytes::from_slice(asset.as_bytes()).unwrap(), asset);
    }

    #[test]
    fn asset_base_rejects_malformed_length_and_characters() {
        assert_eq!(
            AssetBaseBytes::from_slice(&[0u8; 31]),
            Err(ProtocolError::InvalidAssetBase { got: 31 })
        );
        assert_eq!(
            AssetBaseBytes::from_slice(&[0u8; 33]),
            Err(ProtocolError::InvalidAssetBase { got: 33 })
        );
        assert!(AssetBaseBytes::from_hex(&OFFERED_ASSET_BASE[..62]).is_err());
        assert!(AssetBaseBytes::from_hex(&format!("zz{}", &OFFERED_ASSET_BASE[2..])).is_err());
    }

    #[test]
    fn receiver_hex_round_trips_and_splits() {
        let receiver = OrchardReceiverBytes::from_hex(INSTITUTION_A).unwrap();
        assert_eq!(receiver.to_hex(), INSTITUTION_A);
        assert_eq!(receiver.diversifier().len(), ORCHARD_DIVERSIFIER_LEN);
        assert_eq!(
            receiver.transmission_key().len(),
            ORCHARD_TRANSMISSION_KEY_LEN
        );
        let mut rebuilt = Vec::new();
        rebuilt.extend_from_slice(&receiver.diversifier());
        rebuilt.extend_from_slice(&receiver.transmission_key());
        assert_eq!(rebuilt, receiver.as_bytes().to_vec());
    }

    #[test]
    fn receiver_rejects_malformed_length() {
        for length in [0usize, 42, 44] {
            assert_eq!(
                OrchardReceiverBytes::from_slice(&vec![0u8; length]),
                Err(ProtocolError::InvalidReceiver { got: length })
            );
        }
    }
}
