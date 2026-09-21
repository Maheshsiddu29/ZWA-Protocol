//! Frozen `AssetBase` limb encoding used by `TradeCommitmentV1`.
//!
//! A canonical 32-byte `orchard::note::AssetBase` becomes two unsigned 128-bit
//! limbs:
//!
//! ```text
//! lo128 = little-endian bytes[0..16]
//! hi128 = little-endian bytes[16..32]
//! ```
//!
//! The circuits range-constrain both limbs to 128 bits and hash them in the
//! order `(hi, lo)`. Re-encoding concatenates `LE128(lo) || LE128(hi)`, so the
//! transformation is lossless. The bytes are never reinterpreted as a point,
//! scalar, or ZIP-227 asset id.

use zwa_protocol::bytes::ASSET_BASE_LEN;
use zwa_protocol::AssetBaseBytes;

/// Half of a canonical `AssetBase` encoding, in bytes.
const LIMB_BYTES: usize = ASSET_BASE_LEN / 2;

/// The two unsigned 128-bit limbs of a canonical `AssetBase`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct AssetBaseLimbs {
    /// Little-endian interpretation of `bytes[16..32]`.
    pub hi: u128,
    /// Little-endian interpretation of `bytes[0..16]`.
    pub lo: u128,
}

/// Splits a canonical `AssetBase` into its two 128-bit circuit limbs.
///
/// Byte order and limb names differ from the later hash-input order: this
/// function returns `(hi, lo)` fields after reading the low half first.
#[must_use]
pub fn encode_asset_base(asset: &AssetBaseBytes) -> AssetBaseLimbs {
    let bytes = asset.as_bytes();
    AssetBaseLimbs {
        hi: little_endian_u128(&bytes[LIMB_BYTES..]),
        lo: little_endian_u128(&bytes[..LIMB_BYTES]),
    }
}

/// Reassembles the exact canonical `AssetBase` from its two limbs.
///
/// Every `u128` fits in 16 little-endian bytes, so this is total: an
/// `AssetBaseLimbs` value can only describe a well-formed 32-byte encoding.
#[must_use]
pub fn decode_asset_base(limbs: &AssetBaseLimbs) -> AssetBaseBytes {
    let mut bytes = [0u8; ASSET_BASE_LEN];
    bytes[..LIMB_BYTES].copy_from_slice(&limbs.lo.to_le_bytes());
    bytes[LIMB_BYTES..].copy_from_slice(&limbs.hi.to_le_bytes());
    AssetBaseBytes::new(bytes)
}

/// Interprets up to 16 bytes as an unsigned little-endian 128-bit integer.
pub(crate) fn little_endian_u128(bytes: &[u8]) -> u128 {
    let mut padded = [0u8; 16];
    let width = bytes.len().min(16);
    padded[..width].copy_from_slice(&bytes[..width]);
    u128::from_le_bytes(padded)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0F `assetIdentity.offeredAssetBase` and its recorded limbs.
    const OFFERED_ASSET_BASE: &str =
        "4889ad11564115f3655f7e434bffb23074d42aafd58cfecae32a5b5eafaf5301";
    const OFFERED_HI: u128 = 1_763_751_950_854_191_318_876_944_356_107_539_572;
    const OFFERED_LO: u128 = 64_732_350_615_944_551_779_799_158_410_112_239_944;

    /// Phase 0F `assetIdentity.requestedAssetBase` and its recorded limbs.
    const REQUESTED_ASSET_BASE: &str =
        "a7ac13ded8b51e7a59c400097b70fe6d5d855b30ad19b1897de1fd74721a9339";
    const REQUESTED_HI: u128 = 76_529_799_808_812_230_206_338_417_411_766_322_525;
    const REQUESTED_LO: u128 = 146_206_976_320_349_566_928_545_363_030_336_318_631;

    #[test]
    fn phase0f_asset_base_vectors_produce_the_recorded_limbs() {
        let offered = AssetBaseBytes::from_hex(OFFERED_ASSET_BASE).unwrap();
        assert_eq!(
            encode_asset_base(&offered),
            AssetBaseLimbs {
                hi: OFFERED_HI,
                lo: OFFERED_LO
            }
        );
        let requested = AssetBaseBytes::from_hex(REQUESTED_ASSET_BASE).unwrap();
        assert_eq!(
            encode_asset_base(&requested),
            AssetBaseLimbs {
                hi: REQUESTED_HI,
                lo: REQUESTED_LO
            }
        );
    }

    #[test]
    fn asset_base_round_trip_preserves_exact_bytes() {
        for hex in [OFFERED_ASSET_BASE, REQUESTED_ASSET_BASE] {
            let asset = AssetBaseBytes::from_hex(hex).unwrap();
            assert_eq!(decode_asset_base(&encode_asset_base(&asset)), asset);
            assert_eq!(
                decode_asset_base(&encode_asset_base(&asset)).to_hex(),
                hex.to_owned()
            );
        }
    }

    #[test]
    fn boundary_limb_values_round_trip() {
        for limbs in [
            AssetBaseLimbs { hi: 0, lo: 0 },
            AssetBaseLimbs {
                hi: 0,
                lo: u128::MAX,
            },
            AssetBaseLimbs {
                hi: u128::MAX,
                lo: 0,
            },
            AssetBaseLimbs {
                hi: u128::MAX,
                lo: u128::MAX,
            },
            AssetBaseLimbs { hi: 1, lo: 1 },
        ] {
            assert_eq!(encode_asset_base(&decode_asset_base(&limbs)), limbs);
        }
        // The lo limb owns bytes[0..16] and the hi limb owns bytes[16..32].
        let lo_only = decode_asset_base(&AssetBaseLimbs {
            hi: 0,
            lo: u128::MAX,
        });
        assert_eq!(
            lo_only.to_hex(),
            "ffffffffffffffffffffffffffffffff00000000000000000000000000000000"
        );
        let hi_only = decode_asset_base(&AssetBaseLimbs {
            hi: u128::MAX,
            lo: 0,
        });
        assert_eq!(
            hi_only.to_hex(),
            "00000000000000000000000000000000ffffffffffffffffffffffffffffffff"
        );
    }

    #[test]
    fn malformed_lengths_are_rejected_before_encoding() {
        assert!(AssetBaseBytes::from_slice(&[0u8; 31]).is_err());
        assert!(AssetBaseBytes::from_slice(&[0u8; 33]).is_err());
        assert!(AssetBaseBytes::from_hex("4889ad").is_err());
    }
}
