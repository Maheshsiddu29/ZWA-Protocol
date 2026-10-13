//! Lineage request container, version 2 (contract §G4, M8).
//!
//! The research v1 object (`ZWA-LB-LINEAGE:1`, `object.rs`) is the framing
//! reference. Version 2 adds the `vk_id` field (§B3) and is decoded under the
//! exact-length proof rule (§G2). Root and terminal authentication evidence
//! are not part of this version: their layout belongs to the Layer-B port of
//! Phase 2, which will add them as a new container version.
//!
//! ```text
//! "ZWA-LINEAGE-REQ:"(16) ‖ version u8 = 2 ‖ vk_id(32) ‖ chain_ref(117)
//!   ‖ root_cmx(32) ‖ terminal_cmx(32) ‖ N u16 LE
//!   ‖ N × ( cmx_old(32) ‖ nf_old(32) ‖ cmx_new(32) ‖ proof_len u16 LE ‖ proof )
//! ```
//!
//! Decoding is strict and in two passes. Framing first: exact tag, then the
//! version (an unknown one is `UNSUPPORTED`), fixed-size fields,
//! `1 ≤ N ≤ 1024`, `1 ≤ proof_len ≤ 16384`, and no trailing bytes; any
//! failure is `INVALID_ENCODING` (`MALFORMED`). Then the `vk_id` must be VK\*'s
//! (`FOREIGN_VERIFIER`), and the fields are canonicalized by
//! [`RootedLineageEvidence::parse`], where a proof whose length is not
//! exactly 5,120 is `NON_CANONICAL_PROOF(i)`.

use crate::chain_ref::CHAINREF_LEN;
use crate::evidence::{RawLineageEvidence, RawStep, RootedLineageEvidence, MAX_STEPS};
use crate::frozen::FROZEN_VK_ID;
use crate::verdict::{FailureCode, VerificationFailure};

/// Domain tag of the request container.
pub const REQUEST_TAG: &[u8; 16] = b"ZWA-LINEAGE-REQ:";
/// The only supported container version.
pub const REQUEST_VERSION: u8 = 2;
/// Framing bound on a proof length field (research v1 `MAX_PROOF`). Only
/// exactly 5,120 bytes is canonical.
pub const MAX_PROOF_FIELD: usize = 16384;

/// A decoded request: the key it names and its canonical evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LineageRequest {
    /// The verifying-key identity the request names. Always VK\*'s after a
    /// successful decode.
    pub vk_id: [u8; 32],
    /// Canonical evidence.
    pub evidence: RootedLineageEvidence,
}

struct Reader<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], VerificationFailure> {
        let end = self.at.checked_add(n).ok_or_else(malformed)?;
        let s = self.bytes.get(self.at..end).ok_or_else(malformed)?;
        self.at = end;
        Ok(s)
    }

    fn u16(&mut self) -> Result<usize, VerificationFailure> {
        let b: [u8; 2] = self.take(2)?.try_into().map_err(|_| malformed())?;
        Ok(usize::from(u16::from_le_bytes(b)))
    }
}

fn malformed() -> VerificationFailure {
    VerificationFailure::new(FailureCode::InvalidEncoding)
}

/// Decodes and canonicalizes a version-2 request.
///
/// # Errors
///
/// The first failure, in the order given in the module documentation.
pub fn decode_request(bytes: &[u8]) -> Result<LineageRequest, VerificationFailure> {
    let mut r = Reader { bytes, at: 0 };
    if r.take(16)? != REQUEST_TAG {
        return Err(malformed());
    }
    if r.take(1)? != [REQUEST_VERSION] {
        return Err(VerificationFailure::new(FailureCode::UnsupportedVersion));
    }
    let vk_id: [u8; 32] = r.take(32)?.try_into().map_err(|_| malformed())?;
    let chain_ref = r.take(CHAINREF_LEN)?;
    let root_cmx = r.take(32)?;
    let terminal_cmx = r.take(32)?;
    let n = r.u16()?;
    if n == 0 || n > MAX_STEPS {
        return Err(malformed());
    }
    let mut steps = Vec::with_capacity(n);
    for _ in 0..n {
        let cmx_old = r.take(32)?;
        let nf_old = r.take(32)?;
        let cmx_new = r.take(32)?;
        let len = r.u16()?;
        if len == 0 || len > MAX_PROOF_FIELD {
            return Err(malformed());
        }
        let proof = r.take(len)?;
        steps.push(RawStep {
            cmx_old,
            nf_old,
            cmx_new,
            proof,
        });
    }
    if r.at != bytes.len() {
        return Err(malformed());
    }
    if vk_id != FROZEN_VK_ID {
        return Err(VerificationFailure::new(FailureCode::ForeignVerifier));
    }
    let evidence = RootedLineageEvidence::parse(&RawLineageEvidence {
        chain_ref: Some(chain_ref),
        root_cmx,
        terminal_cmx,
        steps,
    })?;
    Ok(LineageRequest { vk_id, evidence })
}

/// Encodes canonical evidence as a version-2 request naming VK\*.
///
/// # Errors
///
/// `CHAIN_CONTEXT_MISSING` if the evidence has no `chain_ref`: version 2 has
/// no encoding for an absent one.
pub fn encode_request(evidence: &RootedLineageEvidence) -> Result<Vec<u8>, VerificationFailure> {
    let chain_ref = evidence
        .chain_ref()
        .ok_or(VerificationFailure::new(FailureCode::ChainContextMissing))?;
    let n = u16::try_from(evidence.len()).map_err(|_| malformed())?;
    let mut v = Vec::new();
    v.extend_from_slice(REQUEST_TAG);
    v.push(REQUEST_VERSION);
    v.extend_from_slice(&FROZEN_VK_ID);
    v.extend_from_slice(&chain_ref.encode());
    v.extend_from_slice(evidence.root_cmx().as_bytes());
    v.extend_from_slice(evidence.terminal_cmx().as_bytes());
    v.extend_from_slice(&n.to_le_bytes());
    for s in evidence.steps() {
        let proof = s.proof.as_bytes();
        let len = u16::try_from(proof.len()).map_err(|_| malformed())?;
        v.extend_from_slice(&s.public_inputs.to_bytes());
        v.extend_from_slice(&len.to_le_bytes());
        v.extend_from_slice(proof);
    }
    Ok(v)
}
