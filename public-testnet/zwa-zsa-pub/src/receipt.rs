//! Privacy-safe public receipts and private wallet-observation records.
//!
//! The public receipt holds only what the chain itself makes public: txid,
//! block, fee, asset identity and (for issuance, per ZIP-227) the plaintext
//! issued amount. Shielded transfer amounts, addresses, keys and wallet
//! balances stay in the private record inside the owner-only root.

use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::chain::Inclusion;
use crate::inspect::TxFacts;
use crate::policy::{self, ObservedIdentity, Profile, ProfileKind};
use crate::wallet::{self, write_private, Role};
use crate::{Plan, PlanKind};

pub const TRUST_ASSUMPTIONS: &[&str] = &[
    "Inclusion is reported by one independently operated lightwalletd (zsa.methyl.cc) in front of a Zebra 5.0.0 node; ZWA does not operate it.",
    "The community ZSA Testnet is experimental: a single operator runs the miner, a reset is possible, and it carries no value.",
    "The network's consensus code is zcash-shielded-assets/zebra@6a91831 as stated by its operator; the node does not report its own commit.",
    "Confirmations are counted from the tip observed at verification time and are not finality.",
];

pub const EXCLUDED_CLAIMS: &[&str] = &[
    "Not an atomic swap or ZIP-228 settlement; the community network accepts only one Action Group per transaction.",
    "Not gated by ZWA's Groth16 eligibility authorization.",
    "Not Zcash mainnet; experimental ZSA consensus.",
    "A public explorer cannot see shielded transfer amounts or recipients.",
];

fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Private wallet-side confirmation of the expected effect.
pub async fn wallet_observation(root: &Path, profile: &Profile, plan: &Plan, mined: bool) -> Result<Value> {
    if !mined {
        return Ok(json!({ "status": "PENDING_INCLUSION" }));
    }
    let base = plan
        .asset_base
        .as_deref()
        .map(hex::decode)
        .transpose()?
        .map(|b| <[u8; 32]>::try_from(b).map_err(|_| anyhow::anyhow!("asset base length")))
        .transpose()?;
    match plan.kind {
        PlanKind::ZsaIssuance => {
            let issuer = wallet::open(root, profile, Role::Issuer).await?;
            issuer.sync().await?;
            let held = issuer.zsa_balance(&base.context("asset")?).await?;
            Ok(json!({
                "status": if held >= plan.amount { "OBSERVED_AS_EXPECTED" } else { "NOT_OBSERVED" },
                "issuerAssetBalance": held,
            }))
        }
        PlanKind::ZsaShieldedTransfer => {
            let recipient = wallet::open(root, profile, Role::Recipient).await?;
            recipient.sync().await?;
            let issuer = wallet::open(root, profile, Role::Issuer).await?;
            issuer.sync().await?;
            let base = base.context("asset")?;
            let received = recipient.zsa_balance(&base).await?;
            Ok(json!({
                "status": if received >= plan.amount { "OBSERVED_AS_EXPECTED" } else { "NOT_OBSERVED" },
                "recipientAssetBalance": received,
                "issuerAssetBalance": issuer.zsa_balance(&base).await?,
                "transferAmount": plan.amount,
            }))
        }
        PlanKind::LabFunding => {
            let issuer = wallet::open(root, profile, Role::Issuer).await?;
            issuer.sync().await?;
            Ok(json!({ "status": "OBSERVED", "issuerOrchardZat": issuer.orchard_zec().await? }))
        }
    }
}

pub fn public_receipt(
    profile: &Profile,
    seen: &ObservedIdentity,
    plan: &Plan,
    facts: &TxFacts,
    inclusion: &Inclusion,
    observed: &Value,
) -> Result<Value> {
    let lab = profile.kind == ProfileKind::Lab;
    let asset = match (plan.kind, &facts.issuance) {
        (PlanKind::ZsaIssuance, Some(i)) => json!({
            "label": plan.asset_label,
            "labelNote": "Off-chain ZWA label; only the description hash is on chain.",
            "assetBase": i.asset_base,
            "assetDescHash": i.asset_desc_hash,
            "issuerValidatingKey": i.ik,
            "finalized": i.finalized,
            "issuedAmount": i.issued_amount,
            "issuedAmountNote": "Public by protocol: ZIP-227 issuance notes are plaintext.",
        }),
        (PlanKind::ZsaShieldedTransfer, _) => json!({
            "assetBase": plan.asset_base,
            "transferAmount": "PRIVATE (shielded; not published)",
        }),
        _ => Value::Null,
    };
    Ok(json!({
        "schema": "zwa-public-zsa-receipt/v1",
        "evidenceClass": if lab { "LOCAL_LAB_NOT_PUBLIC" } else { "PUBLIC_CHAIN_INCLUSION" },
        "network": {
            "label": profile.network_label(),
            "lightwalletd": if lab { "loopback lab endpoint".to_string() } else { profile.lwd_url.clone() },
            "genesisHash": seen.genesis_hash,
            "block1Hash": seen.block1_hash,
            "consensusBranchId": seen.branch_id_hex,
            "nodeSubversion": seen.node_subversion,
            "zebraSource": policy::ZEBRA_SOURCE,
            "walletLibrary": policy::WALLET_LIBRARY,
        },
        "transaction": {
            "kind": plan.kind,
            "txid": facts.txid,
            "txidSource": "community node parser (zebra-chain@6a91831)",
            "sha256": facts.sha256,
            "sizeBytes": facts.size,
            "version": facts.version,
            "feeZat": facts.fee_zat,
            "nodeMinimumFeeZat": facts.conventional_fee_zat,
            "expiryHeight": facts.expiry_height,
            "orchardActions": facts.orchard_actions,
        },
        "inclusion": {
            "mined": inclusion.mined,
            "blockHeight": inclusion.block_height,
            "blockHash": inclusion.block_hash,
            "blockTime": inclusion.block_time,
            "observedTipHeight": inclusion.observed_tip_height,
            "observedTipHash": inclusion.observed_tip_hash,
            "confirmations": inclusion.confirmations,
            "observedAtUnix": now_unix(),
            "method": "lightwalletd GetTransaction by canonical txid; returned bytes identical to the prepared bytes (SHA-256); membership shown by the compact block at the reported height.",
        },
        "asset": asset,
        "walletObservation": {
            "source": "ZWA disposable wallet scan (private; not publicly verifiable)",
            "status": observed.get("status").cloned().unwrap_or(Value::Null),
        },
        "zwaAuthorization": {
            "groth16": "NOT_APPLIED",
            "note": "ZWA's Groth16-gated, human-approved atomic purchase is separate private-regtest evidence and is not represented by this transaction.",
        },
        "trustAssumptions": TRUST_ASSUMPTIONS,
        "excludedClaims": EXCLUDED_CLAIMS,
    }))
}

/// Keys that must never appear in a public receipt.
const FORBIDDEN: &[&str] = &[
    "seed", "mnemonic", "spendingKey", "address", "ufvk", "fvk", "witness", "recipientAssetBalance",
    "issuerAssetBalance", "issuerOrchardZat",
];

pub fn assert_public_safe(receipt: &Value) -> Result<()> {
    fn walk(v: &Value) -> Result<()> {
        match v {
            Value::Object(map) => {
                for (k, v) in map {
                    if FORBIDDEN.iter().any(|f| k.eq_ignore_ascii_case(f)) {
                        anyhow::bail!("public receipt would expose {k}");
                    }
                    walk(v)?;
                }
            }
            Value::Array(items) => items.iter().try_for_each(walk)?,
            Value::String(s) => {
                if s.starts_with("uregtest1") || s.starts_with("utest1") || s.starts_with("uviewregtest") {
                    anyhow::bail!("public receipt would expose an address or key");
                }
            }
            _ => {}
        }
        Ok(())
    }
    walk(receipt)
}

pub fn write(root: &Path, txid: &str, public: &Value, private: &Value) -> Result<()> {
    assert_public_safe(public)?;
    let dir = root.join("receipts");
    if !dir.exists() {
        fs::create_dir(&dir)?;
    }
    let stamp = now_unix();
    write_private(&dir.join(format!("{txid}.{stamp}.public.json")), &serde_json::to_vec_pretty(public)?)?;
    write_private(&dir.join(format!("{txid}.{stamp}.private.json")), &serde_json::to_vec_pretty(private)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_safety_rejects_private_material() {
        assert!(assert_public_safe(&json!({ "txid": "ab", "nested": [{ "fine": 1 }] })).is_ok());
        assert!(assert_public_safe(&json!({ "address": "x" })).is_err());
        assert!(assert_public_safe(&json!({ "a": { "Seed": "x" } })).is_err());
        assert!(assert_public_safe(&json!({ "a": ["uregtest1qqq"] })).is_err());
        assert!(assert_public_safe(&json!({ "recipientAssetBalance": 5 })).is_err());
    }

    #[test]
    fn claims_keep_swap_and_groth16_boundaries() {
        assert!(EXCLUDED_CLAIMS.iter().any(|c| c.contains("Not an atomic swap")));
        assert!(EXCLUDED_CLAIMS.iter().any(|c| c.contains("Groth16")));
    }
}
