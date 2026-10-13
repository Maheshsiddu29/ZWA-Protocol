//! `AuthorizeRoot` (`ROOT_AUTH_SPEC.md` §3–§4).

use std::collections::BTreeMap;

use orchard::issuance::auth::{IssueValidatingKey, ZSASchnorr};
use orchard::issuance::{check_issue_bundle_without_sighash, verify_issue_bundle, AssetRecord, ReferenceKeys};
use orchard::note::{AssetBase, AssetId, ExtractedNoteCommitment, Nullifier};
use orchard::tree::{MerkleHashOrchard, MerklePath};
use orchard::value::NoteValue;
use zcash_primitives::transaction::{OrchardBundle, Transaction};

use crate::chainref::ChainRef;
use crate::policy::{Code, ProvenancePolicy, VerifierConfig, STATUS_ACTIVE};
use crate::util::{shielded_sighash, H32};
use crate::view::{ChainView, LeafKind};

pub const ROOT_EVIDENCE_LEN: usize = 32 + 33 + 32 + 4 + 32 + 4 + 4 + 4 + 32 * 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RootEvidence {
    pub asset_base: H32,
    pub issuer_ik: [u8; 33],
    pub asset_desc_hash: H32,
    pub height: u32,
    pub txid: H32,
    pub action_index: u32,
    pub note_index: u32,
    pub position: u32,
    pub auth_path: [H32; 32],
}

impl RootEvidence {
    pub fn decode(b: &[u8]) -> Option<Self> {
        if b.len() != ROOT_EVIDENCE_LEN {
            return None;
        }
        let u32_at = |o: usize| u32::from_le_bytes(b[o..o + 4].try_into().unwrap());
        let mut auth_path = [[0u8; 32]; 32];
        for (i, s) in auth_path.iter_mut().enumerate() {
            *s = b[145 + i * 32..145 + (i + 1) * 32].try_into().unwrap();
        }
        Some(RootEvidence {
            asset_base: b[0..32].try_into().unwrap(),
            issuer_ik: b[32..65].try_into().unwrap(),
            asset_desc_hash: b[65..97].try_into().unwrap(),
            height: u32_at(97),
            txid: b[101..133].try_into().unwrap(),
            action_index: u32_at(133),
            note_index: u32_at(137),
            position: u32_at(141),
            auth_path,
        })
    }
}

/// What an accepted root establishes (metadata for the result).
#[derive(Clone, Debug)]
pub struct RootInfo {
    pub height: u32,
    pub position: u64,
    pub asset_base: H32,
    pub issuer_ik: [u8; 33],
    pub value: u64,
}

pub fn first_nullifier(tx: &Transaction) -> Option<Nullifier> {
    match tx.orchard_bundle()? {
        OrchardBundle::OrchardZSA(b) => Some(*b.actions().first().nullifier()),
        OrchardBundle::OrchardVanilla(b) => Some(*b.actions().first().nullifier()),
    }
}

/// Merkle path from evidence bytes; `None` if any sibling is non-canonical.
pub fn path_from(position: u32, auth: &[H32; 32]) -> Option<MerklePath> {
    let mut nodes = [MerkleHashOrchard::from_bytes(&[0u8; 32]).unwrap(); 32];
    for (i, s) in auth.iter().enumerate() {
        nodes[i] = Option::from(MerkleHashOrchard::from_bytes(s))?;
    }
    Some(MerklePath::from_parts(position, nodes))
}

/// Asset state (ZIP-227 records) from every issue bundle in the view that
/// precedes `(height, tx_index)` in chain order.
fn records_before(v: &ChainView, height: u32, tx_index: usize) -> Option<BTreeMap<AssetBase, AssetRecord>> {
    let mut records: BTreeMap<AssetBase, AssetRecord> = BTreeMap::new();
    for h in 1..=height {
        for (ti, tx) in v.blocks[h as usize].iter().enumerate() {
            if h == height && ti >= tx_index {
                return Some(records);
            }
            if let Some(b) = tx.issue_bundle() {
                let nf = first_nullifier(tx)?;
                let new = check_issue_bundle_without_sighash(b, |a| records.get(a).cloned(), &nf).ok()?;
                records.extend(new);
            }
        }
    }
    Some(records)
}

/// R8: provenance policy.
fn policy_check(height: u32, ik: &[u8; 33], adh: &H32, asset: &H32, r: &ChainRef, cfg: &VerifierConfig) -> Result<(), Code> {
    let bytes = cfg.policy_bytes.as_ref().ok_or(Code::RootPolicyUntrusted)?;
    let p = ProvenancePolicy::decode(bytes).ok_or(Code::RootPolicyUntrusted)?;
    let d = p.digest();
    if cfg.revoked_policy_digests.contains(&d) {
        return Err(Code::RootPolicyRevoked);
    }
    if !cfg.accepted_policy_digests.contains(&d) {
        return Err(Code::RootPolicyUntrusted);
    }
    if p.network_id != r.network_id {
        return Err(Code::RootPolicyNetwork);
    }
    let by_issuer: Vec<_> = p.entries.iter().filter(|e| &e.issuer_ik == ik).collect();
    if by_issuer.is_empty() {
        return Err(Code::RootPolicyIssuer);
    }
    let e = by_issuer
        .into_iter()
        .find(|e| &e.asset_desc_hash == adh && &e.asset_base == asset)
        .ok_or(Code::RootPolicyAsset)?;
    if e.status != STATUS_ACTIVE {
        return Err(Code::RootPolicyEntryRevoked);
    }
    if height < e.valid_from || height > e.valid_until {
        return Err(Code::RootPolicyWindow);
    }
    Ok(())
}

/// `AuthorizeRoot`. `r` must already have passed `validate_identity`.
pub fn authorize_root(root_cmx: &H32, ev_bytes: &[u8], r: &ChainRef, v: &ChainView, cfg: &VerifierConfig) -> Result<RootInfo, Code> {
    // R1
    let ev = RootEvidence::decode(ev_bytes).ok_or(Code::RootMalformed)?;
    let cmx: ExtractedNoteCommitment = Option::from(ExtractedNoteCommitment::from_bytes(root_cmx)).ok_or(Code::RootMalformed)?;
    let claimed_asset: AssetBase = Option::from(AssetBase::from_bytes(&ev.asset_base)).ok_or(Code::RootMalformed)?;
    if bool::from(claimed_asset.is_zatoshi()) {
        return Err(Code::RootMalformed);
    }
    let ik = IssueValidatingKey::<ZSASchnorr>::decode(&ev.issuer_ik).map_err(|_| Code::RootMalformed)?;
    let path = path_from(ev.position, &ev.auth_path).ok_or(Code::RootMalformed)?;
    // R2
    if ev.height == 0 || ev.height > r.height || ev.position as u64 >= v.sizes[r.height as usize] {
        return Err(Code::RootOutOfRange);
    }
    // R3
    let leaf = v.leaf(ev.position as u64).ok_or(Code::RootOutOfRange)?;
    let (ai, ni) = match leaf.kind {
        LeafKind::IssueNote { action_index, note_index } => (action_index, note_index),
        LeafKind::Action { .. } => return Err(Code::RootNotIssuance),
    };
    if leaf.height != ev.height || leaf.txid != ev.txid || ai != ev.action_index || ni != ev.note_index {
        return Err(Code::RootContext);
    }
    // R4
    let tx = v.tx(leaf.height, leaf.tx_index);
    let bundle = tx.issue_bundle().ok_or(Code::RootContext)?;
    let action = bundle.actions().get(ai as usize).ok_or(Code::RootContext)?;
    let note = action.notes().get(ni as usize).ok_or(Code::RootContext)?;
    if &leaf.cmx != root_cmx || ExtractedNoteCommitment::from(note.commitment()).to_bytes() != *root_cmx {
        return Err(Code::RootCmx);
    }
    // R5
    if path.root(cmx).to_bytes() != r.orchard_root {
        return Err(Code::RootMerkle);
    }
    // R6
    if bundle.ik().encode() != ev.issuer_ik.to_vec()
        || action.asset_desc_hash() != &ev.asset_desc_hash
        || note.asset() != claimed_asset
        || AssetBase::custom(&AssetId::new_v0(&ik, &ev.asset_desc_hash)) != claimed_asset
    {
        return Err(Code::RootAssetMismatch);
    }
    let nf = first_nullifier(tx).ok_or(Code::RootIssuanceInvalid)?;
    let records = records_before(v, leaf.height, leaf.tx_index).ok_or(Code::RootIssuanceInvalid)?;
    verify_issue_bundle(bundle, shielded_sighash(tx), |a| records.get(a).cloned(), &nf).map_err(|_| Code::RootIssuanceInvalid)?;
    // R7
    if note.value() == NoteValue::ZERO || note.recipient() == ReferenceKeys::recipient() {
        return Err(Code::RootShape);
    }
    // R8
    policy_check(ev.height, &ev.issuer_ik, &ev.asset_desc_hash, &ev.asset_base, r, cfg)?;
    Ok(RootInfo {
        height: ev.height,
        position: ev.position as u64,
        asset_base: ev.asset_base,
        issuer_ik: ev.issuer_ik,
        value: u64::from_le_bytes(note.value().to_bytes()),
    })
}
