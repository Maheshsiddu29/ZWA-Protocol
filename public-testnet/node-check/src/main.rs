//! Node-rule check for prepared ZWA public-testnet transactions
//! (NEW ZWA PROTOCOL WORK).
//!
//! Deserializes raw transaction files with the community node's own
//! `zebra-chain` (zcash-shielded-assets/zebra@6a91831, `tx_v6`, NU7) and
//! recomputes each issuance AssetBase with the node's Orchard fork. This
//! exercises the node's parsing rules (one Action Group, zero group expiry,
//! ZIP-233 field layout) without a network connection. It does not verify
//! proofs, signatures or anchors; only the node can do that.

#![forbid(unsafe_code)]

use std::process::ExitCode;

use orchard::note::{AssetBase, AssetId};
use serde_json::{json, Value};
use zebra_chain::serialization::{ZcashDeserializeInto, ZcashSerialize};
use zebra_chain::transaction::Transaction;

fn check(bytes: &[u8]) -> Result<Value, String> {
    let tx: Transaction = bytes.zcash_deserialize_into().map_err(|e| format!("node rejects bytes: {e}"))?;
    let round_trip = tx.zcash_serialize_to_vec().map_err(|e| e.to_string())?;
    if round_trip != bytes {
        return Err("node re-serialization differs from the prepared bytes".into());
    }
    let issuance = match tx.orchard_zsa_issue_data() {
        None => Value::Null,
        Some(data) => {
            let bundle = data.inner();
            let mut actions = Vec::new();
            for action in bundle.actions().iter() {
                let base = AssetBase::custom(&AssetId::new_v0(bundle.ik(), action.asset_desc_hash()));
                let notes_match = action.notes().iter().all(|n| n.asset() == base);
                if !notes_match {
                    return Err("issuance note AssetBase differs from node derivation".into());
                }
                actions.push(json!({
                    "assetDescHash": hex::encode(action.asset_desc_hash()),
                    "assetBase": hex::encode(base.to_bytes()),
                    "finalized": action.is_finalized(),
                    "issuedAmount": action.notes().iter().map(|n| n.value().inner()).sum::<u64>(),
                }));
            }
            json!({ "ik": hex::encode(bundle.ik().encode()), "actions": actions })
        }
    };
    Ok(json!({
        "txid": tx.hash().to_string(),
        "version": tx.version(),
        "expiryHeight": tx.expiry_height().map(|h| h.0),
        "orchardActions": tx.orchard_action_count(),
        "issuance": issuance,
        "nodeSource": "zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f",
    }))
}

fn main() -> ExitCode {
    let paths: Vec<String> = std::env::args().skip(1).collect();
    if paths.is_empty() {
        eprintln!("usage: zwa-node-check <tx-file | ->...");
        return ExitCode::from(2);
    }
    let mut ok = true;
    for path in paths {
        let bytes = if path == "-" {
            let mut buf = Vec::new();
            std::io::Read::read_to_end(&mut std::io::stdin(), &mut buf).map(|_| buf)
        } else {
            std::fs::read(&path)
        };
        let result = bytes.map_err(|e| e.to_string()).and_then(|b| check(&b));
        match result {
            Ok(v) => println!("{}", json!({ "file": path, "nodeParse": "ACCEPTED", "facts": v })),
            Err(e) => {
                ok = false;
                println!("{}", json!({ "file": path, "nodeParse": "REJECTED", "reason": e }));
            }
        }
    }
    if ok { ExitCode::SUCCESS } else { ExitCode::FAILURE }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn garbage_is_rejected() {
        assert!(check(&[]).is_err());
        assert!(check(&[0u8; 100]).is_err());
    }
}
