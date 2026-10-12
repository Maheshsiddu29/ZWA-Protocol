//! Network identity pins, profile selection and private-root rules.
//!
//! Every constant here was observed read-only on 2026-10-12 and is rechecked
//! live before any wallet, construction or broadcast step. A mismatch fails
//! closed; nothing here can be overridden from the command line.

use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

/// The only public lightwalletd this tool will ever talk to.
pub const PUBLIC_LWD: &str = "https://zsa.methyl.cc";
/// Community ZSA Testnet genesis (display order). Matches
/// `ZSA_TESTNET_GENESIS_HASH` in zcash-shielded-assets/zebra@6a91831.
pub const GENESIS_HASH: &str = "03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7";
/// Block 1 of the public network (display order). A lab chain mines its own
/// block 1 from the same genesis, so this separates the two chains.
pub const PUBLIC_BLOCK1_HASH: &str =
    "01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274";
/// NU7 / librustzcash-fork `BranchId::Zsa`.
pub const BRANCH_ID: u32 = 0x7719_0ad8;
pub const BRANCH_ID_HEX: &str = "77190ad8";
pub const CHAIN_NAME: &str = "test";
pub const NODE_SUBVERSION: &str = "/Zebra:5.0.0/";
pub const ZEBRA_SOURCE: &str =
    "zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f";
pub const WALLET_LIBRARY: &str = "hhanh00/zkool2@51df4f0b356a886d5d2308811f215366ab648d65 (rlz)";
/// Experimental assets issued by this tool carry this prefix.
pub const ASSET_PREFIX: &str = "ZWA-TESTNET-";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ProfileKind {
    Lab,
    Public,
}

impl ProfileKind {
    fn root_prefix(self) -> &'static str {
        match self {
            ProfileKind::Lab => "zwa-pub-lab-",
            ProfileKind::Public => "zwa-pub-public-",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Profile {
    pub kind: ProfileKind,
    pub lwd_url: String,
}

impl Profile {
    pub fn public() -> Self {
        Profile { kind: ProfileKind::Public, lwd_url: PUBLIC_LWD.to_string() }
    }

    /// A lab profile may only point at a loopback lightwalletd.
    pub fn lab(url: &str) -> Result<Self> {
        let rest = url
            .strip_prefix("http://")
            .context("lab lightwalletd must be plain http on loopback")?;
        let (host, port) = rest
            .rsplit_once(':')
            .context("lab lightwalletd URL needs an explicit port")?;
        if !matches!(host, "127.0.0.1" | "[::1]" | "localhost") {
            bail!("lab lightwalletd must be on loopback, got {host}");
        }
        let port: u16 = port.parse().context("invalid lab port")?;
        if port < 1024 {
            bail!("lab port must be unprivileged");
        }
        Ok(Profile { kind: ProfileKind::Lab, lwd_url: format!("http://{host}:{port}") })
    }

    pub fn network_label(&self) -> &'static str {
        match self.kind {
            ProfileKind::Lab => "LOCAL LAB fork of community ZSA Testnet parameters (not public)",
            ProfileKind::Public => "Community ZSA Testnet (public, experimental)",
        }
    }
}

/// Facts read from lightwalletd that pin the network.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObservedIdentity {
    pub chain_name: String,
    pub branch_id_hex: String,
    pub node_subversion: String,
    pub tip_height: u64,
    pub genesis_hash: String,
    pub block1_hash: String,
}

pub fn check_identity(profile: &Profile, seen: &ObservedIdentity) -> Result<()> {
    if seen.chain_name != CHAIN_NAME {
        bail!("unexpected chain name {:?}", seen.chain_name);
    }
    if seen.branch_id_hex != BRANCH_ID_HEX {
        bail!("unexpected consensus branch {:?}", seen.branch_id_hex);
    }
    if seen.node_subversion != NODE_SUBVERSION {
        bail!("unexpected node version {:?}", seen.node_subversion);
    }
    if seen.genesis_hash != GENESIS_HASH {
        bail!("genesis mismatch: {}", seen.genesis_hash);
    }
    match profile.kind {
        ProfileKind::Public if seen.block1_hash != PUBLIC_BLOCK1_HASH => {
            bail!("public block 1 differs: network reset or wrong endpoint")
        }
        ProfileKind::Lab if seen.block1_hash == PUBLIC_BLOCK1_HASH => {
            bail!("lab endpoint is serving the public chain; refusing")
        }
        _ => Ok(()),
    }
}

/// Validate a private root path for the given profile without touching disk.
pub fn root_path(kind: ProfileKind, raw: &str) -> Result<PathBuf> {
    let path = Path::new(raw);
    let parent = path.parent().context("root has no parent")?;
    if parent != Path::new("/private/tmp") {
        bail!("root must be a direct child of /private/tmp");
    }
    let name = path.file_name().and_then(|n| n.to_str()).context("root name")?;
    let suffix = name
        .strip_prefix(kind.root_prefix())
        .with_context(|| format!("root name must start with {}", kind.root_prefix()))?;
    if suffix.is_empty()
        || suffix.len() > 40
        || !suffix.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        bail!("root suffix must be 1-40 characters of [A-Za-z0-9-]");
    }
    Ok(path.to_path_buf())
}

/// Create a fresh owner-only root. Refuses to adopt anything that exists.
pub fn create_root(path: &Path) -> Result<()> {
    if fs::symlink_metadata(path).is_ok() {
        bail!("refusing to adopt existing path {}", path.display());
    }
    fs::create_dir(path).with_context(|| format!("create {}", path.display()))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

/// An existing root must be a real directory, owned by us, mode 0700.
pub fn open_root(path: &Path) -> Result<()> {
    let meta = fs::symlink_metadata(path).with_context(|| format!("{} missing", path.display()))?;
    if !meta.file_type().is_dir() {
        bail!("root is not a directory (symlinks are refused)");
    }
    if meta.mode() & 0o077 != 0 {
        bail!("root must be owner-only (0700)");
    }
    let home = std::env::var("HOME").context("HOME unset")?;
    if meta.uid() != fs::metadata(home)?.uid() {
        bail!("root is owned by another user");
    }
    Ok(())
}

/// Experimental asset names: fixed prefix, uppercase ASCII, bounded length.
pub fn check_asset_name(name: &str) -> Result<()> {
    let suffix = name
        .strip_prefix(ASSET_PREFIX)
        .with_context(|| format!("asset name must start with {ASSET_PREFIX}"))?;
    if suffix.is_empty()
        || name.len() > 64
        || !suffix.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '-')
    {
        bail!("asset name suffix must be [A-Z0-9-] and the whole name at most 64 bytes");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity(block1: &str) -> ObservedIdentity {
        ObservedIdentity {
            chain_name: "test".into(),
            branch_id_hex: "77190ad8".into(),
            node_subversion: NODE_SUBVERSION.into(),
            tip_height: 10,
            genesis_hash: GENESIS_HASH.into(),
            block1_hash: block1.into(),
        }
    }

    #[test]
    fn public_profile_is_fixed() {
        assert_eq!(Profile::public().lwd_url, "https://zsa.methyl.cc");
    }

    #[test]
    fn lab_profile_is_loopback_only() {
        assert!(Profile::lab("http://127.0.0.1:19067").is_ok());
        assert!(Profile::lab("http://[::1]:19067").is_ok());
        assert!(Profile::lab("https://zsa.methyl.cc").is_err());
        assert!(Profile::lab("http://zsa.methyl.cc:443").is_err());
        assert!(Profile::lab("http://10.0.0.5:9067").is_err());
        assert!(Profile::lab("http://127.0.0.1").is_err());
        assert!(Profile::lab("http://127.0.0.1:80").is_err());
    }

    #[test]
    fn identity_pins_fail_closed() {
        let public = Profile::public();
        let lab = Profile::lab("http://127.0.0.1:19067").unwrap();
        assert!(check_identity(&public, &identity(PUBLIC_BLOCK1_HASH)).is_ok());
        assert!(check_identity(&public, &identity(&"aa".repeat(32))).is_err());
        assert!(check_identity(&lab, &identity(&"aa".repeat(32))).is_ok());
        assert!(check_identity(&lab, &identity(PUBLIC_BLOCK1_HASH)).is_err());

        let mut wrong = identity(PUBLIC_BLOCK1_HASH);
        wrong.branch_id_hex = "88888888".into();
        assert!(check_identity(&public, &wrong).is_err());
        let mut wrong = identity(PUBLIC_BLOCK1_HASH);
        wrong.genesis_hash = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327".into();
        assert!(check_identity(&public, &wrong).is_err());
        let mut wrong = identity(PUBLIC_BLOCK1_HASH);
        wrong.node_subversion = "/Zebra:5.2.0/".into();
        assert!(check_identity(&public, &wrong).is_err());
    }

    #[test]
    fn roots_are_isolated_from_protected_paths() {
        assert!(root_path(ProfileKind::Lab, "/private/tmp/zwa-pub-lab-run1").is_ok());
        assert!(root_path(ProfileKind::Public, "/private/tmp/zwa-pub-public-a1").is_ok());
        for bad in [
            "/private/tmp/zwa4e-phase4e-b",
            "/private/tmp/zwa4c1-tx-tool",
            "/private/tmp/zwa-pub-lab-run1/../zwa4e-phase4e-b",
            "/tmp/zwa-pub-lab-run1",
            "/private/tmp/zwa-pub-lab-",
            "/private/tmp/zwa-pub-lab-a/b",
            "/private/tmp/zwa-pub-lab-a b",
        ] {
            assert!(root_path(ProfileKind::Lab, bad).is_err(), "{bad}");
        }
        // A lab root cannot be used with the public profile and vice versa.
        assert!(root_path(ProfileKind::Public, "/private/tmp/zwa-pub-lab-run1").is_err());
        assert!(root_path(ProfileKind::Lab, "/private/tmp/zwa-pub-public-a1").is_err());
    }

    #[test]
    fn asset_names_are_marked_experimental() {
        assert!(check_asset_name("ZWA-TESTNET-GOLD-REF-20261012").is_ok());
        assert!(check_asset_name("GOLD").is_err());
        assert!(check_asset_name("ZWA-TESTNET-").is_err());
        assert!(check_asset_name("ZWA-TESTNET-gold").is_err());
        assert!(check_asset_name(&format!("ZWA-TESTNET-{}", "A".repeat(60))).is_err());
    }
}
