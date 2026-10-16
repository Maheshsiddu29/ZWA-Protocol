# ZWA judge demo guide

**Live demo:** https://zwaprotocol.vercel.app

This guide walks you through one real purchase on the ZWA demo exchange and
explains, in plain language, what each step does and what it proves. You need
only a web browser. Allow a few minutes for settlement after you confirm.

> **Experimental.** The demo runs on experimental Zcash Shielded Assets (ZSA)
> software on a private test chain operated by the project. The assets and ZEC
> have no value, the demo wallet is held by the server, and nothing here is
> production ready or runs on Zcash mainnet.

Contents: [1. What ZWA solves](#1-what-zwa-solves) ·
[2. Current demo limits](#2-current-demo-limits) ·
[3. Walkthrough](#3-walkthrough) ·
[4. The custodial demo wallet](#4-the-custodial-demo-wallet) ·
[5. The issuer and eligibility proof gate](#5-the-issuer-and-eligibility-proof-gate) ·
[6. The atomic swap](#6-the-atomic-swap-zsa-for-zec) ·
[7. Reconciliation](#7-how-settlement-is-reconciled) ·
[8. The receipt](#8-what-the-receipt-proves) ·
[9. Privacy](#9-what-shielded-transactions-hide-and-what-they-do-not) ·
[10. Public testnet](#10-the-separate-public-testnet-transfer) ·
[11. Common problems](#11-common-problems) ·
[12. Limitations](#12-experimental-regtest-limitations)

## 1. What ZWA solves

Tokenized real-world assets (a bond-like unit, a gold certificate, a credit
participation) are usually traded on public ledgers where everyone can see who
holds what. Regulated assets also need rules: the buyer must know the asset was
issued by an authorized issuer, and the issuer must know the buyer is allowed
to hold it.

ZWA combines three things on Zcash Shielded Assets:

- **Private holdings.** Assets live in Zcash's shielded pool, so balances and
  transfers are not published in readable form.
- **Private rule checks.** A zero-knowledge proof shows that the asset comes
  from an authorized issuance and that the buyer is eligible, without
  revealing the buyer's identity attributes.
- **Atomic settlement.** The asset and the payment move in one transaction:
  either both happen or neither does, with no trusted escrow holding funds in
  between.

In the demo you buy one unit of a test asset from a demo dealer and pay in
test ZEC.

## 2. Current demo limits

These are the limits deployed today. They may be changed later; the site
always applies whatever is current.

| Limit | Value | What you notice |
|---|---|---|
| Quote validity | **90 seconds** | Confirm within 90 seconds of seeing the quote, or it expires |
| New demo wallets per client | **3 per hour** | Visitors who arrive through the Vercel site can share one allowance, so a launch may be refused until the hour rolls over |
| New demo wallets, whole exchange | 60 per hour, 40 active at once | A message that the demo is at capacity |
| Demo wallet lifetime | 2 hours | The wallet page shows when the session ends |
| Purchases per demo wallet | 5 | Reuse your wallet instead of launching a new one |
| Units per purchase | up to 5 (payment up to 1,000,000 zatoshis = 0.01 test ZEC) | Quantity selector stops at the limit |
| Sponsored purchases, whole exchange | 60 per hour | A message that the hourly settlement budget is used up |
| Concurrency | one purchase at a time across all judges | Your quote may wait while another judge's purchase holds the dealer wallet |

The exchange pays the network fee for every purchase.

## 3. Walkthrough

1. **Click Launch demo.** The exchange creates a new demo wallet for you.
2. **Wait for the wallet to show Ready.** You see three steps: *Wallet
   created*, *Funded with test ZEC*, *Ready to trade*. Funding usually takes
   under ten seconds, longer if another purchase is settling.
3. **Click Browse assets and select an asset.** There are three test
   instruments: ZWA Gold Reference Unit, ZWA Treasury-Style Unit and ZWA Credit
   Participation Unit. Each shows live available units and a fixed test price.
4. **Choose 1 unit and click Buy · get exact quote.** The exchange records
   your order, reserves the unit and prepares an exact quote.
5. **Review the terms, tick "I confirm these exact terms", and click Confirm
   purchase** before the countdown reaches zero. The quote shows what you pay,
   what you receive, the price per unit, that the exchange pays the network
   fee, and when the quote expires.
6. **Wait for Settlement verified.** The progress panel moves through these
   stages:

   | Stage | What happens |
   |---|---|
   | Order accepted | The order is recorded and the units are reserved |
   | Exact quote ready | Price, quantity and chain heights are fixed in one digest |
   | You authorized the terms | Your confirmation of that exact digest |
   | Eligibility proof verified | The Groth16 issuer and eligibility proof is generated and checked ([section 5](#5-the-issuer-and-eligibility-proof-gate)) |
   | Signing and proving | Restricted signers build their parts of the transaction |
   | Buyer wallet signed / Dealer wallet signed | Each side signs only its own part ([section 6](#6-the-atomic-swap-zsa-for-zec)) |
   | Transaction submitted | The atomic swap is sent to the test chain |
   | Included in a block | The node returns the transaction in a block |
   | Balances reconciled | Both wallets are rescanned and checked against the quote ([section 7](#7-how-settlement-is-reconciled)) |
   | Receipt verified on chain | The receipt matches the block the node serves now |
   | Durable | Extra blocks are mined so the settlement survives a node restart |

   "Included and reconciled" with a *Confirming durability* badge means the
   swap is done and the extra blocks are still being mined.
7. **Open View portfolio** to see the purchased units in your wallet, scanned
   with the wallet's own keys.
8. **Click Verify this transaction.** On the Vercel site this page explains
   that the lookup is served by the exchange host and offers **Check this
   transaction on the exchange host ↗**. That page shows whether the
   transaction is **Included**, its block, block hash, size and
   confirmations. It shows only public chain facts: no amounts, parties or
   order details.

Open **Technical details** on the order page to see the order ID, AssetBase,
quote digest, chain heights, the individual proof verdicts, the trade
commitment and the block hash.

## 4. The custodial demo wallet

A normal Zcash user holds their own keys in a wallet app. To let judges try
the exchange without installing anything, the demo uses a **custodial** wallet:

- When you click **Launch demo**, the exchange creates a fresh Zcash shielded
  wallet for your session, funds it with test ZEC in a block of its own, and
  scans the chain to confirm the balance. The wallet page shows its address,
  funding transaction and balance.
- **The server holds the key.** Your browser holds only a session cookie.
- The key is used by a **restricted demo signer**. It signs only purchases you
  confirmed, only for the exact confirmed terms (this asset, this quantity,
  this payment to the demo dealer, before the quote expires), and only within
  the demo limits. Each wallet runs isolated from the others on the server.
- The dealer's wallet is also held by the exchange. It signs sales under a
  standing dealer policy, not by a person approving each sale.
- **End session and delete wallet** retires the wallet. Wallets also expire
  after 2 hours.

This is a demo convenience, not the intended custody model. Elsewhere in the
project, buyer and seller approve trades in their own wallet companions; that
mode is not part of the hosted judge demo.

## 5. The issuer and eligibility proof gate

After you confirm, and **before anything is signed**, the exchange generates
one **Groth16 zero-knowledge proof** and verifies it together with the
authorities' signed roots. A Groth16 proof convinces a verifier that a
statement is true without revealing the private inputs behind it. The check
reports five verdicts:

- **Issuer authentication:** the issuance root was signed by the recognized
  issuer authority.
- **Issuance provenance:** the exact asset type you are buying (its 32-byte
  AssetBase) is a member of that authorized issuance root.
- **Credential authority authentication:** the credential root was signed by
  the recognized credential authority.
- **Investor eligibility:** a credential in that root approves your wallet's
  exact receiving address, and its private attributes (investor class and
  jurisdiction) satisfy the policy. The attributes themselves stay private.
- **Exact trade authorization:** all of this is bound to this quote's digest
  and expiry height through a trade commitment, so the proof cannot be reused
  for a different asset, buyer address, amount or quote.

The order page lists these verdicts under **Technical details**. If the proof
cannot be generated or is rejected, the order stops as **Not executed** and
nothing is signed or transferred.

**What it does not show.**

- In the demo, the issuer and credential authorities are throw-away test
  authorities run by the exchange. For each purchase the demo creates a test
  credential for your demo wallet's address. The demo shows that a purchase
  cannot proceed without a valid proof bound to the exact trade; it does not
  perform a real identity or investor check.
- The proving key is a local development key, not the output of a production
  trusted setup.
- The proof does not show that you control the receiving address (your wallet
  signer checks its own address separately).
- The proof does not show where the specific purchased note came from. ZWA
  has a separate Halo2 note-lineage verifier, but it is **not** applied to the
  purchased note; the order page says "Note lineage: Not proven for the
  purchased note".

## 6. The atomic swap (ZSA for ZEC)

The settlement is one Zcash transaction that does both legs at once:

- your demo wallet pays test ZEC to the dealer, and
- the dealer's wallet sends the asset units to your demo wallet.

Each side builds and signs **only its own part** (an Action Group in the
experimental ZSA swap format) after checking that its own part matches the
confirmed terms. A coordinator combines the separately signed parts, plus an
input from the exchange that pays the network fee, into a single transaction
and submits it. The transaction is valid only as a whole: it cannot be cut
into a "payment without delivery" or a "delivery without payment".

On the tested experimental software, an Action Group signature does not cover
every field of the final transaction, so the coordinator also checks the
assembled transaction. Each approval is one-shot for one exact quote; there
are no standing orders.

## 7. How settlement is reconciled

A transaction being in a block is not, by itself, enough for the exchange to
report success. After inclusion, the exchange rescans both wallets with their
own keys and requires the changes to equal the quote exactly:

- your wallet: **+ the quantity** of the asset;
- the dealer: **− the same quantity**, and **+ exactly the quoted payment** in
  ZEC.

Only then does the order reach *Balances reconciled*. If the rescans do not
match, the order is **not** reported as settled. After reconciliation the
reservation is released and the dealer's sold count is updated, which you can
see in the live inventory.

## 8. What the receipt proves

The receipt on the order page shows what you received and paid, your wallet
change from the rescans, the transaction ID, the block and confirmations, the
network, who signed (*Demo wallet policy signer (server-held)*) and
durability.

**Settlement verified** appears only when all of these hold:

- the order completed the full path through inclusion and reconciliation;
- the reconciled wallet changes are recorded;
- the exchange has just re-read the receipt's block from the node and it
  still matches;
- the durability mark exists: 110 extra blocks were mined after the swap so
  the settlement survives a node restart under the pinned node's policy.

**What it does not prove.**

- **Not public-network finality.** The chain is a private test chain run by
  the exchange operator, so confirmations count blocks on that chain only.
- **Not an independent audit.** The exchange checks its own node and its own
  wallets; a judge is trusting the operator's infrastructure for these checks.
- **Not note lineage, and not real-world backing.** The receipt does not
  prove where the purchased note came from (section 5) or that any
  real-world asset backs the test units.

## 9. What shielded transactions hide and what they do not

A real demo swap was inspected as a public observer would see it: transaction
`af9a6bdc99cc2d169160274d77fbb5cb72cf7ff914c9540d73416c745ecf1453` in block
892 of the demo chain, observed on 2026-10-15. Its raw bytes were read from
the demo chain's node and parsed field by field without any wallet keys. It
has three Action Groups of two Actions each, one transparent input and no
transparent outputs.

**Hidden in the transaction itself:** buyer and seller shielded addresses,
which asset was traded, how many units, and how much ZEC was paid. Notes are
encrypted, and commitments and nullifiers do not reveal their contents
without the wallet keys.

**Visible to anyone:** that a transaction exists, its block and time, its size
and unusual shape (several Action Groups), the transparent input the exchange
used to pay the fee, and the fee itself (5,000 zatoshis).

**Not anonymity.** Outside information can link a transaction to a trade: the
public market (asset names, prices and available units), inventory changes
right after a block, timing, and any receipt or order page you share. ZWA
does not claim anonymity against an observer who has these clues. The
exchange operator also sees order details, because it runs the demo wallets.

## 10. The separate public testnet transfer

The live demo runs on the project's **private regtest chain**. Separately, ZWA
issued a ZSA and made one shielded transfer on the public **Community ZSA
Testnet**:

| Action | Transaction | Block |
|---|---|---|
| Issuance of 1,000,000 units | `dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340` | 202,121 |
| Shielded transfer | `9c0cfc6d475f165ce0b491b5b793c869b38e5af5a50fc6c7ecde58d711cf0e77` | 202,135 |

These appear under **Public testnet records** on the `/verify` page. That
network accepts one Action Group per transaction, so **no atomic swap has
been performed on a public network**. The swap you make in the demo is a
regtest swap only. Reports:
[issuance](public-zsa-testnet-phase3b-issuance-receipt.md),
[transfer](public-zsa-testnet-phase3c-transfer-execution.md).

## 11. Common problems

| What you see | What it means | What to do |
|---|---|---|
| "Too many demo sessions were started. Try again in a minute." | Short burst limit on starting demos | Wait a minute and try again |
| A message that too many demo sessions were started this hour | The limit of 3 new wallets per client per hour is used up. Visitors through the Vercel site can share it | Reuse an existing wallet if you have one; otherwise try again later in the hour |
| A message that the demo is at capacity | 40 wallets are active or 60 were created this hour | Try again in a few minutes |
| Wallet stays on *Setting up* | Funding waits while another purchase's block is mined | Wait; the page updates by itself |
| "Queued: another live quote is using these wallets." | Another judge's purchase holds the single dealer wallet | Wait; your quote follows. This can take a few minutes |
| "Queued: a new demo wallet is being funded. Your quote follows." | Funding and quoting share the chain | Wait |
| **Confirm purchase** is greyed out | The confirmation box is not ticked, or the quote expired | Tick "I confirm these exact terms"; if expired, buy again |
| "The quote expired before it was confirmed." | More than 90 seconds passed | Units are released; start a new purchase and confirm promptly |
| "The chain moved on before … Start a new order." | The quote was tied to chain heights that passed | Start a new purchase; nothing was signed |
| "You have an order in progress. Finish or cancel it first." | One open order per wallet | Open it from the wallet page (*Order in progress →*) |
| "We could not confirm whether the exchange accepted your purchase…" | The answer was lost in transit | Use **Check again (same key)**; it never creates a second order |
| **Held for review** / *Unresolved* | The outcome must be checked against the chain | Do not buy again to fix it; the operator reconciles it. Note the order ID |
| A message that this demo wallet reached its purchase limit | 5 purchases used | Launch a new demo (counts toward the hourly limit) |
| A message that the demo's hourly settlement budget is used up | 60 sponsored purchases this hour | Try again later |
| "Your demo session has ended." | The 2-hour wallet lifetime passed or the session was ended | **Start a new demo** |
| "The experimental chain is unavailable. Trading resumes when it is back." | The exchange host or its node is unreachable | Try again later; no purchase starts while it is down |
| Verify page links to the exchange host instead of showing a result | Expected on the Vercel site | Follow **Check this transaction on the exchange host ↗** |
| Block numbers jump by about 111 | Each wallet funding and each purchase mines one block plus 110 durability blocks | Expected |

## 12. Experimental regtest limitations

- **Experimental software.** ZSA is not active on Zcash mainnet. The demo uses
  QEDIT's experimental ZSA swap branch of the Zebra node (pinned) and
  experimental transaction tooling.
- **Private test chain.** One node run by the project, on a single host. There
  are no other miners, no public finality and no high availability. Test
  assets and test ZEC have no value.
- **Custodial demo.** The server holds the demo wallet and dealer keys and
  approves trades by policy. Independent-party custody and signing are not
  part of the hosted demo.
- **Test trust anchors.** Issuer and credential authorities are disposable test
  authorities, the Groth16 proving key is a development key, and eligibility
  credentials are created by the demo.
- **Rules are enforced by the exchange, not by Zcash consensus.** ZSA
  transfers outside ZWA are not restricted.
- **No integrated lineage.** The Halo2 rooted note-lineage verifier exists as
  a separate component and is not applied to the purchased note.
- **Test prices.** One demo dealer sells at fixed, operator-set test prices,
  not market quotes.
- **Not production ready.** No production security review, key management,
  monitoring or recovery is claimed.

## Further reading

[README](../README.md) · [architecture](architecture.md) ·
[trust model](trust-model.md) · [threat model](threat-model.md) ·
[security claims](security-claims.md) ·
[checkout authorization](../checkout/authorization/README.md) ·
[Vercel remote mode](vercel-native-exchange.md)
