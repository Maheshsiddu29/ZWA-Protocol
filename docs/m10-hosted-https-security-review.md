# M10 trusted HTTPS and deployment security review

**Verdict: PARTIAL.** Trusted local HTTPS, a funded normal-browser wallet,
two-context browser boundaries, and a stopped-service backup/restore drill were
verified. A new browser purchase and its receipt remain unverified because the
final confirmation is a financial transaction requiring user handoff. Nothing
was exposed publicly or copied from the protected Phase 4E environment.

## Browser and TLS evidence

- Caddy served `https://localhost:8443` on loopback using its local CA. The
  public root certificate was installed in the macOS login trust store after
  the operator approved the keychain prompt. `curl` returned HTTP 200 with
  `ssl_verify_result=0`; matching-host OpenSSL validation passed and wrong-host
  validation failed with code 62. The normal in-app browser opened without a
  certificate warning. No TLS bypass flag or disabled verification was used.
- In the normal browser, **Try Demo** created a new disposable wallet. The
  wallet showed READY, 6.25 test ZEC, a funding transaction, and a canonical
  scan at block 450. The market displayed real dealer scans: 898 gold, 899
  treasury, and 899 credit units. The gold asset page quoted 1,000 zatoshis
  per unit, with the exchange paying the network fee. No M10 browser purchase
  was submitted; no M10 txid, block, receipt, or post-purchase balance delta
  can be claimed.
- `tests/acceptance/browser_security.cjs` used two separate Chrome contexts
  with certificate validation enabled against an isolated, loopback-only
  restore project. It passed distinct wallet/funding identities; Secure,
  HttpOnly, SameSite=Strict cookies; missing CSRF, forged Origin and Host
  rejection; ignored client-supplied wallet/buyer identity; cross-session
  order read/cancel denial; buyer/admin role denial; idempotent order creation;
  invalid quote-digest rejection; private-route denial; no obvious secret in
  the rendered wallet page; and loss of authorization after session end.
  Caddy rejected a forged Host at the TLS layer. An initial run against the
  original project hit its real three-signups-per-client/hour cap. The passing
  run used the separately restored disposable fixture with its admission
  history reset; this did not modify the original service or production code.

## Recovery finding and drill

The first stopped-service snapshot restored the ledger and receipts but Zebra
recovered from height 342 to 339. Three recent funding transactions were no
longer on the recovered chain while their wallets still said READY. That is a
real balance/identity failure. The fix makes FUNDING remain pending until its
block is below a durable checkpoint, changes lost funding to FAILED and
revokes pairing, and rescans surviving READY wallets when their prior scan is
orphaned. Transient RPC failures do not themselves revoke a wallet. Recovery
tests cover lost READY and FUNDING transactions, a surviving rescan, and the
durable funding anchor.

After the fix, a new disposable wallet became READY with funding at height
340 and a durable anchor at 350. A further rollback demonstrated that its
funding survived but its scan needed refresh. The final drill stopped the
source services, archived the paired `zwa-root`, `zebra-state`, and
`zwa-params` volumes, and restored to an isolated project without published
ports. A read-only report compared source and restore at height 351:
genesis, ledger integrity, all order states, three completed receipts and
settlements, two active buyer balances/holdings, dealer balance/holdings, and
offer counts matched exactly. There was no new settlement; an interrupted
unconfirmed quote expired without signing. No backup keys or wallet homes
were committed. The drill did not exercise live, online snapshots.

## Public-host threat model

The internet-facing trust boundary is Caddy → Next.js. Caddy publishes only
80/443; Zebra RPC, portal, order service, and signer have no published ports.
The browser test received no usable `/rpc`, `/v1/portal`, or `/api/sign` route.
The portal accepts a fixed action set behind a private bearer token. The web
service has no wallet volume and derives actor, role, and session from its
signed cookie. Private mutations check Origin, Host, CSRF, and bounded body
size. Browser-supplied account/wallet fields do not grant another identity.
The signer receives structured exact terms, checks the recorded quote,
browser confirmation, limits, recipient, fee and tip, then uses confined
disposable wallet homes. The engine refuses unconfined startup on Linux.

Residual deployment risks requiring owner review:

- The hosted judge mode is custodial. A host compromise or stolen `zwa-root`
  backup reveals disposable wallet keys. Encrypt the disk and backups, restrict
  host/operator access, rotate separate portal/session secrets, and use only
  fresh hosted identities. This review did not test operator compromise.
- Client signup (3/hour), global signup (60/hour), wallet (40), order (5),
  size/value, and sponsor (60/hour) caps bound obvious abuse. In-memory web
  login throttling is per process and resets on restart; the persisted engine
  admission limits remain authoritative. Public traffic/load and distributed
  abuse were not exercised. Add external monitoring and resource alarms.
- SQLite order and offer transactions reserve inventory atomically. Python
  concurrency tests confirm reservations cannot oversell and parallel
  duplicate confirmations create one authorization event. Browser tests
  confirm order idempotency and invalid quote rejection. Existing M9 acceptance
  proved three genuine regtest settlements and proof/signing checks. No new M10
  browser settlement or fresh positive Groth16 vector occurred.
- Logs are structured and size-rotated; static scans of changed source found
  no committed credential material. Runtime log/backup access is still a host
  operator responsibility. A public reverse-proxy scan and external security
  review remain open before internet exposure.
- The M9 dependency audit found **15 low-severity advisories**, with zero
  high/critical, in the transitive `circomlibjs → ethers →
  @ethersproject/signing-key → elliptic@6.6.1` path in the root and proof
  runtime locks; the website had zero findings. Local `npm ls` reconfirmed the
  package path without network access. The vulnerable library is present in
  the proof runtime image and cannot be called directly from a public route;
  the fixed-action proof path may still use it transitively, so exploitability
  is **not ruled out**. No new egressing npm audit was run. Track a compatible
  upstream fix and retest proofs before updating cryptographic packages.

## ARM64 and operations readiness

The pinned Zebra image is `linux/arm64`. At idle, the local six services used
about 161 MiB combined (Zebra 25, order 17, signer 15, portal 16, web 69,
Caddy 20 MiB), but the sum of Compose memory ceilings is **9.5 GiB**. The
proposed 4-vCPU/8-GiB/60-GiB encrypted-SSD VM is a planning baseline, not a
validated peak-load minimum. Allow space for images (Docker reports the Zebra
image as 8.53 GB), chain growth, logs, and at least one paired backup. A
larger-memory or load-tested host may be necessary. The local engine source
tests and website production build passed; a full offline engine Docker build
could not finish because its builder lacked cached apt packages/clang. A
temporary image overlay of the changed Python source was used for the local
drill. Rebuild the final engine image on the approved host/build machine before
any deployment.

`deploy/README.md` contains prepared build, fresh bootstrap, health,
paired-volume backup/restore, rollback, and shutdown steps. Provisioning,
public firewall/DNS/TLS, production startup, and cloud backup scheduling were
not executed. Keep `ZWA_BIND_ADDRESS` on loopback until the owner approves the
host, cost, domain, firewall, custodial key handling, certificate plan,
backup destination, and public security review.

## Validation

- PASS: two-context Chrome security script over trusted HTTPS (13 assertions
  reported as PASS); local TLS hostname checks; Compose config/preflight;
  stopped-service restore comparison.
- PASS: 105 exchange Python tests, 12 checkout Python tests, 15 root npm tests,
  Rust offline locked tests, `cargo fmt`, strict `cargo clippy`, website
  typecheck and production build, JavaScript/Python syntax, `git diff --check`.
- PARTIAL: M9's 51/51 Compose API acceptance and 1 positive/11 negative
  Groth16 vectors remain prior evidence; they were not rerun as a full M10
  settlement. No new external dependency audit was performed.
- BLOCKED: the final browser confirmation and therefore new transaction,
  inclusion block, reconciled post-purchase balances, and refreshed receipt.

The experimental regtest and custodial judge proof do not establish public
Community ZSA Testnet or mainnet behavior, Zcash consensus enforcement of ZWA
rules, mandatory consensus fees, or full recursive ownership lineage.
