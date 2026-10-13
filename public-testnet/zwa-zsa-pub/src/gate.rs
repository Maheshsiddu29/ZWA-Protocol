//! Owner broadcast gate. Public submission requires all of:
//! an environment approval naming the exact txid, an interactive terminal,
//! and the owner typing `BROADCAST <txid>`. The default is refusal, and a
//! txid is submitted at most once per root.

use std::io::{BufRead, IsTerminal, Write};
use std::path::Path;

use anyhow::{bail, Context, Result};

use crate::inspect::TxFacts;
use crate::wallet::write_private;
use crate::Plan;

pub const APPROVAL_ENV: &str = "ZWA_OWNER_BROADCAST_APPROVAL";
/// Refuse to send when the transaction would expire within this many blocks.
pub const EXPIRY_MARGIN: u64 = 3;

pub fn check_expiry(tip: u64, expiry_height: u32) -> Result<()> {
    if expiry_height == 0 {
        bail!("transactions without expiry are not part of this plan");
    }
    if tip + EXPIRY_MARGIN >= u64::from(expiry_height) {
        bail!("transaction expires at {expiry_height}; tip is {tip}. Prepare a new one.");
    }
    Ok(())
}

pub fn approval_phrase(txid: &str) -> String {
    format!("BROADCAST {txid}")
}

/// Pure decision used by `owner_approval` and its tests.
pub fn decide(env_value: Option<&str>, is_tty: bool, typed: Option<&str>, txid: &str) -> Result<()> {
    if env_value != Some(txid) {
        bail!("{APPROVAL_ENV} must equal the exact txid being broadcast");
    }
    if !is_tty {
        bail!("public broadcast requires the owner at an interactive terminal");
    }
    match typed {
        Some(t) if t.trim_end_matches(['\r', '\n']) == approval_phrase(txid) => Ok(()),
        _ => bail!("owner did not type the approval phrase; nothing was sent"),
    }
}

pub fn owner_approval(txid: &str, plan: &Plan, facts: &TxFacts) -> Result<()> {
    let env_value = std::env::var(APPROVAL_ENV).ok();
    let is_tty = std::io::stdin().is_terminal() && std::io::stderr().is_terminal();
    // Check the non-interactive conditions before prompting.
    decide(env_value.as_deref(), is_tty, Some(&approval_phrase(txid)), txid)?;
    let mut err = std::io::stderr();
    writeln!(err, "\n=== PUBLIC BROADCAST: community ZSA Testnet (experimental) ===")?;
    writeln!(err, "kind:            {:?}", plan.kind)?;
    writeln!(err, "txid:            {txid}")?;
    writeln!(err, "fee (zat):       {} (node minimum {})", facts.fee_zat, facts.conventional_fee_zat)?;
    writeln!(err, "expiry height:   {}", facts.expiry_height)?;
    if let Some(i) = &facts.issuance {
        writeln!(err, "asset base:      {}", i.asset_base)?;
        writeln!(err, "issued (public): {} finalized={}", i.issued_amount, i.finalized)?;
    }
    writeln!(err, "This is not an atomic swap and not ZWA Groth16-gated settlement.")?;
    writeln!(err, "Type exactly `{}` to send, anything else refuses:", approval_phrase(txid))?;
    let mut line = String::new();
    std::io::stdin().lock().read_line(&mut line).context("read approval")?;
    decide(env_value.as_deref(), is_tty, Some(&line), txid)
}

/// One submission per txid per root; the marker is written before sending.
pub fn record_submission(root: &Path, txid: &str, tip: u64) -> Result<()> {
    let dir = root.join("submitted");
    if !dir.exists() {
        std::fs::create_dir(&dir)?;
    }
    let body = serde_json::json!({ "txid": txid, "tipAtSubmission": tip });
    write_private(&dir.join(format!("{txid}.json")), &serde_json::to_vec_pretty(&body)?)
        .context("this txid was already submitted from this root")
}

#[cfg(test)]
mod tests {
    use super::*;

    const TXID: &str = "aa00000000000000000000000000000000000000000000000000000000000000";

    #[test]
    fn default_is_refusal() {
        assert!(decide(None, true, Some(&approval_phrase(TXID)), TXID).is_err());
        assert!(decide(Some("yes"), true, Some(&approval_phrase(TXID)), TXID).is_err());
        assert!(decide(Some(TXID), false, Some(&approval_phrase(TXID)), TXID).is_err());
        assert!(decide(Some(TXID), true, None, TXID).is_err());
        assert!(decide(Some(TXID), true, Some("y\n"), TXID).is_err());
        assert!(decide(Some(TXID), true, Some("BROADCAST"), TXID).is_err());
        let other = "bb00000000000000000000000000000000000000000000000000000000000000";
        assert!(decide(Some(TXID), true, Some(&approval_phrase(other)), TXID).is_err());
        assert!(decide(Some(TXID), true, Some(&format!("{}\n", approval_phrase(TXID))), TXID).is_ok());
    }

    #[test]
    fn expiry_margin_is_enforced() {
        assert!(check_expiry(100, 140).is_ok());
        assert!(check_expiry(137, 140).is_err());
        assert!(check_expiry(200, 140).is_err());
        assert!(check_expiry(1, 0).is_err());
    }

    #[test]
    fn a_txid_is_submitted_once() {
        let dir = tempfile::tempdir().unwrap();
        assert!(record_submission(dir.path(), TXID, 5).is_ok());
        assert!(record_submission(dir.path(), TXID, 6).is_err());
    }
}
