//! Canonical ZWA issuer/credential root envelopes and credential value types.
//!
//! This crate is the single source of truth for:
//!
//! - issuer authorized-issuance root envelopes;
//! - credential-authority active-root envelopes;
//! - deterministic unsigned-payload serialization;
//! - non-cryptographic structural and freshness validation;
//! - credential and policy value types (`InvestorClass`, `Jurisdiction`, …).
//!
//! Signature material is opaque. No root signature algorithm has been approved,
//! so nothing here is cryptographically authenticated. Structural validation
//! is not authentication.
//!
//! Matcher and RFQ code must consume these types rather than defining their
//! own root payloads or credential schemas.

#![cfg_attr(test, allow(clippy::unwrap_used))]

pub mod envelope;
pub mod ids;
pub mod values;

pub use envelope::{
    CredentialRootEnvelope, CredentialRootPayload, IssuerRootEnvelope, IssuerRootPayload,
};
pub use ids::{AuthorityKeyId, IssuerKeyId};
pub use values::{
    CredentialAuthorityCommitment, CredentialExpiry, CredentialNonce, InvestorClass, Jurisdiction,
};
