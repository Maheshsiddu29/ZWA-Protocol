//! Canonical ZWA Protocol types, validation, and state rules.
//!
//! This crate is the single source of truth for the frozen Phase 0F/0G
//! protocol contract. Matcher, RFQ, settlement, and frontend components consume
//! these types; none of them may define their own trade schema, re-derive the
//! `AssetBase` or Orchard receiver encodings, or reimplement the replay rules.
//!
//! The Poseidon staging of `TradeCommitmentV1` itself lives in the companion
//! `zwa-commitments` crate, which depends on this one.
//!
//! # Scope
//!
//! This crate contains no networking, no persistence, no transaction
//! construction, and no root signature verification. Root envelopes carry
//! opaque signature material only: no concrete root signature algorithm has
//! been approved yet, so nothing here authenticates a root.

#![cfg_attr(test, allow(clippy::unwrap_used))]

pub mod bytes;
pub mod error;
pub mod field;
pub mod intent;
pub mod numbers;
pub mod values;

pub use bytes::{AssetBaseBytes, OrchardReceiverBytes};
pub use error::{ProtocolError, Result};
pub use field::FieldElement;
pub use intent::TradeIntent;
pub use numbers::{RootVersion, TradeAmount, TradeExpiry, TradeNonce, UnixSeconds, ZatoshiAmount};
pub use values::{
    ActiveCredentialRoot, AssetCommitment, AuthorizedIssuanceRoot, FeeCommitment, MatcherFee,
    PolicyRoot, ReceiverCommitment, RecipientCommitment, SubjectCommitment, SubjectSecret,
    TradeCommitment, TradeMeta, TradePartA, TradePartB,
};
