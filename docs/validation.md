# Phase 0H Validation

Validation was performed on 2026-09-18 without creating a commit or writing generated circuit artifacts into the repository.

## Results

- JavaScript schema/adversarial tests: 8 passed, 0 failed.
- Provenance circuit: compiled with Circom 2.2.2 to a temporary directory; 8,837 constraints, 2 public inputs, 21 private inputs.
- Eligibility circuit: compiled with Circom 2.2.2 to a temporary directory; 13,502 constraints, 2 public inputs, 34 private inputs.
- Migration manifest: all 15 source and destination SHA-256 records recomputed with 0 mismatches.
- Semantic-preservation comparison: both migrated circuit files equal their Phase 0 sources after applying only the documented import substitutions.
- Phase 0F integrity manifest: every entry passed `shasum -a 256 -c`.
- Phase 0G integrity manifest: every entry passed `shasum -a 256 -c`.
- ZK-ORIGIN source: clean at `eae3f3e19dd63562364149ecb000f2de7296d025`.
- Generated/state-file scan: no `node_modules`, `target`, R1CS, witness, WASM, proving key, Powers of Tau, wallet/database, log, or `fetch-params.sh` file was found in the product tree.
- Large-file scan: no file exceeds 1 MiB.
- Secret-material scan: no PEM/private-key marker, seed phrase, mnemonic, spending key, or secret-key pattern was found. Fixture fields are deterministic synthetic witness data, not real credentials or keys.

The ignored macOS `.DS_Store` may be recreated by Finder; it is not tracked or part of the product source.
