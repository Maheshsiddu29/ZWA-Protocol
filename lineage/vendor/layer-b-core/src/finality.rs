//! `ValidateChainRef` (`CHAINREF_SPEC.md` §4) and the finality / reorg state
//! machine (`FINALITY_REORG_MODEL.md` §3).

use std::collections::HashMap;

use crate::chainref::{ChainRef, ChainRefDecodeError};
use crate::policy::{Code, FinalityMode, VerifierConfig};
use crate::view::ChainView;
use crate::util::H32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RefState {
    Unknown,
    Accepted,
    Finalized,
    Invalidated,
    FinalityViolation,
}

/// References the verifier has seen ACCEPTED or FINALIZED in earlier views.
#[derive(Default, Clone)]
pub struct Tracker {
    seen: HashMap<(H32, u32, H32), RefState>,
}

impl Tracker {
    fn key(r: &ChainRef) -> (H32, u32, H32) {
        (r.network_id, r.height, r.block_hash)
    }
    pub fn record(&mut self, r: &ChainRef, s: RefState) {
        let e = self.seen.entry(Self::key(r)).or_insert(s);
        if s == RefState::Finalized {
            *e = RefState::Finalized;
        }
    }
    pub fn previous(&self, r: &ChainRef) -> Option<RefState> {
        self.seen.get(&Self::key(r)).copied()
    }
}

/// CR-1 … CR-6: the reference names a block on the verifier's selected chain
/// of the verifier's network, with that block's Orchard root.
pub fn validate_identity(r_bytes: &[u8], view: Option<&ChainView>, cfg: &VerifierConfig, tracker: &Tracker) -> Result<ChainRef, Code> {
    let r = ChainRef::decode(r_bytes).map_err(|e| match e {
        ChainRefDecodeError::Malformed => Code::Malformed,
        ChainRefDecodeError::UnsupportedVersion => Code::UnsupportedVersion,
    })?;
    let v = view.ok_or(Code::ViewUnavailable)?;
    if r.network_id != v.network_id || !cfg.allowed_networks.contains(&r.network_id) {
        return Err(Code::Network);
    }
    if r.height > v.tip {
        return Err(Code::UnknownFuture);
    }
    if v.hashes[r.height as usize] != r.block_hash {
        return Err(match tracker.previous(&r) {
            Some(RefState::Finalized) => Code::FinalityViolation,
            Some(RefState::Accepted) => Code::Invalidated,
            _ => Code::Unknown,
        });
    }
    match v.roots[r.height as usize] {
        None => Err(Code::Unknown),
        Some(root) if root != r.orchard_root => Err(Code::TreestateMismatch),
        Some(_) => Ok(r),
    }
}

/// Finality state of a reference already on the view's selected chain.
pub fn classify(r: &ChainRef, v: &ChainView, cfg: &VerifierConfig) -> Result<RefState, Code> {
    // A trusted checkpoint the view contradicts rejects everything.
    for (h, hash) in &cfg.trusted_checkpoints {
        if *h <= v.tip && v.hashes[*h as usize] != *hash {
            return Err(Code::CheckpointConflict);
        }
    }
    let depth_ok = |k: u32| v.tip - r.height >= k;
    let cp_ok = || cfg.trusted_checkpoints.iter().any(|(h, hash)| r.height <= *h && *h <= v.tip && v.hashes[*h as usize] == *hash);
    let fin = match cfg.finality {
        FinalityMode::Depth(k) => depth_ok(k),
        FinalityMode::Checkpoint => cp_ok(),
        FinalityMode::Hybrid(k) => depth_ok(k) || cp_ok(),
    };
    Ok(if fin { RefState::Finalized } else { RefState::Accepted })
}

/// CR-7 … CR-9: finality, freshness and checkpointed-history policy. Records
/// the reference in the tracker.
pub fn validate_policy(r: &ChainRef, v: &ChainView, cfg: &VerifierConfig, tracker: &mut Tracker) -> Result<RefState, Code> {
    let state = classify(r, v, cfg)?;
    tracker.record(r, state);
    if state != RefState::Finalized {
        return Err(Code::NotFinal);
    }
    if let Some(max) = cfg.max_age {
        if v.tip - r.height > max {
            return Err(Code::Expired);
        }
    }
    if v.node_checkpoint_max >= 1 && !cfg.accept_checkpointed_history {
        return Err(Code::CheckpointedHistory);
    }
    Ok(state)
}

/// Both halves, for a stand-alone reference.
pub fn validate_chain_ref(r_bytes: &[u8], view: Option<&ChainView>, cfg: &VerifierConfig, tracker: &mut Tracker) -> Result<(ChainRef, RefState), Code> {
    let r = validate_identity(r_bytes, view, cfg, tracker)?;
    let s = validate_policy(&r, view.unwrap(), cfg, tracker)?;
    Ok((r, s))
}
