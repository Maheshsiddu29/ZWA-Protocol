//! The frozen Phase 0G raw Orchard receiver limb encoding.
//!
//! The protocol primitive is the canonical 43-byte
//! `orchard::Address::to_raw_address_bytes()` output — an 11-byte diversifier
//! followed by a 32-byte diversified transmission key. The unified-address text
//! encoding is never used as the protocol value.
//!
//! The raw byte sequence is split without reinterpretation into unsigned
//! little-endian limbs of 128, 128, and 88 bits:
//!
//! ```text
//! limb0 = little-endian bytes[0..16]
//! limb1 = little-endian bytes[16..32]
//! limb2 = little-endian bytes[32..43]
//! ```
//!
//! The eligibility circuit range-constrains them with `Num2Bits(128)`,
//! `Num2Bits(128)`, and `Num2Bits(88)`.

use zwa_protocol::bytes::ORCHARD_RECEIVER_LEN;
use zwa_protocol::error::{LimbKind, ProtocolError, Result};
use zwa_protocol::OrchardReceiverBytes;

use crate::asset_base::little_endian_u128;

/// Bit width the eligibility circuit constrains the third receiver limb to.
pub const RECEIVER_LIMB2_BITS: u32 = 88;

/// Largest value the third receiver limb may hold.
pub const RECEIVER_LIMB2_MAX: u128 = (1u128 << RECEIVER_LIMB2_BITS) - 1;

/// Bytes covered by each of the first two receiver limbs.
const WIDE_LIMB_BYTES: usize = 16;

/// Bytes covered by the third receiver limb.
const NARROW_LIMB_BYTES: usize = ORCHARD_RECEIVER_LEN - 2 * WIDE_LIMB_BYTES;

/// The three range-constrained limbs of a canonical raw Orchard receiver.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ReceiverLimbs {
    limb0: u128,
    limb1: u128,
    limb2: u128,
}

impl ReceiverLimbs {
    /// Builds receiver limbs, enforcing the circuit range constraints.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::LimbOutOfRange`] if `limb2` does not fit in 88
    /// bits, because such a value could never satisfy the eligibility circuit
    /// and could not be re-encoded into 11 bytes.
    pub fn new(limb0: u128, limb1: u128, limb2: u128) -> Result<Self> {
        if limb2 > RECEIVER_LIMB2_MAX {
            return Err(ProtocolError::LimbOutOfRange {
                limb: LimbKind::ReceiverLimb2,
                bits: RECEIVER_LIMB2_BITS,
            });
        }
        Ok(Self {
            limb0,
            limb1,
            limb2,
        })
    }

    /// Little-endian interpretation of `bytes[0..16]`.
    #[must_use]
    pub const fn limb0(self) -> u128 {
        self.limb0
    }

    /// Little-endian interpretation of `bytes[16..32]`.
    #[must_use]
    pub const fn limb1(self) -> u128 {
        self.limb1
    }

    /// Little-endian interpretation of `bytes[32..43]`, at most 88 bits wide.
    #[must_use]
    pub const fn limb2(self) -> u128 {
        self.limb2
    }
}

/// Splits a canonical raw Orchard receiver into its three circuit limbs.
///
/// A 43-byte input always yields an in-range third limb, so this is total.
#[must_use]
pub fn encode_receiver(receiver: &OrchardReceiverBytes) -> ReceiverLimbs {
    let bytes = receiver.as_bytes();
    ReceiverLimbs {
        limb0: little_endian_u128(&bytes[..WIDE_LIMB_BYTES]),
        limb1: little_endian_u128(&bytes[WIDE_LIMB_BYTES..2 * WIDE_LIMB_BYTES]),
        limb2: little_endian_u128(&bytes[2 * WIDE_LIMB_BYTES..]),
    }
}

/// Reassembles the exact canonical 43-byte raw Orchard receiver.
///
/// [`ReceiverLimbs`] already enforces the 88-bit bound on the third limb, so
/// this is total.
#[must_use]
pub fn decode_receiver(limbs: &ReceiverLimbs) -> OrchardReceiverBytes {
    let mut bytes = [0u8; ORCHARD_RECEIVER_LEN];
    bytes[..WIDE_LIMB_BYTES].copy_from_slice(&limbs.limb0.to_le_bytes());
    bytes[WIDE_LIMB_BYTES..2 * WIDE_LIMB_BYTES].copy_from_slice(&limbs.limb1.to_le_bytes());
    let narrow = limbs.limb2.to_le_bytes();
    bytes[2 * WIDE_LIMB_BYTES..].copy_from_slice(&narrow[..NARROW_LIMB_BYTES]);
    OrchardReceiverBytes::new(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use zwa_protocol::bytes::ORCHARD_DIVERSIFIER_LEN;

    /// Phase 0G `receiver.institutionABytes` and `receiver.institutionALimbs`.
    const INSTITUTION_A: &str =
        "781671f8a41294c866d8161f3bf5f84a8fd2c328f91a2d085a66036acd59439731c36c4f1b99b4d64be233";
    const INSTITUTION_A_LIMB0: u128 = 99_655_535_183_436_548_447_281_682_583_993_259_640;
    const INSTITUTION_A_LIMB1: u128 = 201_063_132_662_081_691_843_142_144_631_974_908_559;
    const INSTITUTION_A_LIMB2: u128 = 62_723_870_602_439_118_444_872_497;

    /// Phase 0G `receiver.institutionBBytes`, the matcher fee receiver.
    const INSTITUTION_B: &str =
        "ba5a9b6828e14d720cc41e998917f5996635d1a7fa84448cb118f7b6f65068d380099e5cd54d98dd3917bb";
    const INSTITUTION_B_LIMB0: u128 = 204_644_473_482_500_709_856_588_745_508_162_067_130;
    const INSTITUTION_B_LIMB1: u128 = 281_008_748_123_426_134_975_637_680_637_263_033_702;
    const INSTITUTION_B_LIMB2: u128 = 226_178_810_129_051_916_573_739_392;

    #[test]
    fn phase0g_receiver_vectors_produce_the_recorded_limbs() {
        let receiver = OrchardReceiverBytes::from_hex(INSTITUTION_A).unwrap();
        let limbs = encode_receiver(&receiver);
        assert_eq!(limbs.limb0(), INSTITUTION_A_LIMB0);
        assert_eq!(limbs.limb1(), INSTITUTION_A_LIMB1);
        assert_eq!(limbs.limb2(), INSTITUTION_A_LIMB2);

        let fee_receiver = OrchardReceiverBytes::from_hex(INSTITUTION_B).unwrap();
        let fee_limbs = encode_receiver(&fee_receiver);
        assert_eq!(fee_limbs.limb0(), INSTITUTION_B_LIMB0);
        assert_eq!(fee_limbs.limb1(), INSTITUTION_B_LIMB1);
        assert_eq!(fee_limbs.limb2(), INSTITUTION_B_LIMB2);
    }

    #[test]
    fn limbs_reassemble_the_exact_43_original_bytes() {
        for hex in [INSTITUTION_A, INSTITUTION_B] {
            let receiver = OrchardReceiverBytes::from_hex(hex).unwrap();
            let round_tripped = decode_receiver(&encode_receiver(&receiver));
            assert_eq!(round_tripped, receiver);
            assert_eq!(round_tripped.to_hex(), hex.to_owned());
        }
    }

    #[test]
    fn the_diversifier_and_transmission_key_survive_the_round_trip() {
        let receiver = OrchardReceiverBytes::from_hex(INSTITUTION_A).unwrap();
        let round_tripped = decode_receiver(&encode_receiver(&receiver));
        assert_eq!(round_tripped.diversifier(), receiver.diversifier());
        assert_eq!(
            round_tripped.transmission_key(),
            receiver.transmission_key()
        );
        assert_eq!(receiver.diversifier().len(), ORCHARD_DIVERSIFIER_LEN);
    }

    #[test]
    fn boundary_limb_values_round_trip() {
        for limbs in [
            (0, 0, 0),
            (u128::MAX, u128::MAX, RECEIVER_LIMB2_MAX),
            (u128::MAX, 0, 0),
            (0, u128::MAX, 0),
            (0, 0, RECEIVER_LIMB2_MAX),
        ] {
            let limbs = ReceiverLimbs::new(limbs.0, limbs.1, limbs.2).unwrap();
            assert_eq!(encode_receiver(&decode_receiver(&limbs)), limbs);
        }
        let limb2_only = decode_receiver(&ReceiverLimbs::new(0, 0, RECEIVER_LIMB2_MAX).unwrap());
        assert_eq!(
            limb2_only.to_hex(),
            "00000000000000000000000000000000\
             00000000000000000000000000000000\
             ffffffffffffffffffffff"
        );
    }

    #[test]
    fn out_of_range_limb_reconstruction_is_rejected() {
        assert_eq!(
            ReceiverLimbs::new(0, 0, RECEIVER_LIMB2_MAX + 1),
            Err(ProtocolError::LimbOutOfRange {
                limb: LimbKind::ReceiverLimb2,
                bits: RECEIVER_LIMB2_BITS
            })
        );
        assert_eq!(
            ReceiverLimbs::new(0, 0, u128::MAX),
            Err(ProtocolError::LimbOutOfRange {
                limb: LimbKind::ReceiverLimb2,
                bits: RECEIVER_LIMB2_BITS
            })
        );
        assert!(ReceiverLimbs::new(u128::MAX, u128::MAX, RECEIVER_LIMB2_MAX).is_ok());
    }

    #[test]
    fn malformed_receiver_lengths_are_rejected_before_encoding() {
        for length in [0usize, 42, 44, 86] {
            assert!(OrchardReceiverBytes::from_slice(&vec![0u8; length]).is_err());
        }
        assert!(OrchardReceiverBytes::from_hex(&INSTITUTION_A[..84]).is_err());
    }
}
