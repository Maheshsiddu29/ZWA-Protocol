//! Ten release-mode runs of the pinned public N=3 rooted request.

use std::time::{Duration, Instant};

use serde_json::json;
use sha2::{Digest, Sha256};
use zwa_layer_b::policy::ProvenancePolicy;
use zwa_lineage::phase2::{
    Phase2LineageVerifier, Phase2VerifierConfig, SnapshotChainView, SnapshotId, TradePermission,
};
use zwa_lineage::LineageStatus;

const REQUEST: &[u8] = include_bytes!("../tests/fixtures/phase2/n3-v3.bin");
const POLICY: &[u8] = include_bytes!("../tests/fixtures/phase2/policy.bin");
const REQUEST_SHA256: &str = "0821a7abafca556bdcf9403ed9e040603194bf5d46650316d42554ee9c96ebb0";
const POLICY_SHA256: &str = "ce433a74be5510050d7f7f85cc1360f28c9e6bf71acbd68a1e7862a0925d14be";

fn micros(d: Option<Duration>) -> Option<u64> {
    d.map(|v| u64::try_from(v.as_micros()).unwrap_or(u64::MAX))
}

fn run() -> Result<(), String> {
    if hex::encode(Sha256::digest(REQUEST)) != REQUEST_SHA256
        || hex::encode(Sha256::digest(POLICY)) != POLICY_SHA256
    {
        return Err("pinned fixture SHA-256 mismatch".into());
    }
    let view = SnapshotChainView::load(SnapshotId::A7)?;
    let policy = ProvenancePolicy::decode(POLICY).ok_or("invalid policy fixture")?;
    let config = Phase2VerifierConfig::new(view.network_id(), POLICY.to_vec(), policy.digest());
    let verifier = Phase2LineageVerifier::new(view, config)?;
    let mut rows = Vec::with_capacity(10);
    for run in 0..10 {
        let result = verifier.verify_v3(REQUEST);
        if result.status != LineageStatus::RootedLineageVerified {
            return Err(format!("run {run}: {} {:?}", result.status, result.failure));
        }
        let gate_start = Instant::now();
        let gate = TradePermission::from_result(&result);
        let gate_time = gate_start.elapsed();
        if !gate.allowed {
            return Err(format!("run {run}: matcher gate denied VERIFIED"));
        }
        let e = &result.evidence;
        rows.push(json!({
            "run": run + 1,
            "decode_us": micros(e.decode_time),
            "design_a_us": micros(e.design_a_time),
            "chain_ref_us": micros(e.chain_context_time),
            "root_auth_us": micros(e.root_auth_time),
            "terminal_auth_us": micros(e.terminal_auth_time),
            "finality_us": micros(e.finality_time),
            "total_us": micros(Some(e.total_time)),
            "matcher_gate_us": micros(Some(gate_time)),
        }));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({"snapshot": "A7", "runs": rows}))
            .map_err(|e| e.to_string())?
    );
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("measurement failed: {e}");
        std::process::exit(1);
    }
}
