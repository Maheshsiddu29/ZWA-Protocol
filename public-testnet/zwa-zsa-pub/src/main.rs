//! ZWA public ZSA testnet client (NEW ZWA PROTOCOL WORK).
//!
//! Experimental issuance and shielded transfer on the community ZSA Testnet,
//! built on Zkool's `rlz` wallet library (PRE-EXISTING community tooling,
//! MIT). This is not ZWA's Groth16-gated atomic purchase and never builds a
//! bilateral swap. Every public submission requires the owner gate in
//! `gate::owner_approval`.

#![forbid(unsafe_code)]

mod chain;
mod gate;
mod inspect;
mod nodecheck;
mod policy;
mod receipt;
mod wallet;

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand, ValueEnum};
use serde::{Deserialize, Serialize};

use policy::{Profile, ProfileKind};
use wallet::{write_private, Role};

#[derive(Parser)]
#[command(name = "zwa-zsa-pub", about = "Isolated ZWA client for the community ZSA Testnet")]
struct Cli {
    /// Owner-only root: /private/tmp/zwa-pub-lab-* or /private/tmp/zwa-pub-public-*.
    #[arg(long)]
    root: String,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Clone, Copy, ValueEnum)]
enum ProfileArg {
    Lab,
    Public,
}

#[derive(Clone, Copy, ValueEnum)]
enum AddressKind {
    Orchard,
    Transparent,
}

#[derive(Subcommand)]
enum Cmd {
    /// Create a fresh root bound to one network after a live identity check.
    Init {
        #[arg(long, value_enum)]
        profile: ProfileArg,
        /// Loopback lightwalletd for a lab root. Ignored (fixed) for public.
        #[arg(long)]
        lwd: Option<String>,
    },
    /// Re-check the live network against the pins and this root's binding.
    Identity,
    /// Create a new disposable wallet for a role.
    WalletInit {
        #[arg(long, value_enum)]
        role: Role,
    },
    /// Print a wallet's receiving address (PRIVATE: for the owner only).
    Address {
        #[arg(long, value_enum)]
        role: Role,
        #[arg(long, value_enum, default_value = "orchard")]
        kind: AddressKind,
    },
    Sync {
        #[arg(long, value_enum)]
        role: Role,
    },
    /// Private wallet balances (never part of a public receipt).
    Balance {
        #[arg(long, value_enum)]
        role: Role,
    },
    /// Build, sign and inspect a finalized first issuance. Does not send.
    PrepareIssue {
        #[arg(long)]
        name: String,
        #[arg(long)]
        amount: u64,
    },
    /// Build, sign and inspect an issuer -> recipient ZSA transfer. Does not send.
    PrepareTransfer {
        #[arg(long)]
        asset_base: String,
        #[arg(long)]
        amount: u64,
    },
    /// LAB ONLY: shield the lab faucet's matured coinbase to its own Orchard address.
    LabShield,
    /// LAB ONLY: send local tZEC from the lab faucet wallet to the issuer.
    LabFund {
        #[arg(long)]
        amount: u64,
    },
    /// Re-inspect a prepared transaction.
    Inspect {
        #[arg(long)]
        txid: String,
    },
    /// Submit a prepared transaction (public roots require the owner gate).
    Broadcast {
        #[arg(long)]
        txid: String,
    },
    /// Verify inclusion through lightwalletd and write receipts.
    Verify {
        #[arg(long)]
        txid: String,
    },
}

#[derive(Serialize, Deserialize, Clone)]
struct Binding {
    schema: String,
    profile: Profile,
    genesis_hash: String,
    block1_hash: String,
    bound_at_height: u64,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum PlanKind {
    ZsaIssuance,
    ZsaShieldedTransfer,
    LabFunding,
}

#[derive(Serialize, Deserialize)]
pub struct Plan {
    schema: String,
    kind: PlanKind,
    profile: ProfileKind,
    built_at_height: u32,
    asset_label: Option<String>,
    asset_base: Option<String>,
    /// Requested amount. For a transfer this is private and never published.
    amount: u64,
    recipient_role: Option<Role>,
    recipient_decryption_checked: bool,
    facts: serde_json::Value,
}

fn root_kind(raw: &str) -> Result<ProfileKind> {
    if raw.starts_with("/private/tmp/zwa-pub-lab-") {
        Ok(ProfileKind::Lab)
    } else if raw.starts_with("/private/tmp/zwa-pub-public-") {
        Ok(ProfileKind::Public)
    } else {
        bail!("root must be /private/tmp/zwa-pub-lab-* or /private/tmp/zwa-pub-public-*")
    }
}

fn load_binding(root: &Path) -> Result<Binding> {
    let b: Binding = serde_json::from_slice(&fs::read(root.join("binding.json")).context("root is not initialised")?)?;
    if b.schema != "zwa-public-zsa-binding/v1" {
        bail!("unknown binding schema");
    }
    Ok(b)
}

/// Live identity must satisfy the pins and still match this root's binding.
async fn checked(root: &Path) -> Result<(Binding, chain::Lwd, policy::ObservedIdentity)> {
    let binding = load_binding(root)?;
    let mut lwd = chain::connect(&binding.profile).await?;
    let seen = chain::identity(&mut lwd).await?;
    policy::check_identity(&binding.profile, &seen)?;
    if seen.genesis_hash != binding.genesis_hash || seen.block1_hash != binding.block1_hash {
        bail!("CHAIN_RESET: live chain no longer matches this root's binding");
    }
    Ok((binding, lwd, seen))
}

fn pending_dir(root: &Path) -> Result<PathBuf> {
    let dir = root.join("pending");
    if !dir.exists() {
        fs::create_dir(&dir)?;
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700))?;
    }
    Ok(dir)
}

fn check_txid(txid: &str) -> Result<()> {
    if txid.len() != 64 || !txid.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
        bail!("txid must be 64 lowercase hex characters");
    }
    Ok(())
}

/// Wallet-library inspection plus the node's own parser. The node's txid is
/// canonical; any divergence must be explained by the one known Orchard-flags
/// normalization, and issuance AssetBases must agree across both crate graphs.
fn facts_for(kind: PlanKind, bytes: &[u8]) -> Result<inspect::TxFacts> {
    let mut facts = match kind {
        PlanKind::LabFunding => inspect::facts_lab_funding(bytes)?,
        _ => inspect::facts(bytes)?,
    };
    let node = nodecheck::check(bytes)?;
    if node.txid != facts.wallet_library_txid && !facts.orchard_flags_normalized {
        bail!("wallet library and node compute different txids without a known cause");
    }
    let wallet_bases: Vec<String> = facts.issuance.iter().map(|i| i.asset_base.clone()).collect();
    if node.issuance_asset_bases != wallet_bases {
        bail!("node and wallet derive different issuance AssetBases");
    }
    facts.txid = node.txid;
    Ok(facts)
}

fn load_pending(root: &Path, txid: &str) -> Result<(Vec<u8>, Plan)> {
    check_txid(txid)?;
    let dir = root.join("pending");
    let bytes = fs::read(dir.join(format!("{txid}.tx"))).context("no prepared transaction with that txid")?;
    let plan: Plan = serde_json::from_slice(&fs::read(dir.join(format!("{txid}.plan.json")))?)?;
    if plan.kind == PlanKind::LabFunding && plan.profile != ProfileKind::Lab {
        bail!("lab funding plan outside a lab root");
    }
    let facts = facts_for(plan.kind, &bytes)?;
    if facts.txid != txid || serde_json::to_value(&facts)?["sha256"] != plan.facts["sha256"] {
        bail!("prepared transaction file does not match its plan");
    }
    Ok((bytes, plan))
}

/// Inspect freshly built bytes. A candidate that fails is kept as
/// `pending/rejected-<sha256>.tx` for review; it has no plan and can never be
/// broadcast by this tool.
fn inspect_new(root: &Path, kind: PlanKind, bytes: &[u8]) -> Result<inspect::TxFacts> {
    facts_for(kind, bytes).or_else(|e| {
        use sha2::Digest;
        let name = format!("rejected-{}.tx", hex::encode(sha2::Sha256::digest(bytes)));
        let _ = pending_dir(root).and_then(|d| write_private(&d.join(&name), bytes));
        Err(e.context(format!("candidate quarantined as pending/{name}")))
    })
}

fn save_plan(root: &Path, bytes: &[u8], plan: &Plan) -> Result<String> {
    let txid = plan.facts["txid"].as_str().context("plan txid")?.to_string();
    let dir = pending_dir(root)?;
    write_private(&dir.join(format!("{txid}.tx")), bytes)?;
    write_private(&dir.join(format!("{txid}.plan.json")), &serde_json::to_vec_pretty(plan)?)?;
    Ok(txid)
}

fn print(value: &impl Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cli = Cli::parse();
    let kind = root_kind(&cli.root)?;
    let root = policy::root_path(kind, &cli.root)?;

    if let Cmd::Init { profile, lwd } = &cli.cmd {
        let profile = match (profile, kind) {
            (ProfileArg::Public, ProfileKind::Public) => Profile::public(),
            (ProfileArg::Lab, ProfileKind::Lab) => Profile::lab(lwd.as_deref().context("--lwd is required for a lab root")?)?,
            _ => bail!("profile does not match the root name"),
        };
        let mut client = chain::connect(&profile).await?;
        let seen = chain::identity(&mut client).await?;
        policy::check_identity(&profile, &seen)?;
        policy::create_root(&root)?;
        let binding = Binding {
            schema: "zwa-public-zsa-binding/v1".into(),
            profile,
            genesis_hash: seen.genesis_hash.clone(),
            block1_hash: seen.block1_hash.clone(),
            bound_at_height: seen.tip_height,
        };
        write_private(&root.join("binding.json"), &serde_json::to_vec_pretty(&binding)?)?;
        return print(&serde_json::json!({ "root": root, "identity": seen, "bound": true }));
    }

    policy::open_root(&root)?;
    match cli.cmd {
        Cmd::Init { .. } => unreachable!(),
        Cmd::Identity => {
            let (binding, _, seen) = checked(&root).await?;
            print(&serde_json::json!({ "network": binding.profile.network_label(), "identity": seen, "matchesBinding": true }))
        }
        Cmd::WalletInit { role } => {
            let (binding, _, _) = checked(&root).await?;
            print(&wallet::init(&root, &binding.profile, role).await?)
        }
        Cmd::Address { role, kind } => {
            let (binding, _, _) = checked(&root).await?;
            let w = wallet::open(&root, &binding.profile, role).await?;
            let address = match kind {
                AddressKind::Orchard => w.orchard_address().await?,
                AddressKind::Transparent => w.transparent_address().await?,
            };
            eprintln!("PRIVATE: do not paste this address into receipts, commits or chat logs.");
            println!("{address}");
            Ok(())
        }
        Cmd::Sync { role } => {
            let (binding, _, _) = checked(&root).await?;
            let w = wallet::open(&root, &binding.profile, role).await?;
            let height = w.sync().await?;
            print(&serde_json::json!({ "role": role.name(), "syncedTo": height }))
        }
        Cmd::Balance { role } => {
            let (binding, _, _) = checked(&root).await?;
            let w = wallet::open(&root, &binding.profile, role).await?;
            w.sync().await?;
            print(&w.balances().await?)
        }
        Cmd::PrepareIssue { name, amount } => {
            policy::check_asset_name(&name)?;
            if amount == 0 || amount > 1_000_000_000_000 {
                bail!("amount must be between 1 and 10^12 base units");
            }
            let (binding, _, _) = checked(&root).await?;
            if root.join("issued-names").join(&name).exists() {
                bail!("this root already prepared an issuance named {name}; names are single-use");
            }
            let w = wallet::open(&root, &binding.profile, Role::Issuer).await?;
            let height = w.sync().await?;
            let bytes = w.build_issuance(&name, amount).await?;
            let facts = inspect_new(&root, PlanKind::ZsaIssuance, &bytes)?;
            inspect::expect_issuance(&facts, &name, amount)?;
            let plan = Plan {
                schema: "zwa-public-zsa-plan/v1".into(),
                kind: PlanKind::ZsaIssuance,
                profile: binding.profile.kind,
                built_at_height: height,
                asset_label: Some(name.clone()),
                asset_base: facts.issuance.as_ref().map(|i| i.asset_base.clone()),
                amount,
                recipient_role: None,
                recipient_decryption_checked: false,
                facts: serde_json::to_value(&facts)?,
            };
            let txid = save_plan(&root, &bytes, &plan)?;
            let names = root.join("issued-names");
            fs::create_dir_all(&names)?;
            write_private(&names.join(&name), txid.as_bytes())?;
            print(&plan)
        }
        Cmd::PrepareTransfer { asset_base, amount } => {
            let base: [u8; 32] = hex::decode(&asset_base)?
                .try_into()
                .map_err(|_| anyhow::anyhow!("asset base must be 32 bytes"))?;
            if amount == 0 {
                bail!("amount must be positive");
            }
            let (binding, _, _) = checked(&root).await?;
            let issuer = wallet::open(&root, &binding.profile, Role::Issuer).await?;
            let recipient = wallet::open(&root, &binding.profile, Role::Recipient).await?;
            let height = issuer.sync().await?;
            let held = issuer.zsa_balance(&base).await?;
            if held < amount {
                bail!("issuer holds {held} units of that asset; cannot transfer {amount}");
            }
            let address = recipient.orchard_address().await?;
            let bytes = issuer.build_payment(address, amount, base.to_vec()).await?;
            let facts = inspect_new(&root, PlanKind::ZsaShieldedTransfer, &bytes)?;
            let fvk = recipient.orchard_fvk().await?;
            inspect::expect_transfer(&bytes, &facts, &fvk, &base, amount)?;
            let plan = Plan {
                schema: "zwa-public-zsa-plan/v1".into(),
                kind: PlanKind::ZsaShieldedTransfer,
                profile: binding.profile.kind,
                built_at_height: height,
                asset_label: None,
                asset_base: Some(asset_base),
                amount,
                recipient_role: Some(Role::Recipient),
                recipient_decryption_checked: true,
                facts: serde_json::to_value(&facts)?,
            };
            save_plan(&root, &bytes, &plan)?;
            print(&plan)
        }
        Cmd::LabShield => {
            let (binding, _, _) = checked(&root).await?;
            if binding.profile.kind != ProfileKind::Lab {
                bail!("lab-shield exists only for lab roots");
            }
            let faucet = wallet::open(&root, &binding.profile, Role::Labfaucet).await?;
            let height = faucet.sync().await?;
            let bytes = faucet.build_shield_all().await?;
            let facts = inspect_new(&root, PlanKind::LabFunding, &bytes)?;
            if facts.transparent_outputs != 0 {
                bail!("shielding must not create transparent outputs");
            }
            let plan = Plan {
                schema: "zwa-public-zsa-plan/v1".into(),
                kind: PlanKind::LabFunding,
                profile: binding.profile.kind,
                built_at_height: height,
                asset_label: None,
                asset_base: None,
                amount: 0,
                recipient_role: Some(Role::Labfaucet),
                recipient_decryption_checked: false,
                facts: serde_json::to_value(&facts)?,
            };
            let txid = save_plan(&root, &bytes, &plan)?;
            let tip = faucet.height().await?;
            gate::record_submission(&root, &txid, u64::from(tip))?;
            let sent = faucet.submit(tip, &bytes).await?;
            print(&serde_json::json!({ "labShieldTxid": txid, "lightwalletdReply": sent }))
        }
        Cmd::LabFund { amount } => {
            let (binding, _, _) = checked(&root).await?;
            if binding.profile.kind != ProfileKind::Lab {
                bail!("lab-fund exists only for lab roots; public funding comes from the faucet");
            }
            let faucet = wallet::open(&root, &binding.profile, Role::Labfaucet).await?;
            let issuer = wallet::open(&root, &binding.profile, Role::Issuer).await?;
            let height = faucet.sync().await?;
            let bytes = faucet.build_payment(issuer.orchard_address().await?, amount, vec![]).await?;
            let facts = inspect_new(&root, PlanKind::LabFunding, &bytes)?;
            let plan = Plan {
                schema: "zwa-public-zsa-plan/v1".into(),
                kind: PlanKind::LabFunding,
                profile: binding.profile.kind,
                built_at_height: height,
                asset_label: None,
                asset_base: None,
                amount,
                recipient_role: Some(Role::Issuer),
                recipient_decryption_checked: false,
                facts: serde_json::to_value(&facts)?,
            };
            let txid = save_plan(&root, &bytes, &plan)?;
            let tip = faucet.height().await?;
            gate::record_submission(&root, &txid, u64::from(tip))?;
            let sent = faucet.submit(tip, &bytes).await?;
            print(&serde_json::json!({ "labFundingTxid": txid, "lightwalletdReply": sent }))
        }
        Cmd::Inspect { txid } => {
            let (_, plan) = load_pending(&root, &txid)?;
            print(&plan)
        }
        Cmd::Broadcast { txid } => {
            let (binding, mut lwd, seen) = checked(&root).await?;
            let (bytes, plan) = load_pending(&root, &txid)?;
            let facts = facts_for(plan.kind, &bytes)?;
            let tip = seen.tip_height;
            gate::check_expiry(tip, facts.expiry_height)?;
            if binding.profile.kind == ProfileKind::Public {
                gate::owner_approval(&txid, &plan, &facts)?;
            }
            let (tip_now, _) = chain::tip(&mut lwd).await?;
            gate::check_expiry(tip_now, facts.expiry_height)?;
            gate::record_submission(&root, &txid, tip_now)?;
            let w = wallet::open(&root, &binding.profile, Role::Issuer).await?;
            let reply = w.submit(tip_now as u32, &bytes).await?;
            print(&serde_json::json!({ "submitted": txid, "tipAtSubmission": tip_now, "lightwalletdReply": reply }))
        }
        Cmd::Verify { txid } => {
            check_txid(&txid)?;
            let (binding, mut lwd, seen) = checked(&root).await?;
            let (bytes, plan) = load_pending(&root, &txid)?;
            let expected_sha = plan.facts["sha256"].as_str().context("plan sha256")?;
            let inclusion = chain::inclusion(&mut lwd, &txid, expected_sha).await?;
            let observed = receipt::wallet_observation(&root, &binding.profile, &plan, inclusion.mined).await?;
            let public = receipt::public_receipt(&binding.profile, &seen, &plan, &facts_for(plan.kind, &bytes)?, &inclusion, &observed)?;
            receipt::assert_public_safe(&public)?;
            // Receipts are written only for mined transactions, so polling
            // an unconfirmed txid leaves no files behind.
            if inclusion.mined {
                receipt::write(&root, &txid, &public, &observed)?;
            }
            print(&public)
        }
    }
}
