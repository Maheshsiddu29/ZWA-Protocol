//! Circuit module. Paths point at Orchard cf801a5, not the old d91aaf14 pin.
#![allow(dead_code, unused_imports, clippy::all)]

#[path = "../orchard-src/circuit/note_commit.rs"]
#[allow(dead_code, unused_imports, clippy::all)]
pub(crate) mod note_commit;

#[path = "../orchard-src/circuit/derive_nullifier.rs"]
#[allow(dead_code, unused_imports, clippy::all)]
pub(crate) mod derive_nullifier;

pub(crate) mod gadget;
pub mod lineage;
