//! Same adapter as r2b/src/circuit/gadget.rs. The only change is the
//! add_chip path: L1 Orchard cf801a5 instead of d91aaf14.
#![allow(dead_code, unused_imports, clippy::all)]

use ff::Field;
use halo2_proofs::{
    circuit::{AssignedCell, Chip, Layouter},
    plonk,
};

#[path = "../../orchard-src/circuit/gadget/add_chip.rs"]
pub(in crate::circuit) mod add_chip;

/// Restated from pinned gadget.rs so add_chip's `super::AddInstruction` resolves.
pub(in crate::circuit) trait AddInstruction<F: Field>: Chip<F> {
    fn add(
        &self,
        layouter: impl Layouter<F>,
        a: &AssignedCell<F, F>,
        b: &AssignedCell<F, F>,
    ) -> Result<AssignedCell<F, F>, plonk::Error>;
}
