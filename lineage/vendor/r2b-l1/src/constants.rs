//! L1 Orchard constants, compiled in place. The scalar constants below are
//! the same literals as orchard/src/constants.rs at cf801a5.
#![allow(dead_code, unused_imports, clippy::all)]

pub mod fixed_bases;
#[path = "../orchard-src/constants/nullifier_l.rs"]
pub mod nullifier_l;
#[path = "../orchard-src/constants/sinsemilla.rs"]
pub mod sinsemilla;
#[path = "../orchard-src/constants/util.rs"]
pub mod util;

pub use {
    self::sinsemilla::{OrchardCommitDomains, OrchardHashDomains},
    fixed_bases::{NullifierK, OrchardFixedBases, OrchardFixedBasesFull, ValueCommitV},
};

pub const T_P: u128 = 45560315531419706090280762371685220353;
pub const L_ORCHARD_BASE: usize = 255;
pub const L_ORCHARD_SCALAR: usize = 255;
pub const L_VALUE: usize = 64;
pub const KEY_DIVERSIFICATION_PERSONALIZATION: &str = "z.cash:Orchard-gd";
