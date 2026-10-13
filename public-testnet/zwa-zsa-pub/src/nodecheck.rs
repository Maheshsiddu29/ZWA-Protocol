//! Mandatory second parser: the community node's own `zebra-chain@6a91831`
//! (via the separate `zwa-node-check` binary, which pins the node's crate
//! graph and feature set). Its txid is canonical: blocks, lightwalletd and
//! explorers index transactions by it. The wallet library's txid can differ
//! when its V6 reader normalizes Orchard flags (see `inspect`).

use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use anyhow::{bail, Context, Result};
use serde_json::Value;

pub const NODE_CHECK_ENV: &str = "ZWA_NODE_CHECK";
const DEFAULT_PATH: &str = "/private/tmp/zwa-pub-build/nodecheck-target/release/zwa-node-check";

#[derive(Debug, Clone)]
pub struct NodeView {
    pub txid: String,
    pub issuance_asset_bases: Vec<String>,
    pub orchard_zsa_enabled: bool,
}

fn binary() -> Result<PathBuf> {
    let path = PathBuf::from(std::env::var(NODE_CHECK_ENV).unwrap_or_else(|_| DEFAULT_PATH.to_string()));
    if !path.is_absolute() {
        bail!("{NODE_CHECK_ENV} must be an absolute path");
    }
    let meta = std::fs::metadata(&path).with_context(|| format!("zwa-node-check not found at {}", path.display()))?;
    if !meta.is_file() || meta.permissions().mode() & 0o111 == 0 {
        bail!("zwa-node-check at {} is not executable", path.display());
    }
    Ok(path)
}

pub fn check(bytes: &[u8]) -> Result<NodeView> {
    let mut child = Command::new(binary()?)
        .arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("run zwa-node-check")?;
    child.stdin.take().context("stdin")?.write_all(bytes)?;
    let out = child.wait_with_output()?;
    let report: Value = serde_json::from_slice(&out.stdout).context("zwa-node-check output")?;
    if report["nodeParse"] != "ACCEPTED" || !out.status.success() {
        bail!("node parser rejects the transaction: {}", report["reason"]);
    }
    let facts = &report["facts"];
    let txid = facts["txid"].as_str().context("node txid")?.to_string();
    let orchard_zsa_enabled = facts["orchardZsaEnabled"].as_bool().context("node OrchardZSA flag")?;
    let issuance_asset_bases = facts["issuance"]["actions"]
        .as_array()
        .map(|a| a.iter().filter_map(|x| x["assetBase"].as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    Ok(NodeView { txid, issuance_asset_bases, orchard_zsa_enabled })
}
