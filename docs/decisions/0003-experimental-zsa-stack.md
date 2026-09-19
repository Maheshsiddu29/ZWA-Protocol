# ADR 0003: Experimental ZSA settlement stack

- **Status:** Accepted for Colosseum prototype only
- **Decision:** Use the exact pinned QEDIT experimental stack validated in Phase 0 for the single demo settlement.

## Context

The prototype requires shielded custom assets, multi-party atomic exchange, and a shielded native ZEC matcher payment. Phase 0 reproduced these capabilities locally on mutually compatible experimental branches.

## Pins

- `zcash_tx_tool` `217b979ee01afb844190a162fb77874135aef587`
- Zebra `0aef55cea41b83f17610e6ea708e59995f9e739f`
- librustzcash `5a55da948498dd0995d0f438b4c8e9a3f0150154`
- Orchard `d91aaf146364a06de1653e64f93b56fac5b3ca0f`

## Consequences

The demo can build on observed atomic settlement behavior, but these features are not generally available production Zcash mainnet capabilities. Every integration must preserve the pins and reproduce tests. The project must not claim production readiness, mainnet deployment, stable APIs, or an audited production setup.
