# Circuit Migration Record

The provenance and eligibility circuits preserve their Phase 0 constraints, public inputs, private witnesses, domain constants, integer ranges, Merkle depths, and Poseidon staging. Intentional changes are limited to include paths pointing at `circuits/shared/zk-origin` and the package-style `circomlib/circuits/bitify.circom` include. The eligibility JavaScript schema now imports the local frozen `trade-commitment-v1.js`; test files import their new locations. ZK-ORIGIN helper changes, where hashes differ, are dependency import relocation only.

Source and destination hashes for every migrated component are recorded in `migration-manifest.tsv`. The two synthetic JSON fixtures are byte-identical copies. No proof, witness, trusted-setup, wallet, database, log, or generated circuit output was migrated.
