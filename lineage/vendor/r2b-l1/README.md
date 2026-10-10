# Frozen R2B-L1 source package

This package is a local source packaging of `ZWA-Lineage-Research/r2c-h1d/r2b-l1` at research commit `3e7115d0e4c2f1cf36355f028447fc1b58290583`. It retains the frozen circuit, witness mapping, public inputs, prover, VK derivation, parameters, and transcript code. The separate package name `zwa-lineage-r2b-l1-local` lets differential tests link the original research crate and this local copy in one process.

All imported `.rs` files and their SHA-256 hashes are listed in `SOURCE_MANIFEST.json`. Source files are byte-identical to research except the seven `#[path]` directives in `circuit.rs`, `circuit/gadget.rs`, `constants.rs`, and `spec.rs`. Those directives now point to exact packaged copies of the referenced Orchard cf801a5 sources. The Orchard files are byte-identical to commit `cf801a5d6701bc128e21678ba0034564af4a2aca`. `lineage/scripts/verify-r2b-l1-source.py` checks all local hashes and, when originals are present, confirms the path-only transformation and upstream bytes.

The original research harness binary was excluded from this production library package; it is not part of the verification path. Original R2B-L1 source authorship belongs to the ZWA lineage research team. Orchard source is licensed under the notices in `orchard-licenses/`. No claim of independent human security review is made.
