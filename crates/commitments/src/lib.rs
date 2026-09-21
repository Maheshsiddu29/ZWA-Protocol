//! The canonical ZWA commitment engine.
//!
//! This crate is the only place `TradeCommitmentV1`, the `AssetBase` limb
//! encoding, the raw Orchard receiver limb encoding, and the Phase 0G recipient
//! binding are implemented. Matcher, RFQ, settlement, and frontend components
//! must call these functions; recomputing the Poseidon staging elsewhere would
//! create a second source of truth for a frozen contract.
//!
//! Every commitment function is a pure, total function of validated
//! [`zwa_protocol`] types, so equal inputs always yield equal commitments.
//!
//! # Phase 0 compatibility
//!
//! The Rust output is asserted to equal the frozen Phase 0 JavaScript and Circom
//! results. `tests/poseidon_compat.rs` pins the Poseidon parameters and
//! `tests/phase0_vectors.rs` reproduces both reference fixtures, including the
//! two mandatory golden `TradeCommitmentV1` values.

#![cfg_attr(test, allow(clippy::unwrap_used))]

pub mod asset_base;
pub mod domain;
pub mod poseidon;
pub mod receiver;
pub mod trade;

pub use asset_base::{decode_asset_base, encode_asset_base, AssetBaseLimbs};
pub use domain::Domain;
pub use poseidon::poseidon;
pub use receiver::{decode_receiver, encode_receiver, ReceiverLimbs};
pub use trade::{
    asset_commitment, bind_recipient, canonical_bytes_commitment, fee_commitment,
    matcher_fee_recipient_commitment, receiver_commitment, recipient_commitment,
    subject_commitment, trade_commitment_parts, trade_commitment_v1, verify_trade_commitment,
    RecipientBinding, TradeCommitmentParts,
};
