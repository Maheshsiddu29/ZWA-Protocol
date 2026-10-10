//! Canonical lineage evidence (contract §A1, §B6, §G).
//!
//! Untrusted input enters through [`RootedLineageEvidence::parse`] (or the
//! wire decoder in [`crate::container`], which calls it). Every value inside a
//! [`RootedLineageEvidence`] is therefore already canonical: field elements
//! are `< q_P`, every proof is exactly [`STEP_PROOF_LEN`] bytes, and
//! `1 ≤ N ≤ MAX_STEPS`. Verification never re-interprets raw bytes.
//!
//! Parse order follows the contract's acceptance order (§C11, §B7): `chain_ref`,
//! then `root_cmx`, then the steps in index order (each step's tuple, then its
//! proof), then `terminal_cmx`. The first failure is returned.

use core::fmt;

use crate::chain_ref::{ChainRef, ChainRefError};
use crate::field::{CanonicalCmx, CanonicalNullifier};
use crate::verdict::{FailureCode, FieldRole, VerificationFailure};

/// Exact length of an R2B-L1 step proof under VK\* (contract §G2).
///
/// The pinned halo2 verifier reads a VK-determined number of transcript
/// elements and ignores anything after them (`CANONICAL_PROOF_ENCODING.md`).
/// Requiring exactly this length rejects trailing bytes before any proof is
/// hashed, stored or verified.
pub const STEP_PROOF_LEN: usize = 5120;

/// Maximum number of Design-A steps (research `object.rs` `MAX_N`).
pub const MAX_STEPS: usize = 1024;

/// BLAKE2b personalization of [`RootedLineageEvidence::lineage_statement_id`].
pub const STATEMENT_ID_PERSONAL: &[u8; 16] = b"ZWA_LineageStmt1";

/// The public tuple `x_i = (cmx_old, nf_old, cmx_new)` of one
/// `NormalLineageStepV2` step.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct LineagePublicInputs {
    /// `cmx` of the spent note.
    pub cmx_old: CanonicalCmx,
    /// Nullifier of the spent note; the circuit proves `ρ_new = nf_old`.
    pub nf_old: CanonicalNullifier,
    /// `cmx` of the created note.
    pub cmx_new: CanonicalCmx,
}

impl LineagePublicInputs {
    /// Canonical 96-byte encoding `cmx_old ‖ nf_old ‖ cmx_new`.
    #[must_use]
    pub fn to_bytes(&self) -> [u8; 96] {
        let mut out = [0u8; 96];
        let fields = [
            self.cmx_old.as_bytes(),
            self.nf_old.as_bytes(),
            self.cmx_new.as_bytes(),
        ];
        for (chunk, field) in out.chunks_exact_mut(32).zip(fields) {
            chunk.copy_from_slice(field);
        }
        out
    }
}

/// An R2B-L1 step proof of exactly [`STEP_PROOF_LEN`] bytes.
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct CanonicalStepProof(Box<[u8; STEP_PROOF_LEN]>);

impl CanonicalStepProof {
    /// Accepts exactly [`STEP_PROOF_LEN`] bytes. Empty, short, long and
    /// trailing-byte encodings are all rejected; nothing is truncated.
    ///
    /// # Errors
    ///
    /// Returns the supplied length on any other length.
    pub fn from_slice(bytes: &[u8]) -> Result<Self, usize> {
        let array: [u8; STEP_PROOF_LEN] = bytes.try_into().map_err(|_| bytes.len())?;
        Ok(Self(Box::new(array)))
    }

    /// The proof bytes.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.0.as_slice()
    }
}

impl fmt::Debug for CanonicalStepProof {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CanonicalStepProof({} bytes)", STEP_PROOF_LEN)
    }
}

/// One Design-A edge: a public tuple and its proof.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct LineageStepProof {
    /// The public tuple.
    pub public_inputs: LineagePublicInputs,
    /// The R2B-L1 proof for exactly this tuple.
    pub proof: CanonicalStepProof,
}

/// Untrusted bytes of one step, before canonicalization.
#[derive(Debug, Clone, Copy)]
pub struct RawStep<'a> {
    /// Claimed `cmx_old`.
    pub cmx_old: &'a [u8],
    /// Claimed `nf_old`.
    pub nf_old: &'a [u8],
    /// Claimed `cmx_new`.
    pub cmx_new: &'a [u8],
    /// Claimed proof.
    pub proof: &'a [u8],
}

/// Untrusted rooted-lineage evidence, before canonicalization.
#[derive(Debug, Clone)]
pub struct RawLineageEvidence<'a> {
    /// Claimed `chain_ref` encoding, if any.
    pub chain_ref: Option<&'a [u8]>,
    /// Claimed `root_cmx`.
    pub root_cmx: &'a [u8],
    /// Claimed `terminal_cmx`.
    pub terminal_cmx: &'a [u8],
    /// Claimed steps, in order.
    pub steps: Vec<RawStep<'a>>,
}

/// Canonical Design-A evidence `E = (root_cmx, terminal_cmx, [(x_i, π_i)])`
/// plus its Layer-B chain context.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct RootedLineageEvidence {
    root_cmx: CanonicalCmx,
    terminal_cmx: CanonicalCmx,
    steps: Vec<LineageStepProof>,
    chain_ref: Option<ChainRef>,
}

impl RootedLineageEvidence {
    /// Assembles evidence from canonical parts.
    ///
    /// # Errors
    ///
    /// `EMPTY_LINEAGE` for `N = 0`, `TOO_MANY_STEPS` for `N > MAX_STEPS`.
    pub fn new(
        root_cmx: CanonicalCmx,
        terminal_cmx: CanonicalCmx,
        steps: Vec<LineageStepProof>,
        chain_ref: Option<ChainRef>,
    ) -> Result<Self, VerificationFailure> {
        check_step_count(steps.len())?;
        Ok(Self {
            root_cmx,
            terminal_cmx,
            steps,
            chain_ref,
        })
    }

    /// Canonicalizes untrusted evidence. Fails closed on the first problem, in
    /// the order given in the module documentation.
    ///
    /// # Errors
    ///
    /// The first canonicalization failure.
    pub fn parse(raw: &RawLineageEvidence<'_>) -> Result<Self, VerificationFailure> {
        let chain_ref = raw.chain_ref.map(parse_chain_ref).transpose()?;
        let root_cmx = CanonicalCmx::from_slice(raw.root_cmx)
            .map_err(|_| VerificationFailure::non_canonical(FieldRole::RootCmx, None))?;
        check_step_count(raw.steps.len())?;
        let mut steps = Vec::with_capacity(raw.steps.len());
        for (i, s) in raw.steps.iter().enumerate() {
            let field = |role| VerificationFailure::non_canonical(role, Some(i));
            let public_inputs = LineagePublicInputs {
                cmx_old: CanonicalCmx::from_slice(s.cmx_old)
                    .map_err(|_| field(FieldRole::CmxOld))?,
                nf_old: CanonicalNullifier::from_slice(s.nf_old)
                    .map_err(|_| field(FieldRole::NfOld))?,
                cmx_new: CanonicalCmx::from_slice(s.cmx_new)
                    .map_err(|_| field(FieldRole::CmxNew))?,
            };
            let proof = CanonicalStepProof::from_slice(s.proof)
                .map_err(|_| VerificationFailure::at_step(FailureCode::NonCanonicalProof, i))?;
            steps.push(LineageStepProof {
                public_inputs,
                proof,
            });
        }
        let terminal_cmx = CanonicalCmx::from_slice(raw.terminal_cmx)
            .map_err(|_| VerificationFailure::non_canonical(FieldRole::TerminalCmx, None))?;
        Self::new(root_cmx, terminal_cmx, steps, chain_ref)
    }

    /// `root_cmx`.
    #[must_use]
    pub const fn root_cmx(&self) -> &CanonicalCmx {
        &self.root_cmx
    }

    /// `terminal_cmx`.
    #[must_use]
    pub const fn terminal_cmx(&self) -> &CanonicalCmx {
        &self.terminal_cmx
    }

    /// The steps, in order (`1 ≤ N ≤ MAX_STEPS`).
    #[must_use]
    pub fn steps(&self) -> &[LineageStepProof] {
        &self.steps
    }

    /// `N`.
    #[must_use]
    pub fn len(&self) -> usize {
        self.steps.len()
    }

    /// Always `false`: evidence with no steps cannot be constructed.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.steps.is_empty()
    }

    /// The Layer-B chain context, if the evidence carries one.
    #[must_use]
    pub const fn chain_ref(&self) -> Option<&ChainRef> {
        self.chain_ref.as_ref()
    }

    /// Statement-keyed identifier (contract §G3):
    ///
    /// ```text
    /// BLAKE2b-256("ZWA_LineageStmt1",
    ///     chain_ref(117) ‖ root_cmx ‖ terminal_cmx ‖ N(u16 LE) ‖ [cmx_old_i ‖ nf_old_i ‖ cmx_new_i])
    /// ```
    ///
    /// It covers the statement only, never proof bytes (honest proofs are
    /// randomized). `None` without a `chain_ref`: the statement is defined
    /// relative to one chain context.
    #[must_use]
    pub fn lineage_statement_id(&self) -> Option<[u8; 32]> {
        let chain_ref = self.chain_ref.as_ref()?;
        let n = u16::try_from(self.steps.len()).ok()?;
        let mut m = Vec::with_capacity(117 + 64 + 2 + 96 * self.steps.len());
        m.extend_from_slice(&chain_ref.encode());
        m.extend_from_slice(self.root_cmx.as_bytes());
        m.extend_from_slice(self.terminal_cmx.as_bytes());
        m.extend_from_slice(&n.to_le_bytes());
        for s in &self.steps {
            m.extend_from_slice(&s.public_inputs.to_bytes());
        }
        Some(crate::blake2b_256(STATEMENT_ID_PERSONAL, &m))
    }
}

fn check_step_count(n: usize) -> Result<(), VerificationFailure> {
    if n == 0 {
        return Err(VerificationFailure::new(FailureCode::EmptyLineage));
    }
    if n > MAX_STEPS {
        return Err(VerificationFailure::new(FailureCode::TooManySteps));
    }
    Ok(())
}

pub(crate) fn parse_chain_ref(bytes: &[u8]) -> Result<ChainRef, VerificationFailure> {
    ChainRef::decode(bytes).map_err(|e| {
        VerificationFailure::new(match e {
            ChainRefError::Malformed => FailureCode::ChainRefMalformed,
            ChainRefError::UnsupportedVersion => FailureCode::ChainRefUnsupportedVersion,
        })
    })
}
