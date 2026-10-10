//! `AuthenticateTerminal` (`TERMINAL_MAF_SPEC.md` §3).

use orchard::circuit::VerifyingKey;
use orchard::note::ExtractedNoteCommitment;
use zcash_primitives::transaction::OrchardBundle;

use crate::chainref::ChainRef;
use crate::policy::Code;
use crate::root::path_from;
use crate::util::H32;
use crate::view::{ChainView, LeafKind};

pub const TERMINAL_EVIDENCE_LEN: usize = 4 + 32 * 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TerminalEvidence {
    pub position: u32,
    pub auth_path: [H32; 32],
}

impl TerminalEvidence {
    pub fn decode(b: &[u8]) -> Option<Self> {
        if b.len() != TERMINAL_EVIDENCE_LEN {
            return None;
        }
        let mut auth_path = [[0u8; 32]; 32];
        for (i, s) in auth_path.iter_mut().enumerate() {
            *s = b[4 + i * 32..4 + (i + 1) * 32].try_into().unwrap();
        }
        Some(TerminalEvidence { position: u32::from_le_bytes(b[..4].try_into().unwrap()), auth_path })
    }
}

/// Metadata of an authenticated terminal. txid and Action index are derived
/// by the verifier; they are not part of the public lineage statement.
#[derive(Clone, Debug)]
pub struct TermInfo {
    pub height: u32,
    pub position: u64,
    pub txid: H32,
    pub action_index: u32,
}

/// `AuthenticateTerminal`. `r` must already have passed `validate_identity`.
/// `vk` is `VerifyingKey::build::<OrchardZSA>()` (Zebra's `VERIFYING_KEY_ZSA`).
pub fn authenticate_terminal(term_cmx: &H32, ev_bytes: &[u8], r: &ChainRef, v: &ChainView, vk: &VerifyingKey) -> Result<TermInfo, Code> {
    // T1
    let ev = TerminalEvidence::decode(ev_bytes).ok_or(Code::TermMalformed)?;
    let cmx: ExtractedNoteCommitment = Option::from(ExtractedNoteCommitment::from_bytes(term_cmx)).ok_or(Code::TermMalformed)?;
    let path = path_from(ev.position, &ev.auth_path).ok_or(Code::TermMalformed)?;
    // T2
    if ev.position as u64 >= v.sizes[r.height as usize] {
        return Err(Code::TermOutOfRange);
    }
    let leaf = v.leaf(ev.position as u64).ok_or(Code::TermOutOfRange)?;
    // T3
    let action_index = match leaf.kind {
        LeafKind::Action { action_index } => action_index,
        LeafKind::IssueNote { .. } => return Err(Code::TermNotAction),
    };
    let tx = v.tx(leaf.height, leaf.tx_index);
    let bundle = match tx.orchard_bundle() {
        Some(OrchardBundle::OrchardZSA(b)) => b,
        _ => return Err(Code::TermNotZsaAction),
    };
    // T4
    if &leaf.cmx != term_cmx || bundle.actions().get(action_index as usize).map(|a| a.cmx().to_bytes()) != Some(*term_cmx) {
        return Err(Code::TermCmx);
    }
    // T5
    if path.root(cmx).to_bytes() != r.orchard_root {
        return Err(Code::TermMerkle);
    }
    // T6
    bundle.verify_proof(vk).map_err(|_| Code::TermActionProof)?;
    // T7
    let anchor = bundle.anchor().to_bytes();
    if !(0..leaf.height).any(|h| v.roots[h as usize] == Some(anchor)) {
        return Err(Code::TermAnchor);
    }
    Ok(TermInfo { height: leaf.height, position: leaf.position, txid: leaf.txid, action_index })
}
