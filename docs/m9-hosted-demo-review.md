# M9 hosted judge demo: local acceptance and release review

**Verdict: PARTIAL.** The isolated local Compose judge sandbox passed its 51/51 HTTP acceptance assertions, including three real experimental regtest settlements. The hosted demo is **not ready for public exposure**: no browser-initiated purchase was completed through the intended HTTPS judge environment, and no public-host security review or owner deployment approval has occurred.

## Scope and provenance

- Branch: `integration/hosted-judge-demo`; pre-review HEAD: `50a4733c3e418a06dae5826e9cfe6cc5ac9b61fb`. The existing uncommitted integration work was preserved and completed locally. This report and local commits are the M9 continuation; nothing was pushed or deployed.
- Protected Phase 4E root `/private/tmp/zwa4e-phase4e-b` was not used. The Compose stack uses fresh disposable regtest keys, chain, ledger and wallet volume. It is separate from the earlier human-wallet flow and from any public Community ZSA Testnet work.
- Source acceptance record: `/private/tmp/claude-501/-Users-mahesh-Desktop-Colosseum-ZWA-ZWA-Brand-Integration/e138851b-60d1-4ad1-acd3-f14c3e414b2c/scratchpad/accept-docker.log`. It ends with `51/51 checks passed` and `exit=0`; the assertions themselves were inspected.

## Acceptance evidence

| Area | Result and evidence |
|---|---|
| Startup and readiness | PASS locally. Zebra, portal, order service, demo signer, web and Caddy ran in Compose; portal `/readyz` reported `ready: true`, chain height 340 and `CUSTODIAL_DEMO_POLICY`. Next.js pages and the market API responded. SQLite `integrity_check` returned `ok`; schema version 4. |
| Demo sessions and wallets | PASS. Judge A and B received distinct funded wallets, private homes and session-scoped pairing. Ended sessions could no longer act; repeated signup was rate-limited. |
| Inventory and listings | PASS after restart fix. The live market showed gold 898, treasury 899 and credit 899 available units, each verified by a dealer scan at tip 340. A persisted scan at orphaned height 438 had hidden this inventory; the lookup now selects scans at or below the current tip, and the dealer rescans when the tip hash changes. Two regression tests cover this. |
| Orders and authorization | PASS for the HTTP judge flow. Acceptance checked exact quotes, idempotency, explicit digest confirmation, per-order limits, CSRF, Origin, cookie tampering, role checks, cross-session denial, forged client fields, dealer pause and quote expiry. Groth16 authorization and both restricted policy signers were asserted. |
| Atomic settlement | PASS on the isolated experimental regtest chain. Judge A bought 2 gold units for 2,000 zat in block 216; judge B bought 1 treasury unit for 1,250 zat in block 217. A post-restart order bought 1 credit unit for 850 zat in block 328. Independent RPC inclusion and block hashes, approval evidence, receipt files and reconciled buyer/dealer scans were inspected. The three orders are `RECEIPT_AVAILABLE`; buyer asset deltas are +2/+1/+1 and dealer deltas -2/-1/-1 with matching native proceeds. |
| Durability and failures | PASS in acceptance. Service kill/restart completed an in-flight order; Zebra outage refused purchases and recovered; prior receipts survived. A paused dealer failed closed, and expired quotes released reservations. |
| Browser review | PARTIAL. In a read-only local HTTP preview, the homepage and Markets page rendered and the latter displayed 898/899/899 units at block 340. The intended local HTTPS URL uses an untrusted local Caddy certificate in the in-app browser; its certificate warning was not bypassed. No browser purchase was performed. |

These results prove the **custodial judge sandbox on an isolated experimental regtest chain**. They do not prove a human-wallet transaction, public Community ZSA Testnet atomic swap, mainnet behavior, production custody security or purchased-note Halo2 lineage. Groth16 issuer/eligibility authorization is supported; rooted lineage for a purchased note remains `UNSUPPORTED`.

## Validation performed

- Root `npm ci` from the new root `package-lock.json`, followed by `npm test`: **15/15 pass**. `circomlibjs` was already declared; the missing lockfile and local installation caused the earlier failure. No sibling `node_modules` was used.
- Python `unittest discover -s exchange -p 'test_*.py'`: **101 pass** after the restart regression was added. `python3 -m unittest checkout.test_checkout`: **12 pass**.
- `cargo test --offline --locked`, `cargo build --offline --locked`, `cargo fmt --all -- --check`, `cargo clippy --offline --locked --all-targets -- -D warnings`: **pass**. `lineage/scripts/check.sh` passed fixture pins, formatting, strict clippy, Rust tests and doctests; it emitted pre-existing duplicate-package/unused-patch warnings.
- Website `npm run lint` (TypeScript) and `npm run build`: **pass**. Engine and web production Docker builds passed. Compose config and deployment preflight passed.
- Actual settled-order proof vectors on the macOS rehearsal root: **1 positive and 11 negative pass**, including invalid Groth16, forged issuer, revoked/stale root, replayed digest and tampered payment, asset and receiver. The proof is checked before restricted signing. No adversarial assertion was removed or mocked.
- `git diff --check` passed. Source, relevant untracked files, deployment templates, generated website artifacts and recent container logs were scanned for local secrets and private-key markers; no match was found. Secret values were not printed.
- Dependency audit: root and proof-runtime locks had **0 high/critical, 15 low** advisories in the `elliptic` transitive chain; the website had **0 findings**. Root and proof-runtime overrides pin patched `underscore` 1.13.8 and `ws` 8.21.0 without changing direct cryptographic package versions. No patched `elliptic` release was available in the checked advisory. A later repeat of the root npm audit was rejected by automatic approval review because it would send lockfile dependency metadata to npm; the prior successful audit result is the evidence used here.

## Deployment and security review

The stack persists the Zebra state, the order ledger/evidence and disposable wallet homes in named volumes. Zebra RPC, portal and signer have no published ports; only Caddy publishes ports. Caddy defaults to **127.0.0.1** for safe rehearsal. Engine containers drop capabilities, use read-only filesystems and no-new-privileges; Linux Landlock confines wallet operations and refuses startup when unavailable. The public Next.js layer has session/role checks, CSRF and Origin checks, fixed portal actions, bounded request bodies, rate limits and expiration. The portal requires a private bearer token. The service limits wallet count, order size and sponsor use. Recovery and paired-volume backup instructions are in [`deploy/README.md`](../deploy/README.md).

Residual risks: the judge mode is deliberately **custodial** and holds disposable private keys on the host; use an encrypted disk and limit operator access. The local acceptance does not establish internet-facing security. A real domain and trusted TLS certificate, VM firewall review, backup/restore drill, monitoring, external security review and browser transaction are still required. The proof-runtime transitive `elliptic` advisories remain low severity. The pinned Zebra image is `linux/arm64`; an amd64 host needs a separately pinned rebuild.

## Owner deployment plan and approvals

Estimated host: **arm64, 4 vCPU, 8 GiB RAM, 60 GiB encrypted SSD**, Docker Engine 25+ with Compose v2 and Linux Landlock. The pinned Zebra image is about 8.5 GiB; total images about 10 GiB. Expect one dealer settlement at a time, about 35 seconds each, and roughly 110 added blocks per purchase. Budget estimate in the deploy guide is US$30–130/month, provider dependent.

The exact build, image-transfer, parameter verification, Compose bootstrap, health, backup and restore commands are in [`deploy/README.md`](../deploy/README.md#exact-commands-owner-review). Before any public step, the owner must approve the provider and cost, domain/DNS, public 80/443 exposure and firewall, custodial key handling, certificate/TLS plan, backup/restore plan and a security review. Only after those approvals should the owner set `ZWA_BIND_ADDRESS=0.0.0.0`. Then run the documented deployment commands on a clean Linux host, inspect `/readyz`, and perform a **browser-initiated** judge purchase through the trusted HTTPS domain, verifying its receipt, independent RPC inclusion and reconciled portfolio.

No public DNS, host, service or wallet key was provisioned or published in this review. No public testnet broadcast, push, merge or pull request was made.
