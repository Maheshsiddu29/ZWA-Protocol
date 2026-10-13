"""Operator-side lifecycle of disposable judge wallets (custodial demo mode).

Run inside the order service tick. It never signs a trade:

  REQUESTED -> private home, seed and address              -> CREATED
  CREATED   -> fund from one mature sponsor coinbase        -> FUNDING
  FUNDING   -> RPC inclusion + independent wallet scans     -> READY (+ pairing)
  READY     -> lifetime ends, no settlement in progress     -> EXPIRED
  EXPIRED   -> grace period, no open order                  -> RETIRED (home deleted)

Every block this module produces (funding, sponsor top-up) is mined only
while no quote is live, because an exact quote is bound to the next height.
"""

from __future__ import annotations

import time

from .demo_ledger import DEALER_KEY, LIVE_WALLET_STATES, DemoLedger
from .order_ledger import FAILURE_STATES, PRE_EXECUTION_STATES, WALLET_COMMITTED_STATES, OrderError
from .tools import FUNDED_LINE, NATIVE_LINE, ToolError

RETIRE_GRACE_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 600
MAX_FUNDINGS_PER_TICK = 4
SCAN_LINE_PREFIX = "PHASE35F_BALANCES "


class DemoError(RuntimeError):
    pass


class DemoOperator:
    def __init__(self, service):
        self.service = service
        self.config = service.config
        self.policy = service.config.demo
        self.tools = service.tools
        self.ledger: DemoLedger = service.ledger
        self.log = service.log
        self._dealer_address: str | None = None

    # ------------------------------------------------------------ inspection

    def idle(self) -> bool:
        """No quote may be live and no settlement may be in flight."""
        return not self.ledger.orders_in(WALLET_COMMITTED_STATES)

    def funding_pending(self) -> bool:
        return bool(self.ledger.wallets_in(("CREATED",)))

    def dealer_address(self) -> str:
        if self._dealer_address is None:
            self._dealer_address = self.tools.wallet_address("seller")
        return self._dealer_address

    def wallet_for_pairing(self, pairing_id: str) -> str | None:
        """Demo wallet ID behind a custodial pairing, or None for a role wallet."""
        pairing = self.ledger.pairing(pairing_id)
        if not pairing or pairing["method"] != "CUSTODIAL_DEMO_WALLET":
            return None
        row = self.ledger.wallet_for_pairing(pairing_id)
        if not row or row["wallet_address"] != pairing["wallet_address"]:
            raise DemoError("custodial pairing has no matching demo wallet")
        return row["id"]

    # ----------------------------------------------------------------- tick

    def tick(self, tip: int) -> None:
        self.ledger.ensure_dealer_pairing(account_id=self.policy.dealer_account_id,
                                          wallet_address=self.dealer_address())
        for step in (self.create_requested, self.expire_due, self.confirm_funding,
                     self.retire_expired):
            try:
                step()
            except (DemoError, OrderError, ToolError, OSError, ValueError) as exc:
                self.log({"event": "DEMO_STEP_FAILED", "step": step.__name__, "error": str(exc)[:300]})
        if self.idle():
            try:
                self.top_up_sponsors()
                self.fund_created()
                self.confirm_funding()
                self.refresh_dealer()
            except (DemoError, OrderError, ToolError, OSError, ValueError) as exc:
                self.log({"event": "DEMO_STEP_FAILED", "step": "idle", "error": str(exc)[:300]})

    # --------------------------------------------------------- provisioning

    def create_requested(self) -> None:
        for row in self.ledger.wallets_in(("REQUESTED",)):
            if row["created_at"] + REQUEST_TIMEOUT_SECONDS <= time.time():
                self.ledger.advance(row["id"], expected="REQUESTED", state="FAILED",
                                    failure="wallet creation did not complete")
                continue
            self.tools.create_demo_home(row["id"])
            try:
                address = self.tools.wallet_address("buyer", wallet=row["id"])
            except ToolError:
                # First attempt (or a crash before init finished): create the seed.
                self.tools.signer("buyer", "init", wallet=row["id"])
                address = self.tools.wallet_address("buyer", wallet=row["id"])
            self.ledger.advance(row["id"], expected="REQUESTED", state="CREATED", wallet_address=address)
            self.log({"event": "DEMO_WALLET_CREATED", "wallet": row["id"]})

    def _free_coinbases(self, tip: int) -> list[tuple[int, str]]:
        committed = self.ledger.committed_sponsor_txids()
        return [(height, txid) for height, txid in self.tools.mature_sponsor_coinbases(tip)
                if txid not in committed]

    def top_up_sponsors(self) -> None:
        """Keep enough mature sponsor coinbases for queued fundings and fees."""
        tip = self.tools.chain()["height"]
        need = self.policy.sponsor_reserve + len(self.ledger.wallets_in(("CREATED",)))
        have = len(self._free_coinbases(tip))
        if have >= need:
            return
        # Each new block matures the coinbase mined 100 blocks earlier.
        count = min(need - have, 50)
        self.tools.coordinator("mine-empty", "--count", str(count))
        self.log({"event": "DEMO_SPONSOR_TOP_UP", "blocks": count})

    def fund_created(self) -> None:
        for row in self.ledger.wallets_in(("CREATED",))[:MAX_FUNDINGS_PER_TICK]:
            if row["expires_at"] <= time.time() or not self.idle():
                continue
            tip = self.tools.chain()["height"]
            coinbase = next(((h, txid) for h, txid in self._free_coinbases(tip)
                             if self.tools.coinbase_txid(h) == txid), None)
            if coinbase is None:
                raise DemoError("no mature sponsor coinbase is available for funding")
            lines = self.tools.coordinator("fund-address", "--address", row["wallet_address"],
                                           "--coinbase-height", str(coinbase[0]))
            funded = [m for line in lines if (m := FUNDED_LINE.fullmatch(line))]
            if (len(funded) != 1 or funded[0].group(1) != row["wallet_address"]
                    or int(funded[0].group(4)) != tip + 1):
                raise DemoError("coordinator did not report the exact funding")
            self.ledger.advance(row["id"], expected="CREATED", state="FUNDING",
                                funding_txid=funded[0].group(3), funding_height=int(funded[0].group(4)))
            self.log({"event": "DEMO_WALLET_FUNDING", "wallet": row["id"], "txid": funded[0].group(3)})

    def scan_wallet(self, wallet_key: str, assets: list[str]) -> dict[str, dict]:
        """Independent wallet scans, one per asset, recorded with the block hash."""
        wallet = None if wallet_key == DEALER_KEY else wallet_key
        role = "seller" if wallet_key == DEALER_KEY else "buyer"
        results = {}
        for asset in assets:
            lines = [line for line in self.tools.signer(role, "balances", "--asset", asset, wallet=wallet)
                     if line.startswith(SCAN_LINE_PREFIX)]
            parsed = self.service.parse_scan(lines, role, asset)
            block_hash = self.tools.rpc("getblockhash", [parsed["height"]])
            self.ledger.record_scan(wallet_key, asset_base=asset, height=parsed["height"],
                                    block_hash=block_hash, native_zat=parsed["nativeZat"],
                                    asset_units=parsed["assetUnits"])
            results[asset] = parsed
        return results

    def confirm_funding(self) -> None:
        for row in self.ledger.wallets_in(("FUNDING",)):
            block = self.tools.rpc("getblock", [str(row["funding_height"]), 1])
            if not isinstance(block, dict) or row["funding_txid"] not in block.get("tx", []):
                raise DemoError("funding transaction is not in its reported block")
            scans = self.scan_wallet(row["id"], list(self.service.assets.values()))
            if any(scan["height"] < row["funding_height"] or scan["nativeZat"] <= 0
                   for scan in scans.values()):
                raise DemoError("wallet scan does not show the funding yet")
            self.ledger.mark_ready(row["id"])
            self.log({"event": "DEMO_WALLET_READY", "wallet": row["id"]})

    # --------------------------------------------------------------- expiry

    def expire_due(self) -> None:
        now = time.time()
        for row in self.ledger.wallets_in(LIVE_WALLET_STATES):
            if row["expires_at"] > now:
                continue
            blocked = False
            for order in self.ledger.orders_for_buyer(row["account_id"]):
                state = order["state"]
                if state in FAILURE_STATES or state == "RECEIPT_AVAILABLE":
                    continue
                if state in PRE_EXECUTION_STATES and self.ledger.execution(order["id"]) is None:
                    self.ledger.fail(order["id"], expected=state, state="EXPIRED", release=True,
                                     evidence={"reason": "DEMO_SESSION_EXPIRED",
                                               "quoteDigest": order["quote_digest"]})
                else:
                    blocked = True  # A settlement in flight finishes first.
            if blocked:
                continue
            self.ledger.advance(row["id"], expected=row["state"], state="EXPIRED")
            self.log({"event": "DEMO_WALLET_EXPIRED", "wallet": row["id"]})

    def retire_expired(self) -> None:
        now = time.time()
        for row in self.ledger.wallets_in(("EXPIRED", "FAILED")):
            if row["expires_at"] + RETIRE_GRACE_SECONDS > now and row["state"] == "EXPIRED":
                continue
            orders = self.ledger.orders_for_buyer(row["account_id"])
            # UNCERTAIN keeps the wallet for operator reconciliation.
            if any(o["state"] not in FAILURE_STATES and o["state"] != "RECEIPT_AVAILABLE"
                   or o["state"] == "UNCERTAIN" for o in orders):
                continue
            self.tools.remove_demo_home(row["id"])
            self.ledger.advance(row["id"], expected=row["state"], state="RETIRED")
            self.log({"event": "DEMO_WALLET_RETIRED", "wallet": row["id"]})

    # ----------------------------------------------------------------- scans

    def refresh_dealer(self) -> None:
        """Rescan dealer inventory at the tip so offers show verified units."""
        tip = self.tools.chain()["height"]
        latest = self.ledger.latest_scans(DEALER_KEY)
        assets = list(self.service.assets.values())
        if all(latest.get(asset, {}).get("scan_height") == tip for asset in assets):
            return
        self.scan_wallet(DEALER_KEY, assets)

    def record_settlement_scans(self, wallet: str, asset: str, buyer_after: dict,
                                seller_after: dict) -> None:
        """Keep the reconciled after-scans as the newest portfolio observations."""
        for key, scan in ((wallet, buyer_after), (DEALER_KEY, seller_after)):
            self.ledger.record_scan(key, asset_base=asset, height=scan["height"],
                                    block_hash=self.tools.rpc("getblockhash", [scan["height"]]),
                                    native_zat=scan["nativeZat"], asset_units=scan["assetUnits"])

    def rescan_after_stabilization(self, orders: list[dict]) -> str:
        """Fresh scans of the dealer and every affected judge wallet."""
        assets = list(self.service.assets.values())
        self.scan_wallet(DEALER_KEY, assets)
        wallets = set()
        for order in orders:
            row = self.ledger.wallet_for_account(order["buyer_id"])
            if row and row["state"] in LIVE_WALLET_STATES:
                wallets.add(row["id"])
        for wallet in sorted(wallets):
            self.scan_wallet(wallet, assets)
        return "demo-wallet-scans"


def status_view(ledger: DemoLedger, row: dict, assets: dict[str, str], tip: int | None,
                block_hash_at) -> dict:
    """Public wallet status for its own judge session. No key material."""
    scans = ledger.latest_scans(row["id"])
    holdings, native, height = [], None, None
    for instrument, asset in assets.items():
        scan = scans.get(asset)
        if scan is None:
            continue
        # A scan counts only while its block is still in the live chain.
        try:
            valid = tip is not None and scan["scan_height"] <= tip and \
                block_hash_at(scan["scan_height"]) == scan["block_hash"]
        except Exception:
            valid = False
        if not valid:
            continue
        holdings.append({"instrumentId": instrument, "assetBase": asset, "units": scan["asset_units"],
                         "scanHeight": scan["scan_height"]})
        if height is None or scan["scan_height"] > height:
            height, native = scan["scan_height"], scan["native_zat"]
    address = row["wallet_address"]
    orders = ledger.orders_for_buyer(row["account_id"])
    return {"mode": "CUSTODIAL_DEMO_WALLET", "state": row["state"],
            "walletAddress": f"{address[:10]}…{address[-6:]}" if address else None,
            "fundingTxid": row["funding_txid"], "fundingHeight": row["funding_height"],
            "createdAt": row["created_at"], "readyAt": row["ready_at"], "expiresAt": row["expires_at"],
            "nativeZat": native, "scanHeight": height, "holdings": holdings,
            "ordersUsed": ledger.counted_orders(row["account_id"]),
            "openOrderId": (ledger.open_order(row["account_id"]) or {}).get("id"),
            "orderCount": len(orders)}


