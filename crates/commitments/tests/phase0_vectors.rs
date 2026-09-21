//! Phase 0 compatibility vectors.
//!
//! These tests read the migrated deterministic synthetic fixtures in
//! `tests/fixtures/` directly, so there is no duplicated copy of the Phase 0
//! data anywhere in the Rust tree. The expected values are authoritative: if
//! Rust disagrees, the Rust implementation is wrong.
//!
//! - `reference-trade-v1.json` is the Phase 0F provenance reference trade.
//! - `eligible-reference-trade-v1.json` is the Phase 0G eligibility-aware trade.

#![allow(clippy::unwrap_used)]

use serde_json::Value;
use zwa_commitments::{
    asset_commitment, bind_recipient, canonical_bytes_commitment, fee_commitment,
    matcher_fee_recipient_commitment, trade_commitment_parts, trade_commitment_v1,
    verify_trade_commitment, Domain,
};
use zwa_protocol::error::ProtocolError;
use zwa_protocol::{
    AssetBaseBytes, MatcherFee, OrchardReceiverBytes, PolicyRoot, RecipientCommitment,
    SubjectSecret, TradeAmount, TradeCommitment, TradeExpiry, TradeIntent, TradeNonce,
    ZatoshiAmount,
};

/// Mandatory Phase 0F provenance reference `TradeCommitmentV1`.
const PHASE_0F_GOLDEN_TRADE_COMMITMENT: &str =
    "7409670081847436957289371955571360481923983184454289247710022466448715682310";

/// Mandatory Phase 0G eligibility-aware `TradeCommitmentV1`.
const PHASE_0G_GOLDEN_TRADE_COMMITMENT: &str =
    "10187400613857124614980227259922066295752635539032972479692659299555113110306";

fn phase_0f() -> Value {
    let raw = include_str!("../../../tests/fixtures/reference-trade-v1.json");
    serde_json::from_str(raw).unwrap()
}

fn phase_0g() -> Value {
    let raw = include_str!("../../../tests/fixtures/eligible-reference-trade-v1.json");
    serde_json::from_str(raw).unwrap()
}

fn text(node: &Value, key: &str) -> String {
    node[key]
        .as_str()
        .unwrap_or_else(|| panic!("fixture field {key} must be a string"))
        .to_owned()
}

fn amount(node: &Value, key: &str) -> TradeAmount {
    TradeAmount::new(text(node, key).parse().unwrap())
}

fn decode_hex(text: &str) -> Vec<u8> {
    assert!(
        text.len().is_multiple_of(2),
        "hexadecimal must have even length"
    );
    (0..text.len() / 2)
        .map(|index| u8::from_str_radix(&text[index * 2..index * 2 + 2], 16).unwrap())
        .collect()
}

/// Builds a `TradeIntent` from a fixture `trade` object, taking the recipient
/// commitment from the caller so that Phase 0G can supply a value it derived
/// rather than a value it read.
fn intent_from(
    trade: &Value,
    recipient_commitment: RecipientCommitment,
    fee_key: &str,
) -> TradeIntent {
    TradeIntent {
        offered_asset: AssetBaseBytes::from_hex(&text(trade, "offeredAssetBase")).unwrap(),
        offered_amount: amount(trade, "offeredAmount"),
        requested_asset: AssetBaseBytes::from_hex(&text(trade, "requestedAssetBase")).unwrap(),
        requested_amount: amount(trade, "requestedAmount"),
        recipient_commitment,
        policy_root: PolicyRoot::from_decimal_str(&text(trade, "policyRoot")).unwrap(),
        matcher_fee: MatcherFee::new(
            ZatoshiAmount::new(text(trade, fee_key).parse().unwrap()),
            RecipientCommitment::from_decimal_str(&text(trade, "matcherFeeRecipientCommitment"))
                .unwrap(),
        ),
        nonce: TradeNonce::new(text(trade, "nonce").parse().unwrap()),
        expiry: TradeExpiry::new(text(trade, "expiry").parse().unwrap()),
    }
}

#[test]
fn phase0f_reference_trade_commitment_matches_exactly() {
    let fixture = phase_0f();
    let trade = &fixture["trade"];
    let intent = intent_from(
        trade,
        RecipientCommitment::from_decimal_str(&text(trade, "recipientCommitment")).unwrap(),
        "matcherFeeAmountZatoshis",
    );
    let parts = trade_commitment_parts(&intent);

    assert_eq!(
        parts.offered_asset_commitment.to_string(),
        text(trade, "offeredAssetCommitment")
    );
    assert_eq!(
        parts.requested_asset_commitment.to_string(),
        text(trade, "requestedAssetCommitment")
    );
    assert_eq!(
        parts.fee_commitment.to_string(),
        text(trade, "feeCommitment")
    );
    assert_eq!(parts.trade_part_a.to_string(), text(trade, "tradePartA"));
    assert_eq!(parts.trade_part_b.to_string(), text(trade, "tradePartB"));
    assert_eq!(parts.trade_meta.to_string(), text(trade, "tradeMeta"));
    assert_eq!(
        parts.trade_commitment.to_string(),
        text(trade, "tradeCommitment")
    );
    assert_eq!(
        parts.trade_commitment.to_string(),
        PHASE_0F_GOLDEN_TRADE_COMMITMENT
    );
    assert_eq!(
        text(&fixture["circuitInput"], "tradeCommitment"),
        PHASE_0F_GOLDEN_TRADE_COMMITMENT
    );
}

#[test]
fn phase0f_canonical_byte_recipient_commitments_match() {
    let fixture = phase_0f();
    let recipients = &fixture["canonicalRecipients"];

    // The Phase 0F trade recipient predates the Phase 0G recipient binding and
    // commits to raw canonical bytes under RCPTV1.
    let trade_recipient_bytes = decode_hex(&text(recipients, "tradeRecipientBytes"));
    assert_eq!(
        canonical_bytes_commitment(Domain::RECIPIENT_V1, &trade_recipient_bytes).to_string(),
        text(recipients, "recipientCommitment")
    );

    // The matcher fee receiver commitment is the same derivation under FRCPTV1
    // in both Phase 0F and Phase 0G.
    let fee_receiver =
        OrchardReceiverBytes::from_hex(&text(recipients, "matcherFeeReceiverBytes")).unwrap();
    assert_eq!(
        matcher_fee_recipient_commitment(&fee_receiver).to_string(),
        text(recipients, "matcherFeeRecipientCommitment")
    );
}

#[test]
fn phase0g_recipient_binding_matches() {
    let fixture = phase_0g();
    let expected = &fixture["recipientBinding"];
    let secret =
        SubjectSecret::from_decimal_str(&text(&fixture["trade"], "subjectSecret")).unwrap();
    let receiver =
        OrchardReceiverBytes::from_hex(&text(&fixture["receiver"], "institutionABytes")).unwrap();

    let binding = bind_recipient(secret, &receiver);
    assert_eq!(
        binding.subject_commitment.to_string(),
        text(expected, "subjectCommitment")
    );
    assert_eq!(
        binding.receiver_commitment.to_string(),
        text(expected, "receiverCommitment")
    );
    assert_eq!(
        binding.recipient_commitment.to_string(),
        text(expected, "recipientCommitment")
    );
    assert_eq!(
        binding.subject_commitment.to_string(),
        text(&fixture["credential"], "subjectCommitment")
    );
}

#[test]
fn phase0g_eligibility_aware_trade_commitment_matches_exactly() {
    let fixture = phase_0g();
    let trade = &fixture["trade"];
    let secret = SubjectSecret::from_decimal_str(&text(trade, "subjectSecret")).unwrap();
    let receiver =
        OrchardReceiverBytes::from_hex(&text(&fixture["receiver"], "institutionABytes")).unwrap();

    // The recipient commitment is derived here, not read from the fixture.
    let binding = bind_recipient(secret, &receiver);
    assert_eq!(
        binding.recipient_commitment.to_string(),
        text(trade, "recipientCommitment")
    );

    let intent = intent_from(trade, binding.recipient_commitment, "matcherFeeAmount");
    let parts = trade_commitment_parts(&intent);

    assert_eq!(
        parts.offered_asset_commitment.to_string(),
        text(trade, "offeredAssetCommitment")
    );
    assert_eq!(
        parts.requested_asset_commitment.to_string(),
        text(trade, "requestedAssetCommitment")
    );
    assert_eq!(
        parts.fee_commitment.to_string(),
        text(trade, "feeCommitment")
    );
    assert_eq!(parts.trade_part_a.to_string(), text(trade, "tradePartA"));
    assert_eq!(parts.trade_part_b.to_string(), text(trade, "tradePartB"));
    assert_eq!(parts.trade_meta.to_string(), text(trade, "tradeMeta"));
    assert_eq!(
        parts.trade_commitment.to_string(),
        PHASE_0G_GOLDEN_TRADE_COMMITMENT
    );
}

#[test]
fn phase0g_second_trade_binds_the_second_receiver() {
    let fixture = phase_0g();
    let trade = &fixture["tradeB"];
    let secret = SubjectSecret::from_decimal_str(&text(trade, "subjectSecret")).unwrap();
    let receiver =
        OrchardReceiverBytes::from_hex(&text(&fixture["receiver"], "institutionBBytes")).unwrap();

    let binding = bind_recipient(secret, &receiver);
    assert_eq!(
        binding.recipient_commitment.to_string(),
        text(trade, "recipientCommitment")
    );

    let intent = intent_from(trade, binding.recipient_commitment, "matcherFeeAmount");
    assert_eq!(
        trade_commitment_v1(&intent).to_string(),
        text(trade, "tradeCommitment")
    );
    // Same assets, amounts, fee, and policy; a different receiver and nonce
    // therefore a different commitment.
    assert_ne!(
        text(trade, "tradeCommitment"),
        PHASE_0G_GOLDEN_TRADE_COMMITMENT
    );
}

#[test]
fn both_phase0g_proof_inputs_expose_the_same_trade_commitment() {
    let fixture = phase_0g();
    assert_eq!(
        text(&fixture["provenanceInput"], "tradeCommitment"),
        PHASE_0G_GOLDEN_TRADE_COMMITMENT
    );
    assert_eq!(
        text(&fixture["eligibilityInput"], "tradeCommitment"),
        PHASE_0G_GOLDEN_TRADE_COMMITMENT
    );
}

#[test]
fn phase0g_asset_commitments_match_the_circuit_input_limbs() {
    let fixture = phase_0g();
    let input = &fixture["eligibilityInput"];
    let offered = AssetBaseBytes::from_hex(&text(&fixture["trade"], "offeredAssetBase")).unwrap();
    let limbs = zwa_commitments::encode_asset_base(&offered);
    assert_eq!(limbs.hi.to_string(), text(input, "offeredAssetHi"));
    assert_eq!(limbs.lo.to_string(), text(input, "offeredAssetLo"));
    assert_eq!(
        asset_commitment(&offered).to_string(),
        text(&fixture["trade"], "offeredAssetCommitment")
    );

    let receiver =
        OrchardReceiverBytes::from_hex(&text(&fixture["receiver"], "institutionABytes")).unwrap();
    let receiver_limbs = zwa_commitments::encode_receiver(&receiver);
    assert_eq!(
        receiver_limbs.limb0().to_string(),
        text(input, "receiverLimb0")
    );
    assert_eq!(
        receiver_limbs.limb1().to_string(),
        text(input, "receiverLimb1")
    );
    assert_eq!(
        receiver_limbs.limb2().to_string(),
        text(input, "receiverLimb2")
    );
}

#[test]
fn fee_commitment_matches_the_shared_phase0_value() {
    // Both fixtures commit the same 5-zatoshi fee to the same matcher receiver.
    let fee = MatcherFee::new(
        ZatoshiAmount::new(5),
        RecipientCommitment::from_decimal_str(
            "1800273984094439421343257609634901689467303577600258601269976617936586404380",
        )
        .unwrap(),
    );
    let expected = "9224703263949515639127683057768488660604225733035241220145439229774558899084";
    assert_eq!(fee_commitment(&fee).to_string(), expected);
    assert_eq!(text(&phase_0f()["trade"], "feeCommitment"), expected);
    assert_eq!(text(&phase_0g()["trade"], "feeCommitment"), expected);
}

#[test]
fn commitment_verification_detects_a_substituted_commitment() {
    let fixture = phase_0g();
    let trade = &fixture["trade"];
    let intent = intent_from(
        trade,
        RecipientCommitment::from_decimal_str(&text(trade, "recipientCommitment")).unwrap(),
        "matcherFeeAmount",
    );
    let golden = TradeCommitment::from_decimal_str(PHASE_0G_GOLDEN_TRADE_COMMITMENT).unwrap();
    assert!(verify_trade_commitment(&intent, golden).is_ok());

    let other = TradeCommitment::from_decimal_str(PHASE_0F_GOLDEN_TRADE_COMMITMENT).unwrap();
    assert_eq!(
        verify_trade_commitment(&intent, other),
        Err(ProtocolError::CommitmentMismatch {
            expected: PHASE_0F_GOLDEN_TRADE_COMMITMENT.to_owned(),
            actual: PHASE_0G_GOLDEN_TRADE_COMMITMENT.to_owned(),
        })
    );
}
