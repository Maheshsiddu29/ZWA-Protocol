# Phase 1 validation record

Run at verification commit `7cbfd75` in a clean detached worktree. Machine-readable summary: `phase-1-result.json`.

| File | Content |
|---|---|
| `check-summary.txt` | verdict lines of `lineage/scripts/check.sh` (fixture pins, fmt, clippy, tests, lock) |
| `differential.jsonl` | Design-A differential against the live frozen research verifier and recorded rows |
| `measure_n3.json`, `measure_n3.rss.txt` | N = 3 timings (release, 10 runs) and process max RSS |
| `frozen_identity.txt` | VK\* and parameter identity recomputed from the frozen code |
| `protocol-core.log` | existing protocol-core checks at the same commit |
