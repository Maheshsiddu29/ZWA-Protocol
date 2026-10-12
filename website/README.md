# ZWA Protocol website (Phase 4 local integration)

This Next.js site is a local-only, review-stage redesign on `integration/phase4-brand-website`. It imports the team website subtree from `origin/fix/m3-remediation` onto the checkout/verifier backend HEAD and integrates selected official assets from the untracked `design-assets/` package. It does not publish, sign, purchase, or broadcast.

## Start

From this directory:

```sh
npm ci
npm run lint
npm run build
./node_modules/.bin/next start -H 127.0.0.1 -p 3210
```

For the live verifier demo, in a second terminal from the **original backend checkout**:

```sh
cd lineage
ZWA_CHAIN_VIEW=A7 ZWA_DEMO_ENABLED=true ./target/debug/zwa-lineage-api
```

For read-only checkout evidence, in a third terminal from the original backend checkout:

```sh
python3 -m checkout.api --demo --port 18745
```

The website proxies the verifier on `http://127.0.0.1:3000` and checkout on `http://127.0.0.1:18745`. Override only to another loopback HTTP origin with `ZWA_VERIFIER_URL` or `ZWA_CHECKOUT_URL` before starting Next.js. A local verifier binary must be built from `lineage/` if it is not already present; see `docs/phase3-api.md`. The checkout API can serve the recorded session without a running regtest node, but then live node availability is unavailable and recorded receipt finality is `UNKNOWN`.

## Scope and trust boundaries

- The verifier demo uses `/v1/demo/scenarios`, `/v1/demo/run`, and `/v1/lineage/verify` through explicit server routes. The browser cannot select arbitrary upstream paths.
- The checkout panel uses exactly four read-only GET paths. It has no buy, wallet, signer, or dispatch control.
- A `VERIFIED` verdict concerns the supported rooted-lineage relation in the configured pinned research view. The API separately returns investor eligibility `NOT_EVALUATED` and settlement `NOT_EXECUTED`.
- The recorded checkout receipt is historical disposable regtest evidence. Purchased-note rooted lineage is `UNSUPPORTED`, investor eligibility `NOT_EVALUATED`, and current finality `UNKNOWN` when the node is stopped.
- Source brand files stay in the original `design-assets/`; only selected production copies are in `public/`.

See `docs/ASSET-INVENTORY.md` for source-to-site mapping and missing video files.
