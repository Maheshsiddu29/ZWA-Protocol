//! The verifier's own chain view (`CHAINREF_SPEC.md` §6).
//!
//! A snapshot is captured from the verifier's pinned node over loopback RPC.
//! Loading re-derives everything Layer B relies on from the block bytes:
//! header hashes, `prev_block` linkage, the canonical genesis, the transaction
//! Merkle root of every block, and the Orchard tree (leaves in Zebra's order,
//! C4) with its root after every block, which must equal the node's
//! `finalRoot`. Any disagreement rejects the whole view.

use bridgetree::BridgeTree;
use orchard::note::ExtractedNoteCommitment;
use orchard::tree::MerkleHashOrchard;
use orchard::Anchor;
use serde_json::Value;
use zcash_primitives::transaction::{OrchardBundle, Transaction};

use crate::chainref::{NetworkKind, NetworkParams};
use crate::util::{from_display, h, parse_block, sha256_hex, to_display, H32};

pub const SNAPSHOT_FORMAT: &str = "zwa-layer-b-view-v1";
const DEPTH: u8 = 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LeafKind {
    /// Orchard(-ZSA) Action output `action_index` of the transaction.
    Action { action_index: u32 },
    /// Issue note `(action_index, note_index)` of the transaction's issue bundle.
    IssueNote { action_index: u32, note_index: u32 },
}

#[derive(Clone, Debug)]
pub struct Leaf {
    pub position: u64,
    pub height: u32,
    pub tx_index: usize,
    /// Internal byte order.
    pub txid: H32,
    pub kind: LeafKind,
    pub cmx: H32,
}

pub struct ChainView {
    pub label: String,
    pub snapshot_sha256: String,
    pub params: NetworkParams,
    pub network_id: H32,
    pub tip: u32,
    /// Internal byte order, index = height.
    pub hashes: Vec<H32>,
    /// Orchard root after each height; `None` where the view has no Orchard state.
    pub roots: Vec<Option<H32>>,
    /// Number of Orchard leaves after each height.
    pub sizes: Vec<u64>,
    pub blocks: Vec<Vec<Transaction>>,
    pub leaves: Vec<Leaf>,
    /// Highest height the node committed through a Zebra checkpoint (0 = genesis only).
    pub node_checkpoint_max: u32,
}

/// Leaves a transaction appends, in Zebra's order (C4): Action `cmx`, then
/// issue-note commitments.
pub fn tx_leaves(tx: &Transaction) -> Vec<(LeafKind, H32)> {
    let mut out = Vec::new();
    match tx.orchard_bundle() {
        Some(OrchardBundle::OrchardZSA(b)) => {
            for (i, a) in b.actions().iter().enumerate() {
                out.push((LeafKind::Action { action_index: i as u32 }, a.cmx().to_bytes()));
            }
        }
        Some(OrchardBundle::OrchardVanilla(b)) => {
            for (i, a) in b.actions().iter().enumerate() {
                out.push((LeafKind::Action { action_index: i as u32 }, a.cmx().to_bytes()));
            }
        }
        None => {}
    }
    if let Some(issue) = tx.issue_bundle() {
        for (ai, action) in issue.actions().iter().enumerate() {
            for (ni, note) in action.notes().iter().enumerate() {
                let cmx = ExtractedNoteCommitment::from(note.commitment()).to_bytes();
                out.push((LeafKind::IssueNote { action_index: ai as u32, note_index: ni as u32 }, cmx));
            }
        }
    }
    out
}

pub fn new_tree() -> BridgeTree<MerkleHashOrchard, u32, DEPTH> {
    BridgeTree::new(16)
}

/// Root of the tree with exactly `leaves`, with the empty root for none.
pub fn root_of(leaves: &[H32]) -> H32 {
    if leaves.is_empty() {
        return Anchor::empty_tree().to_bytes();
    }
    let mut tree = new_tree();
    for l in leaves {
        let cmx = ExtractedNoteCommitment::from_bytes(l).expect("leaf is canonical");
        assert!(tree.append(MerkleHashOrchard::from_cmx(&cmx)));
    }
    tree.checkpoint(0);
    tree.root(0).expect("root").to_bytes()
}

impl ChainView {
    /// Validates a snapshot and builds the view. `kind`, `action_vk_id` and
    /// `ruleset_tag` come from the verifier's configuration.
    pub fn load(bytes: &[u8], kind: NetworkKind, action_vk_id: H32, ruleset_tag: &str) -> Result<ChainView, String> {
        let v: Value = serde_json::from_slice(bytes).map_err(|e| format!("json: {e}"))?;
        if v["format"] != SNAPSHOT_FORMAT {
            return Err("unknown snapshot format".into());
        }
        let kind_s = v["network_kind"].as_str().ok_or("network_kind")?;
        if NetworkKind::parse(kind_s) != Some(kind) {
            return Err(format!("snapshot network kind {kind_s} differs from verifier configuration"));
        }
        let tip = v["tip"].as_u64().ok_or("tip")? as u32;
        let blocks_v = v["blocks"].as_array().ok_or("blocks")?;
        if blocks_v.len() != tip as usize + 1 {
            return Err("blocks are not exactly heights 0..=tip".into());
        }
        let mut hashes = vec![];
        let mut roots = vec![];
        let mut sizes = vec![];
        let mut blocks = vec![];
        let mut leaves: Vec<Leaf> = vec![];
        let mut tree = new_tree();
        for (i, b) in blocks_v.iter().enumerate() {
            if b["height"].as_u64() != Some(i as u64) {
                return Err(format!("height {i} out of order"));
            }
            let node_hash = from_display(b["hash"].as_str().ok_or("hash")?);
            let raw = hex::decode(b["hex"].as_str().ok_or("hex")?.trim()).map_err(|_| "block hex")?;
            let (hash, txs) = if i == 0 {
                // Genesis: header hash only (its pre-Overwinter coinbase carries no Orchard data).
                let mut cursor = std::io::Cursor::new(&raw);
                let header = zcash_primitives::block::BlockHeader::read(&mut cursor).map_err(|e| format!("genesis header: {e}"))?;
                (header.hash().0, vec![])
            } else {
                let p = parse_block(&raw).map_err(|e| format!("height {i}: {e}"))?;
                if p.header.prev_block.0 != hashes[i - 1] {
                    return Err(format!("height {i}: prev_block does not link to height {}", i - 1));
                }
                // Binds every transaction's effecting data (txid) to the header,
                // hence to the block hash a chain_ref names. Authorizing data
                // (proofs, signatures) is committed only through
                // hashBlockCommitments (ZIP-221 history root), not re-derived here.
                if crate::util::merkle_root(p.txs.iter().map(|t| *t.txid().as_ref())) != p.header.merkle_root {
                    return Err(format!("height {i}: transaction Merkle root differs from the header"));
                }
                (p.hash, p.txs)
            };
            if hash != node_hash {
                return Err(format!("height {i}: recomputed header hash {} differs from node hash", to_display(&hash)));
            }
            if i == 0 && to_display(&hash) != kind.genesis_display() {
                return Err("height-0 hash is not the canonical genesis for the configured network".into());
            }
            for (ti, tx) in txs.iter().enumerate() {
                let txid = *tx.txid().as_ref();
                for (lk, cmx) in tx_leaves(tx) {
                    let leaf = ExtractedNoteCommitment::from_bytes(&cmx);
                    if bool::from(leaf.is_none()) {
                        return Err(format!("height {i}: non-canonical leaf"));
                    }
                    tree.append(MerkleHashOrchard::from_cmx(&leaf.unwrap()));
                    leaves.push(Leaf { position: leaves.len() as u64, height: i as u32, tx_index: ti, txid, kind: lk, cmx });
                }
            }
            tree.checkpoint(i as u32);
            let recomputed = if leaves.is_empty() { None } else { Some(tree.root(0).expect("root").to_bytes()) };
            let node_root = b["orchard_final_root"].as_str().map(crate::util::unhex32);
            let root = match (node_root, recomputed) {
                (Some(n), Some(r)) if n == r => Some(n),
                (Some(n), None) if n == Anchor::empty_tree().to_bytes() => Some(n),
                (None, None) => None,
                (n, r) => {
                    return Err(format!(
                        "height {i}: Orchard root mismatch node {:?} recomputed {:?}",
                        n.map(|x| h(&x)),
                        r.map(|x| h(&x))
                    ))
                }
            };
            hashes.push(hash);
            roots.push(root);
            sizes.push(leaves.len() as u64);
            blocks.push(txs);
        }
        let best = from_display(v["best_hash"].as_str().ok_or("best_hash")?);
        if best != hashes[tip as usize] {
            return Err("best hash is not the block at tip height".into());
        }
        let mut upgrades = vec![];
        for u in v["upgrades"].as_array().ok_or("upgrades")? {
            let id = u32::from_str_radix(u["branch_id"].as_str().ok_or("branch_id")?, 16).map_err(|_| "branch_id")?;
            let height = u["height"].as_u64().ok_or("upgrade height")? as u32;
            upgrades.push((id, height));
        }
        let mut node_checkpoint_max = 0;
        for c in v["node_checkpoints"].as_array().ok_or("node_checkpoints")? {
            let hh = c[0].as_u64().ok_or("checkpoint height")? as u32;
            let hs = from_display(c[1].as_str().ok_or("checkpoint hash")?);
            if hh <= tip && hashes[hh as usize] != hs {
                return Err(format!("node checkpoint at {hh} contradicts the node's chain"));
            }
            node_checkpoint_max = node_checkpoint_max.max(hh);
        }
        let params = NetworkParams {
            kind,
            genesis_hash: hashes[0],
            upgrades,
            action_vk_id,
            ruleset_tag: ruleset_tag.to_string(),
        };
        Ok(ChainView {
            label: v["label"].as_str().unwrap_or("").to_string(),
            snapshot_sha256: sha256_hex(bytes),
            network_id: params.network_id(),
            params,
            tip,
            hashes,
            roots,
            sizes,
            blocks,
            leaves,
            node_checkpoint_max,
        })
    }

    pub fn leaf(&self, position: u64) -> Option<&Leaf> {
        self.leaves.get(position as usize)
    }

    pub fn tx(&self, height: u32, tx_index: usize) -> &Transaction {
        &self.blocks[height as usize][tx_index]
    }

}
