//! Frozen Poseidon domain separators.
//!
//! Each tag is its ASCII label read as a positive big-endian integer, exactly
//! as the Phase 0F/0G Circom circuits and `circuits/shared/*.js` define it.
//! `Domain` has no public constructor, so no component can invent a new domain
//! separator.

use zwa_protocol::FieldElement;

/// A frozen Poseidon domain separator.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Domain(u64);

impl Domain {
    /// `"ASSETV1"` — asset commitment over the two `AssetBase` limbs.
    pub const ASSET_V1: Self = Self(18_387_490_596_738_609);

    /// `"FEEV1"` — native ZEC matcher-fee commitment.
    pub const FEE_V1: Self = Self(301_809_882_673);

    /// `"TRDA_V1"` — `TradePartA`.
    pub const TRADE_A_V1: Self = Self(23_734_351_151_715_889);

    /// `"TRDB_V1"` — `TradePartB`.
    pub const TRADE_B_V1: Self = Self(23_734_351_168_493_105);

    /// `"TRDM_V1"` — `TradeMeta`.
    pub const TRADE_META_V1: Self = Self(23_734_351_353_042_481);

    /// `"TRADE_V1"` — `TradeCommitmentV1`.
    pub const TRADE_V1: Self = Self(6_075_990_608_753_677_873);

    /// `"ZEC"` — the native-ZEC fee asset tag hashed inside the fee commitment.
    ///
    /// This is a fee-asset tag, not an `AssetBase`.
    pub const ZEC_ASSET_TAG: Self = Self(5_915_971);

    /// `"SUBJECT1"` — credential subject commitment.
    pub const SUBJECT_V1: Self = Self(6_004_778_564_925_477_937);

    /// `"RECEIVR1"` — commitment over the three raw Orchard receiver limbs.
    pub const RECEIVER_V1: Self = Self(5_928_218_449_365_324_337);

    /// `"RCPBIND1"` — Phase 0G recipient binding of subject and receiver.
    pub const RECIPIENT_BINDING_V1: Self = Self(5_927_669_780_177_634_353);

    /// `"FRCPTV1"` — canonical-byte commitment to the matcher's fee receiver.
    pub const FEE_RECIPIENT_V1: Self = Self(19_793_697_433_736_753);

    /// `"RCPTV1"` — Phase 0F canonical-byte commitment to a trade recipient.
    ///
    /// Phase 0G superseded this with [`Domain::RECIPIENT_BINDING_V1`], which
    /// additionally binds the credential subject. It is retained because the
    /// frozen Phase 0F reference vector depends on it.
    pub const RECIPIENT_V1: Self = Self(90_449_063_990_833);

    /// `"CREDMETA"` — credential metadata inside an active credential leaf.
    pub const CREDENTIAL_META_V1: Self = Self(4_851_015_908_287_927_361);

    /// `"CRED_V2"` — Phase 1B active credential leaf.
    ///
    /// The Phase 0G leaf domain `"CRED_V1"` (18949280892933681) committed to no
    /// receiver, so any receiver satisfied any credential. The V2 leaf also
    /// commits to the authority-approved receiver commitment. A separate domain
    /// keeps the two leaf statements unambiguous.
    pub const CREDENTIAL_V2: Self = Self(18_949_280_892_933_682);

    /// Returns the domain separator as the integer the circuits hash.
    #[must_use]
    pub const fn tag(self) -> u64 {
        self.0
    }

    /// Returns the domain separator as a field element.
    #[must_use]
    pub fn as_field(self) -> FieldElement {
        FieldElement::from_u64(self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tags_match_the_frozen_domain_table() {
        // docs/trade-commitment-v1.md and circuits/shared/trade-commitment-v1.js.
        assert_eq!(Domain::ASSET_V1.tag(), 18387490596738609);
        assert_eq!(Domain::FEE_V1.tag(), 301809882673);
        assert_eq!(Domain::TRADE_A_V1.tag(), 23734351151715889);
        assert_eq!(Domain::TRADE_B_V1.tag(), 23734351168493105);
        assert_eq!(Domain::TRADE_META_V1.tag(), 23734351353042481);
        assert_eq!(Domain::TRADE_V1.tag(), 6075990608753677873);
        assert_eq!(Domain::ZEC_ASSET_TAG.tag(), 5915971);
        // circuits/shared/eligibility-v1.js.
        assert_eq!(Domain::SUBJECT_V1.tag(), 6004778564925477937);
        assert_eq!(Domain::RECEIVER_V1.tag(), 5928218449365324337);
        assert_eq!(Domain::RECIPIENT_BINDING_V1.tag(), 5927669780177634353);
        assert_eq!(Domain::FEE_RECIPIENT_V1.tag(), 19793697433736753);
        assert_eq!(Domain::RECIPIENT_V1.tag(), 90449063990833);
        assert_eq!(Domain::CREDENTIAL_META_V1.tag(), 4851015908287927361);
        assert_eq!(Domain::CREDENTIAL_V2.tag(), 18949280892933682);
    }

    #[test]
    fn tags_are_their_ascii_labels_read_big_endian() {
        for (label, domain) in [
            ("ASSETV1", Domain::ASSET_V1),
            ("FEEV1", Domain::FEE_V1),
            ("TRDA_V1", Domain::TRADE_A_V1),
            ("TRDB_V1", Domain::TRADE_B_V1),
            ("TRDM_V1", Domain::TRADE_META_V1),
            ("TRADE_V1", Domain::TRADE_V1),
            ("ZEC", Domain::ZEC_ASSET_TAG),
            ("SUBJECT1", Domain::SUBJECT_V1),
            ("RECEIVR1", Domain::RECEIVER_V1),
            ("RCPBIND1", Domain::RECIPIENT_BINDING_V1),
            ("FRCPTV1", Domain::FEE_RECIPIENT_V1),
            ("RCPTV1", Domain::RECIPIENT_V1),
            ("CREDMETA", Domain::CREDENTIAL_META_V1),
            ("CRED_V2", Domain::CREDENTIAL_V2),
        ] {
            let mut expected = 0u64;
            for byte in label.bytes() {
                expected = (expected << 8) | u64::from(byte);
            }
            assert_eq!(domain.tag(), expected, "{label}");
        }
    }
}
