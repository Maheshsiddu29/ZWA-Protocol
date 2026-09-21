//! The frozen ZWA commitment schemas.
//!
//! The Poseidon staging below is copied from the Phase 0F/0G source of truth
//! (`circuits/provenance/rwa_trade_provenance_v1.circom`,
//! `circuits/eligibility/rwa_investor_eligibility_v1.circom`, and
//! `circuits/shared/*.js`) and must never diverge from it:
//!
//! ```text
//! OfferedAssetCommitment   = H(ASSETV1, offeredAssetHi, offeredAssetLo)
//! RequestedAssetCommitment = H(ASSETV1, requestedAssetHi, requestedAssetLo)
//! FeeCommitment            = H(FEEV1, ZEC, matcherFeeAmount,
//!                              matcherFeeRecipientCommitment)
//! TradePartA               = H(TRDA_V1, OfferedAssetCommitment, offeredAmount,
//!                              RequestedAssetCommitment)
//! TradePartB               = H(TRDB_V1, requestedAmount, recipientCommitment,
//!                              policyRoot)
//! TradeMeta                = H(TRDM_V1, FeeCommitment, nonce, expiry)
//! TradeCommitmentV1        = H(TRADE_V1, TradePartA, TradePartB, TradeMeta)
//! ```
//!
//! and the Phase 0G recipient binding:
//!
//! ```text
//! SubjectCommitment   = H(SUBJECT1, subjectSecret)
//! ReceiverCommitment  = H(RECEIVR1, receiverLimb0, receiverLimb1, receiverLimb2)
//! recipientCommitment = H(RCPBIND1, SubjectCommitment, ReceiverCommitment)
//! ```

use zwa_protocol::error::{ProtocolError, Result};
use zwa_protocol::{
    AssetBaseBytes, AssetCommitment, FeeCommitment, FieldElement, MatcherFee, OrchardReceiverBytes,
    ReceiverCommitment, RecipientCommitment, SubjectCommitment, SubjectSecret, TradeCommitment,
    TradeIntent, TradeMeta, TradePartA, TradePartB,
};

use crate::asset_base::{encode_asset_base, little_endian_u128};
use crate::domain::Domain;
use crate::poseidon::hash;
use crate::receiver::encode_receiver;

/// Chunk width of the canonical-byte commitment staging.
const CANONICAL_BYTE_CHUNK: usize = 16;

/// Commits to a canonical `AssetBase` as `H(ASSETV1, hi, lo)`.
#[must_use]
pub fn asset_commitment(asset: &AssetBaseBytes) -> AssetCommitment {
    let limbs = encode_asset_base(asset);
    AssetCommitment::new(hash([
        Domain::ASSET_V1.as_field(),
        FieldElement::from_u128(limbs.hi),
        FieldElement::from_u128(limbs.lo),
    ]))
}

/// Commits to a credential subject secret as `H(SUBJECT1, subjectSecret)`.
#[must_use]
pub fn subject_commitment(secret: SubjectSecret) -> SubjectCommitment {
    SubjectCommitment::new(hash([Domain::SUBJECT_V1.as_field(), secret.value()]))
}

/// Commits to a canonical raw Orchard receiver as
/// `H(RECEIVR1, limb0, limb1, limb2)`.
#[must_use]
pub fn receiver_commitment(receiver: &OrchardReceiverBytes) -> ReceiverCommitment {
    let limbs = encode_receiver(receiver);
    ReceiverCommitment::new(hash([
        Domain::RECEIVER_V1.as_field(),
        FieldElement::from_u128(limbs.limb0()),
        FieldElement::from_u128(limbs.limb1()),
        FieldElement::from_u128(limbs.limb2()),
    ]))
}

/// Binds a subject commitment to a receiver commitment as
/// `H(RCPBIND1, SubjectCommitment, ReceiverCommitment)`.
///
/// This pins the exact intended receiver to the trade. It does not prove
/// knowledge of that receiver's spending key; recipient-control authentication
/// is a separate matcher responsibility.
#[must_use]
pub fn recipient_commitment(
    subject: SubjectCommitment,
    receiver: ReceiverCommitment,
) -> RecipientCommitment {
    RecipientCommitment::new(hash([
        Domain::RECIPIENT_BINDING_V1.as_field(),
        subject.value(),
        receiver.value(),
    ]))
}

/// The intermediate values of the Phase 0G recipient binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RecipientBinding {
    /// `H(SUBJECT1, subjectSecret)`.
    pub subject_commitment: SubjectCommitment,
    /// `H(RECEIVR1, limb0, limb1, limb2)`.
    pub receiver_commitment: ReceiverCommitment,
    /// `H(RCPBIND1, SubjectCommitment, ReceiverCommitment)`.
    pub recipient_commitment: RecipientCommitment,
}

/// Derives the full Phase 0G recipient binding from its private inputs.
#[must_use]
pub fn bind_recipient(secret: SubjectSecret, receiver: &OrchardReceiverBytes) -> RecipientBinding {
    let subject = subject_commitment(secret);
    let receiver_commitment = receiver_commitment(receiver);
    RecipientBinding {
        subject_commitment: subject,
        receiver_commitment,
        recipient_commitment: recipient_commitment(subject, receiver_commitment),
    }
}

/// Commits to the native ZEC matcher fee as
/// `H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment)`.
#[must_use]
pub fn fee_commitment(fee: &MatcherFee) -> FeeCommitment {
    FeeCommitment::new(hash([
        Domain::FEE_V1.as_field(),
        Domain::ZEC_ASSET_TAG.as_field(),
        FieldElement::from_u64(fee.amount.get()),
        fee.recipient_commitment.value(),
    ]))
}

/// Commits to a canonical byte string under `domain`.
///
/// This is the frozen Phase 0F staging for committing to raw canonical bytes of
/// any length:
///
/// ```text
/// state = H(domain, byteLength, limbCount)
/// state = H(state, limb[i], i)   for each 16-byte little-endian chunk
/// ```
#[must_use]
pub fn canonical_bytes_commitment(domain: Domain, bytes: &[u8]) -> FieldElement {
    let chunks: Vec<u128> = bytes
        .chunks(CANONICAL_BYTE_CHUNK)
        .map(little_endian_u128)
        .collect();
    let mut state = hash([
        domain.as_field(),
        FieldElement::from_u64(bytes.len() as u64),
        FieldElement::from_u64(chunks.len() as u64),
    ]);
    for (index, limb) in chunks.iter().enumerate() {
        state = hash([
            state,
            FieldElement::from_u128(*limb),
            FieldElement::from_u64(index as u64),
        ]);
    }
    state
}

/// Commits to the matcher's fee receiver under `FRCPTV1`.
///
/// This is the derivation behind `matcherFeeRecipientCommitment` in both the
/// Phase 0F and Phase 0G reference fixtures.
#[must_use]
pub fn matcher_fee_recipient_commitment(receiver: &OrchardReceiverBytes) -> RecipientCommitment {
    RecipientCommitment::new(canonical_bytes_commitment(
        Domain::FEE_RECIPIENT_V1,
        receiver.as_bytes(),
    ))
}

/// Every intermediate value of `TradeCommitmentV1`.
///
/// Exposed so that tests and future matcher diagnostics can compare against the
/// Phase 0 fixtures stage by stage instead of only comparing the final value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeCommitmentParts {
    /// `H(ASSETV1, offeredAssetHi, offeredAssetLo)`.
    pub offered_asset_commitment: AssetCommitment,
    /// `H(ASSETV1, requestedAssetHi, requestedAssetLo)`.
    pub requested_asset_commitment: AssetCommitment,
    /// `H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment)`.
    pub fee_commitment: FeeCommitment,
    /// `H(TRDA_V1, OfferedAssetCommitment, offeredAmount, RequestedAssetCommitment)`.
    pub trade_part_a: TradePartA,
    /// `H(TRDB_V1, requestedAmount, recipientCommitment, policyRoot)`.
    pub trade_part_b: TradePartB,
    /// `H(TRDM_V1, FeeCommitment, nonce, expiry)`.
    pub trade_meta: TradeMeta,
    /// `H(TRADE_V1, TradePartA, TradePartB, TradeMeta)`.
    pub trade_commitment: TradeCommitment,
}

/// Computes every stage of `TradeCommitmentV1` for a canonical intent.
///
/// This is a pure total function of the intent: identical intents always
/// produce identical commitments, and no other component may reimplement it.
#[must_use]
pub fn trade_commitment_parts(intent: &TradeIntent) -> TradeCommitmentParts {
    let offered_asset_commitment = asset_commitment(&intent.offered_asset);
    let requested_asset_commitment = asset_commitment(&intent.requested_asset);
    let fee = fee_commitment(&intent.matcher_fee);

    let trade_part_a = TradePartA::new(hash([
        Domain::TRADE_A_V1.as_field(),
        offered_asset_commitment.value(),
        FieldElement::from_u64(intent.offered_amount.get()),
        requested_asset_commitment.value(),
    ]));
    let trade_part_b = TradePartB::new(hash([
        Domain::TRADE_B_V1.as_field(),
        FieldElement::from_u64(intent.requested_amount.get()),
        intent.recipient_commitment.value(),
        intent.policy_root.value(),
    ]));
    let trade_meta = TradeMeta::new(hash([
        Domain::TRADE_META_V1.as_field(),
        fee.value(),
        FieldElement::from_u64(intent.nonce.get()),
        FieldElement::from_u64(intent.expiry.get()),
    ]));
    let trade_commitment = TradeCommitment::new(hash([
        Domain::TRADE_V1.as_field(),
        trade_part_a.value(),
        trade_part_b.value(),
        trade_meta.value(),
    ]));

    TradeCommitmentParts {
        offered_asset_commitment,
        requested_asset_commitment,
        fee_commitment: fee,
        trade_part_a,
        trade_part_b,
        trade_meta,
        trade_commitment,
    }
}

/// Computes `TradeCommitmentV1` for a canonical intent.
#[must_use]
pub fn trade_commitment_v1(intent: &TradeIntent) -> TradeCommitment {
    trade_commitment_parts(intent).trade_commitment
}

/// Recomputes `TradeCommitmentV1` and checks it against a presented value.
///
/// The matcher gate requires that the commitment it recomputes from the
/// authenticated intent equals the public commitment both proofs expose.
///
/// # Errors
///
/// Returns [`ProtocolError::CommitmentMismatch`] when the recomputed commitment
/// differs from `expected`.
pub fn verify_trade_commitment(intent: &TradeIntent, expected: TradeCommitment) -> Result<()> {
    let actual = trade_commitment_v1(intent);
    if actual != expected {
        return Err(ProtocolError::CommitmentMismatch {
            expected: expected.to_string(),
            actual: actual.to_string(),
        });
    }
    Ok(())
}
