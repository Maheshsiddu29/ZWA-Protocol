//! Independent inspection of signed transaction bytes before broadcast.
//!
//! The wallet library builds the transaction; this module re-parses the final
//! bytes and checks them against the community network's rules and ZWA's own
//! expectations. It never trusts the builder's summary.

use anyhow::{bail, Context, Result};
use nonempty_shim::non_empty;
use orchard::issuance::compute_asset_desc_hash;
use orchard::keys::{FullViewingKey, PreparedIncomingViewingKey, Scope};
use orchard::note::{AssetBase, AssetId, ExtractedNoteCommitment, TransmittedNoteCiphertext};
use orchard::zsa::OrchardZSADomain;
use serde::Serialize;
use sha2::{Digest, Sha256};
use zcash_note_encryption::try_note_decryption;
use zcash_note_encryption::note_bytes::NoteBytesData;
use zcash_primitives::transaction::{Transaction, TxVersion};
use zcash_protocol::consensus::BranchId;

use crate::policy::BRANCH_ID;

/// ZIP-317 constants as enforced by the community node's mempool
/// (zebra-chain/src/transaction/unmined/zip317.rs at 6a91831).
pub const MARGINAL_FEE: u64 = 5_000;
pub const GRACE_ACTIONS: u64 = 2;
const P2PKH_STANDARD_INPUT_SIZE: usize = 150;
const P2PKH_STANDARD_OUTPUT_SIZE: usize = 34;

/// Facts that are public on chain, safe for receipts.
#[derive(Clone, Debug, Serialize)]
pub struct TxFacts {
    /// Canonical txid as computed by the node's parser (set by
    /// `main::full_facts`); equals `wallet_library_txid` unless the wallet
    /// library normalized Orchard flags.
    pub txid: String,
    pub wallet_library_txid: String,
    pub size: usize,
    pub sha256: String,
    pub version: &'static str,
    pub consensus_branch_id: String,
    pub expiry_height: u32,
    pub fee_zat: u64,
    pub conventional_fee_zat: u64,
    pub logical_actions: u64,
    pub zip233_amount_zat: u64,
    pub transparent_inputs: usize,
    pub transparent_outputs: usize,
    pub sapling_spends: usize,
    pub sapling_outputs: usize,
    pub orchard_actions: usize,
    pub orchard_flavor: &'static str,
    /// True when the wallet library's re-serialization cleared `enableZSA`
    /// on a native-asset Orchard bundle (the only tolerated difference).
    pub orchard_flags_normalized: bool,
    pub issuance: Option<IssuanceFacts>,
}

#[derive(Clone, Debug, Serialize)]
pub struct IssuanceFacts {
    /// Issuer validating key encoding (algorithm byte + x-only key). Public.
    pub ik: String,
    pub asset_desc_hash: String,
    pub asset_base: String,
    pub finalized: bool,
    /// Issuance note values are plaintext on chain under ZIP-227.
    pub issued_amount: u64,
    pub note_count: usize,
}

/// Orchard `flagsOrchard` bit 2 (enableZSA). See `orchard_flags_normalization`.
const ENABLE_ZSA_FLAG: u8 = 0b100;

pub fn parse(bytes: &[u8]) -> Result<Transaction> {
    Ok(parse_with_normalization(bytes)?.0)
}

/// Parse, then require the bytes to round-trip. The wallet library's V6
/// reader holds a native-asset Orchard bundle as a vanilla bundle and drops
/// `enableZSA` when re-writing (observed in the lab: 0x07 -> 0x03), while the
/// node keeps it. Exactly that one-byte difference is tolerated, and only when
/// the byte sits directly before the bundle's anchor. Anything else fails.
/// Byte-exact acceptance by the node's own parser is checked separately by
/// `zwa-node-check`.
pub fn parse_with_normalization(bytes: &[u8]) -> Result<(Transaction, bool)> {
    let tx = Transaction::read(bytes, BranchId::Zsa).context("transaction does not parse under BranchId::Zsa")?;
    let mut round_trip = Vec::with_capacity(bytes.len());
    tx.write(&mut round_trip)?;
    if round_trip == bytes {
        return Ok((tx, false));
    }
    let diffs: Vec<usize> = if round_trip.len() == bytes.len() {
        bytes.iter().zip(&round_trip).enumerate().filter(|(_, (a, b))| a != b).map(|(i, _)| i).collect()
    } else {
        vec![]
    };
    if orchard_flags_normalization(&tx, bytes, &round_trip, &diffs) {
        return Ok((tx, true));
    }
    bail!(
        "transaction bytes do not round-trip (len {} vs {}, differing offsets {:?})",
        bytes.len(),
        round_trip.len(),
        diffs.iter().take(8).collect::<Vec<_>>()
    )
}

fn orchard_flags_normalization(tx: &Transaction, original: &[u8], round_trip: &[u8], diffs: &[usize]) -> bool {
    let [k] = diffs else { return false };
    let k = *k;
    let Some(bundle) = tx.orchard_bundle() else { return false };
    let Some(vanilla) = bundle.as_vanilla() else { return false };
    let anchor = vanilla.anchor().to_bytes();
    original[k] == round_trip[k] | ENABLE_ZSA_FLAG
        && round_trip[k] & ENABLE_ZSA_FLAG == 0
        && original.get(k + 1..k + 33) == Some(&anchor[..])
}

/// Mirrors the node's `conventional_actions` for the shapes this tool builds.
pub fn logical_actions(tx: &Transaction) -> Result<u64> {
    let (t_in, t_out) = match tx.transparent_bundle() {
        Some(b) => {
            let mut size_in = 0usize;
            for input in &b.vin {
                let mut buf = Vec::new();
                input.write(&mut buf)?;
                size_in += buf.len();
            }
            let mut size_out = 0usize;
            for output in &b.vout {
                let mut buf = Vec::new();
                output.write(&mut buf)?;
                size_out += buf.len();
            }
            (
                size_in.div_ceil(P2PKH_STANDARD_INPUT_SIZE),
                size_out.div_ceil(P2PKH_STANDARD_OUTPUT_SIZE),
            )
        }
        None => (0, 0),
    };
    let (s_spends, s_outputs) = tx
        .sapling_bundle()
        .map_or((0, 0), |b| (b.shielded_spends().len(), b.shielded_outputs().len()));
    let orchard = tx.orchard_bundle().map_or(0, |b| b.action_count());
    let issue_notes: usize = tx
        .issue_bundle()
        .map_or(0, |b| b.actions().iter().map(|a| a.notes().len()).sum());
    let logical = t_in.max(t_out) + s_spends.max(s_outputs) + orchard + issue_notes;
    Ok((logical as u64).max(GRACE_ACTIONS))
}

/// Strict inspection for issuance and transfers: shielded ZEC only, fee
/// computed from value balances and checked against the node's minimum.
pub fn facts(bytes: &[u8]) -> Result<TxFacts> {
    facts_inner(bytes, false)
}

/// LAB ONLY: the lab faucet spends mature transparent coinbase, whose input
/// values are not in the transaction, so the fee is left unknown (0).
pub fn facts_lab_funding(bytes: &[u8]) -> Result<TxFacts> {
    facts_inner(bytes, true)
}

fn facts_inner(bytes: &[u8], allow_transparent_inputs: bool) -> Result<TxFacts> {
    let (tx, orchard_flags_normalized) = parse_with_normalization(bytes)?;
    if tx.version() != TxVersion::V6 {
        bail!("expected a V6 transaction");
    }
    if u32::from(tx.consensus_branch_id()) != BRANCH_ID {
        bail!("transaction is not bound to consensus branch 0x77190ad8");
    }
    let transparent_inputs = tx.transparent_bundle().map_or(0, |b| b.vin.len());
    let transparent_outputs = tx.transparent_bundle().map_or(0, |b| b.vout.len());
    let logical = logical_actions(&tx)?;
    let conventional = logical * MARGINAL_FEE;
    let fee_zat = if transparent_inputs != 0 {
        // Fee needs prevout values we deliberately do not fetch; only the lab
        // faucet's coinbase spend takes this path.
        if !allow_transparent_inputs {
            bail!("transparent inputs are outside this tool's transaction plan");
        }
        if transparent_outputs != 0 {
            bail!("a coinbase spend must not have transparent outputs");
        }
        0
    } else {
        let fee = tx
            .fee_paid(|_| Ok::<_, zcash_protocol::value::BalanceError>(None))
            .map_err(|e| anyhow::anyhow!("fee: {e:?}"))?
            .context("fee could not be computed")?;
        let fee_zat = u64::from(fee);
        if fee_zat < conventional {
            bail!("fee {fee_zat} is below the node's conventional fee {conventional}; it would not relay");
        }
        // A generous ceiling against a builder overpaying from a faucet balance.
        if fee_zat > conventional + 10 * MARGINAL_FEE {
            bail!("fee {fee_zat} exceeds the planned ceiling");
        }
        fee_zat
    };
    let (sapling_spends, sapling_outputs) = tx
        .sapling_bundle()
        .map_or((0, 0), |b| (b.shielded_spends().len(), b.shielded_outputs().len()));
    // This wallet fork's V6 reader stores the original 612-byte ZSA
    // ciphertexts separately and represents the actions as vanilla Orchard.
    // The node parser independently validates the original bytes in facts_for.
    let orchard_flavor = match tx.orchard_bundle() {
        None => "none",
        Some(b) if b.as_zsa().is_some() => "orchard-zsa",
        Some(b) if !tx.zsa_action_enc_ciphertexts.is_empty()
            && tx.zsa_action_enc_ciphertexts.len() == b.action_count()
            && tx.zsa_action_enc_ciphertexts.iter().all(|c| c.len() == 612) => "orchard-zsa",
        Some(_) => "orchard-vanilla",
    };
    let issuance = match tx.issue_bundle() {
        None => None,
        Some(bundle) => {
            if bundle.actions().len() != 1 {
                bail!("exactly one issue action is expected");
            }
            let action = bundle.actions().first();
            let ik = bundle.ik();
            let desc_hash = *action.asset_desc_hash();
            let base = AssetBase::custom(&AssetId::new_v0(ik, &desc_hash));
            let mut issued = 0u64;
            for note in action.notes() {
                if note.asset() != base {
                    bail!("an issuance note carries a different AssetBase than ik+desc_hash derive");
                }
                issued = issued.checked_add(note.value().inner()).context("issued overflow")?;
            }
            Some(IssuanceFacts {
                ik: hex::encode(ik.encode()),
                asset_desc_hash: hex::encode(desc_hash),
                asset_base: hex::encode(base.to_bytes()),
                finalized: action.is_finalized(),
                issued_amount: issued,
                note_count: action.notes().len(),
            })
        }
    };
    Ok(TxFacts {
        txid: tx.txid().to_string(),
        wallet_library_txid: tx.txid().to_string(),
        size: bytes.len(),
        sha256: hex::encode(Sha256::digest(bytes)),
        version: "V6",
        consensus_branch_id: format!("{:08x}", u32::from(tx.consensus_branch_id())),
        expiry_height: u32::from(tx.expiry_height()),
        fee_zat,
        conventional_fee_zat: conventional,
        logical_actions: logical,
        zip233_amount_zat: u64::from(tx.zip233_amount()),
        transparent_inputs,
        transparent_outputs,
        sapling_spends,
        sapling_outputs,
        orchard_actions: tx.orchard_bundle().map_or(0, |b| b.action_count()),
        orchard_flavor,
        orchard_flags_normalized,
        issuance,
    })
}

/// Check a planned issuance against the name, amount and finality ZWA asked for.
pub fn expect_issuance(facts: &TxFacts, name: &str, amount: u64) -> Result<()> {
    if facts.transparent_inputs != 0 || facts.transparent_outputs != 0 || facts.sapling_outputs != 0 {
        bail!("issuance must not create transparent or Sapling outputs");
    }
    let issuance = facts.issuance.as_ref().context("transaction has no issue bundle")?;
    let desc_hash = compute_asset_desc_hash(&non_empty(name.as_bytes())?);
    if issuance.asset_desc_hash != hex::encode(desc_hash) {
        bail!("asset description hash does not match the requested name");
    }
    if issuance.issued_amount != amount {
        bail!("issued amount {} differs from requested {amount}", issuance.issued_amount);
    }
    if !issuance.finalized {
        bail!("issuance must be finalized (community wallet rule)");
    }
    if facts.zip233_amount_zat != 0 {
        bail!("no ZIP-233 amount is planned");
    }
    Ok(())
}

/// Check a planned transfer: ZSA Orchard bundle only, no issuance, and an
/// output the recipient's own viewing key decrypts to exactly the requested
/// asset and amount.
pub fn expect_transfer(
    bytes: &[u8],
    facts: &TxFacts,
    recipient_fvk: &FullViewingKey,
    asset_base: &[u8; 32],
    amount: u64,
) -> Result<()> {
    if facts.issuance.is_some() {
        bail!("a transfer must not carry an issue bundle");
    }
    if facts.orchard_flavor != "orchard-zsa" {
        bail!("a ZSA transfer needs an OrchardZSA bundle");
    }
    if facts.transparent_outputs != 0 || facts.sapling_outputs != 0 || facts.zip233_amount_zat != 0 {
        bail!("transfer leaks value outside the Orchard pool");
    }
    let tx = parse(bytes)?;
    let bundle = tx.orchard_bundle().context("missing Orchard bundle")?;
    let vanilla = bundle.as_vanilla().context("wallet reader did not expose ZSA actions")?;
    if tx.zsa_action_enc_ciphertexts.len() != vanilla.actions().len() {
        bail!("ZSA ciphertext count differs from Orchard action count");
    }
    let ivk = PreparedIncomingViewingKey::new(&recipient_fvk.to_ivk(Scope::External));
    let mut matched = 0u64;
    for (action, raw) in vanilla.actions().iter().zip(&tx.zsa_action_enc_ciphertexts) {
        let raw: [u8; 612] = raw.as_slice().try_into().context("ZSA ciphertext length")?;
        let ciphertext = TransmittedNoteCiphertext::<OrchardZSADomain> {
            epk_bytes: action.encrypted_note().epk_bytes,
            enc_ciphertext: NoteBytesData(raw),
            out_ciphertext: action.encrypted_note().out_ciphertext,
        };
        let zsa_action = orchard::Action::from_parts(
            *action.nullifier(),
            action.rk().clone(),
            Option::from(ExtractedNoteCommitment::from_bytes(&action.cmx().to_bytes())).context("commitment")?,
            ciphertext,
            action.cv_net().clone(),
            (),
        ).context("ZSA action reconstruction")?;
        let domain = OrchardZSADomain::for_action(&zsa_action);
        if let Some((note, _, _)) = try_note_decryption(&domain, &ivk, &zsa_action) {
            if note.asset().to_bytes() == *asset_base {
                matched = matched.checked_add(note.value().inner()).context("overflow")?;
            }
        }
    }
    if matched != amount {
        bail!("recipient decrypts {matched} units of the asset, expected {amount}");
    }
    Ok(())
}

mod nonempty_shim {
    use anyhow::{Context, Result};

    pub fn non_empty(bytes: &[u8]) -> Result<nonempty::NonEmpty<u8>> {
        nonempty::NonEmpty::from_slice(bytes).context("asset description must be non-empty")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn garbage_bytes_are_rejected() {
        assert!(facts(&[]).is_err());
        assert!(facts(&[0u8; 64]).is_err());
    }

    #[test]
    fn desc_hash_follows_zip227_for_the_name() {
        let a = compute_asset_desc_hash(&non_empty(b"ZWA-TESTNET-A").unwrap());
        let b = compute_asset_desc_hash(&non_empty(b"ZWA-TESTNET-B").unwrap());
        assert_ne!(a, b);
        assert!(non_empty(b"").is_err());
    }
}
