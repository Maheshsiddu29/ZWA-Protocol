#!/usr/bin/env python3
"""End-to-end acceptance for the hosted judge demo, through the website only.

Drives the public HTTP API exactly like a browser: cookies, Origin header and
the per-session CSRF token taken from rendered pages. Every check reads
backend state (orders, wallet scans, chain inclusion); nothing is mocked.

    python3 tests/acceptance/judge_demo_acceptance.py --base http://127.0.0.1:3311 \\
        --rpc http://127.0.0.1:18342 [--dealer-password-env ZWA_ACCEPT_DEALER_PASSWORD] \\
        [--restart-service-cmd '...'] [--stop-node-cmd '...'] [--start-node-cmd '...']

Requires the website with ZWA_DEMO_MODE=1 and ZWA_TRUST_PROXY=1 (so each
simulated client gets its own X-Forwarded-For address), the order service,
the demo signer, and a fresh judge-demo root.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TERMINAL = {"CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN"}
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)
    return ok


class Client:
    """One browser: its own cookie jar and client address."""

    def __init__(self, base: str, address: str | None = None):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  NoRedirect())
        self.address = address or f"198.51.100.{secrets.randbelow(250) + 1}"
        self.csrf = ""

    def request(self, method: str, path: str, body: dict | None = None, *, csrf: bool = True,
                origin: str | None = None, form: dict | None = None, timeout: int = 60):
        headers = {"Origin": origin if origin is not None else self.base, "X-Forwarded-For": self.address}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["x-zwa-csrf"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                text = response.read().decode()
                return response.status, _json(text), text
        except urllib.error.HTTPError as error:
            text = error.read().decode()
            return error.code, _json(text), text

    def get(self, path: str):
        return self.request("GET", path, csrf=False)

    def post(self, path: str, body: dict | None = None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def load_csrf(self, page: str) -> str:
        _, _, html = self.get(page)
        match = re.search(r'csrf\\?"\s*:\s*\\?"([0-9a-f]{64})', html)
        self.csrf = match.group(1) if match else ""
        return self.csrf


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "1.0", "id": "accept", "method": method, "params": params}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, body, {"content-type": "application/json"}),
                                timeout=10) as response:
        return json.load(response).get("result")


def wait_for(fn, timeout: float, interval: float = 1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def start_judge(base: str, address: str | None = None) -> tuple[Client, dict | None, float]:
    client = Client(base, address)
    started = time.time()
    status, body, _ = client.post("/api/demo/session", csrf=False)
    if status != 200:
        return client, {"error": body, "status": status}, 0
    wallet = wait_for(lambda: (lambda r: r[1] if r[0] == 200 and r[1].get("state") == "READY" else None)(
        client.get("/api/exchange/wallet")), 180)
    client.load_csrf("/wallet")
    return client, wallet, time.time() - started


def offer_for(client: Client, instrument: str) -> dict:
    _, market, _ = client.get("/api/exchange/market")
    return next(o for o in market["instruments"] if o["instrumentId"] == instrument)


def order(client: Client, order_id: str) -> dict:
    return client.get(f"/api/exchange/orders/{order_id}")[1] or {}


def place(client: Client, instrument: str, quantity: int, key: str | None = None) -> tuple[int, dict]:
    offer = offer_for(client, instrument)
    status, body, _ = client.post("/api/exchange/orders", {"offerId": offer["offerId"], "quantity": quantity,
                                                           "idempotencyKey": key or "accept-" + secrets.token_hex(6)})
    return status, body


def wait_state(client: Client, order_id: str, states: set[str], timeout: float = 600) -> dict | None:
    return wait_for(lambda: (lambda o: o if o.get("state") in states | TERMINAL else None)(order(client, order_id)),
                    timeout, 1.5)


def buy_and_settle(client: Client, instrument: str, quantity: int, rpc_url: str, label: str) -> dict | None:
    status, placed = place(client, instrument, quantity)
    if not record(f"{label}: order accepted", status == 200 and placed.get("state") == "BUYER_REQUESTED",
                  f"status={status}"):
        return None
    oid = placed["id"]
    quoted = wait_state(client, oid, {"QUOTE_READY"})
    quote = (quoted or {}).get("quote") or {}
    offer = offer_for(client, instrument)
    record(f"{label}: backend quote is exact", bool(quote) and quote["quantity"] == quantity and
           quote["paymentZat"] == quantity * offer["priceZatPerUnit"] and len(quote["digest"]) == 64,
           f"payment={quote.get('paymentZat')} digest={str(quote.get('digest'))[:12]}")
    status, confirmed, _ = client.post(f"/api/exchange/orders/{oid}/confirm", {"quoteDigest": quote.get("digest")})
    record(f"{label}: explicit confirmation", status == 200 and confirmed.get("state") == "BUYER_CONFIRMED",
           f"status={status}")
    done = wait_state(client, oid, {"RECEIPT_AVAILABLE"})
    receipt = (done or {}).get("receipt")
    if not record(f"{label}: settled with receipt", bool(done) and done["state"] == "RECEIPT_AVAILABLE" and bool(receipt),
                  f"state={(done or {}).get('state')} failure={(done or {}).get('failure')}"):
        return done
    proof = done.get("proof") or {}
    record(f"{label}: Groth16 authorization verified", all(proof.get(k) == "VERIFIED" for k in (
        "issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
        "investorEligibility", "exactTradeAuthorization")))
    record(f"{label}: restricted policy signers", done.get("buyerApprovalMode") == "CUSTODIAL_DEMO_POLICY" and
           done.get("sellerApprovalMode") == "CUSTODIAL_DEMO_POLICY")
    block = rpc(rpc_url, "getblock", [str(receipt["height"]), 1])
    record(f"{label}: transaction included (independent RPC)", receipt["txid"] in block["tx"] and
           block["hash"] == receipt["blockHash"], f"txid={receipt['txid'][:16]} height={receipt['height']}")
    record(f"{label}: wallet scans reconcile", done.get("effects") == {"assetUnits": quantity,
                                                                       "nativeZat": -quote["paymentZat"]},
           f"effects={done.get('effects')}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True)
    parser.add_argument("--rpc", required=True, help="independent loopback RPC URL of the demo chain")
    parser.add_argument("--dealer", default="dealer")
    parser.add_argument("--dealer-password-env", default="ZWA_ACCEPT_DEALER_PASSWORD")
    parser.add_argument("--restart-service-cmd")
    parser.add_argument("--stop-node-cmd")
    parser.add_argument("--start-node-cmd")
    parser.add_argument("--skip-expiry", action="store_true")
    args = parser.parse_args()
    base = args.base

    # 1-2. Fresh judges get distinct, ready wallets.
    a, wallet_a, ready_a = start_judge(base)
    record("judge A wallet provisioned and ready", bool(wallet_a) and wallet_a.get("state") == "READY" and
           (wallet_a.get("nativeZat") or 0) > 0, f"{ready_a:.1f}s native={wallet_a and wallet_a.get('nativeZat')}")
    b, wallet_b, ready_b = start_judge(base)
    record("judge B wallet provisioned and ready", bool(wallet_b) and wallet_b.get("state") == "READY",
           f"{ready_b:.1f}s")
    record("judge wallets are distinct", wallet_a and wallet_b and
           wallet_a["walletAddress"] != wallet_b["walletAddress"] and
           wallet_a["fundingTxid"] != wallet_b["fundingTxid"])

    # 3. Listing with inventory verified by a dealer wallet scan.
    gold = offer_for(a, "zwa-gold-reference")
    record("asset listing has a live price and scanned inventory", gold["priceZatPerUnit"] > 0 and
           gold["availableUnits"] > 0 and gold["inventoryVerifiedAtTip"], f"available={gold['availableUnits']}")

    # Negative checks that do not need a live order.
    status, body = place(a, "zwa-gold-reference", 6)
    record("per-order unit limit enforced", status == 409, f"status={status} {body}")
    anon = Client(base)
    record("anonymous purchase refused", anon.post("/api/exchange/orders", {"offerId": gold["offerId"], "quantity": 1,
           "idempotencyKey": "anon-attempt"}, csrf=False)[0] in (401, 403))
    saved = a.csrf
    a.csrf = ""
    record("missing CSRF token refused", place(a, "zwa-gold-reference", 1)[0] == 403)
    a.csrf = "0" * 64
    record("another session's CSRF token refused", place(a, "zwa-gold-reference", 1)[0] == 403)
    a.csrf = saved
    status = a.request("POST", "/api/exchange/orders", {"offerId": gold["offerId"], "quantity": 1,
                       "idempotencyKey": "foreign-origin"}, origin="https://evil.example")[0]
    record("foreign Origin refused", status == 403)
    forged = Client(base)
    for cookie in a.jar:
        payload, _, tag = cookie.value.partition(".")
        forged.jar.set_cookie(http.cookiejar.Cookie(0, cookie.name, payload[:-2] + "AA." + tag, None, False,
                              cookie.domain, False, False, "/", True, False, None, False, None, None, {}))
    record("tampered session cookie refused", forged.get("/api/exchange/wallet")[0] in (401, 403, 409))
    record("judge cannot use seller actions", a.post(f"/api/exchange/offers/{gold['offerId']}/price",
           {"priceZatPerUnit": 1})[0] in (403, 409))

    # 4. Duplicate submission returns the same order.
    key = "dup-" + secrets.token_hex(6)
    first = place(a, "zwa-gold-reference", 2, key)
    second = place(a, "zwa-gold-reference", 2, key)
    record("duplicate submission is idempotent", first[0] == 200 and second[0] == 200 and
           first[1]["id"] == second[1]["id"], f"{first[1].get('id')}")
    record("second open order refused", place(a, "zwa-gold-reference", 1)[0] == 409)
    a.post(f"/api/exchange/orders/{first[1]['id']}/cancel")
    record("pre-signing cancel releases the order", wait_state(a, first[1]["id"], {"CANCELLED"}, 30) is not None)

    # Forged identifiers in the body are ignored; the server binds the order to the session.
    status, body, _ = a.post("/api/exchange/orders", {"offerId": gold["offerId"], "quantity": 1,
                             "idempotencyKey": "forged-" + secrets.token_hex(4), "buyerId": "judge-" + "0" * 20,
                             "walletAddress": "f" * 86, "priceZatPerUnit": 1})
    record("forged buyer, wallet and price fields ignored", status == 200 and body["buyerId"] != "judge-" + "0" * 20
           and body["quantity"] == 1, f"buyer={body.get('buyerId')}")
    forged_order = body["id"]
    quoted = wait_state(a, forged_order, {"QUOTE_READY"})
    record("forged price did not reach the quote", quoted and quoted["quote"]["paymentZat"] == gold["priceZatPerUnit"])
    status, body, _ = a.post(f"/api/exchange/orders/{forged_order}/confirm", {"quoteDigest": "0" * 64})
    record("confirmation of a different digest refused", status == 409, f"status={status}")
    a.post(f"/api/exchange/orders/{forged_order}/cancel")
    wait_state(a, forged_order, {"CANCELLED"}, 30)

    # 5-12 for judge A, with judge B buying concurrently (serialized on the dealer).
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(buy_and_settle, a, "zwa-gold-reference", 2, args.rpc, "judge A")
        time.sleep(2)
        fb = pool.submit(buy_and_settle, b, "zwa-treasury-style", 1, args.rpc, "judge B (concurrent)")
        done_a, done_b = fa.result(), fb.result()

    # Wrong-session access.
    if done_a:
        record("other judge cannot read the order", b.get(f"/api/exchange/orders/{done_a['id']}")[0] != 200)
        record("other judge cannot cancel the order", b.post(f"/api/exchange/orders/{done_a['id']}/cancel")[0] != 200)
        status, _, html = Client(base).get(f"/verify?tx={done_a['receipt']['txid']}")
        record("public verify shows inclusion only", status == 200 and "Included" in html and
               done_a["buyerId"] not in html)

    # 11. Portfolio reflects the purchase after the wallet scan.
    holdings = wait_for(lambda: (lambda w: w if any(h["instrumentId"] == "zwa-gold-reference" and h["units"] >= 2
                                                    for h in w.get("holdings", [])) else None)(
        a.get("/api/exchange/wallet")[1] or {}), 60)
    record("portfolio reflects purchased asset", bool(holdings))
    durable = wait_for(lambda: (lambda o: o if (o.get("receipt") or {}).get("durability") else None)(
        order(a, done_a["id"])) if done_a else None, 400, 3)
    record("receipt becomes restart-durable", bool(durable))

    # Rejected signing: the dealer pauses its standing policy after the judge confirms.
    password = os.environ.get(args.dealer_password_env)
    if password:
        dealer = Client(base)
        status, _, _ = dealer.request("POST", "/api/exchange/login", form={"id": args.dealer, "password": password},
                                      csrf=False)
        dealer.load_csrf("/seller")
        record("dealer signs in", status == 303 and bool(dealer.csrf))
        status, placed = place(a, "zwa-credit-participation", 1)
        oid = placed.get("id")
        quoted = wait_state(a, oid, {"QUOTE_READY"})
        a.post(f"/api/exchange/orders/{oid}/confirm", {"quoteDigest": quoted["quote"]["digest"]})
        dealer.post("/api/exchange/dealer-policy", {"policy": "PAUSED"})
        failed = wait_state(a, oid, set(), 300)
        record("dealer policy refusal ends the order unsettled", failed and failed["state"] == "EXECUTION_FAILED" and
               (failed.get("failure") or {}).get("transactionSubmitted") is False,
               f"state={(failed or {}).get('state')} reason={((failed or {}).get('failure') or {}).get('reason')}")
        record("paused dealer refuses new purchases", place(a, "zwa-gold-reference", 1)[0] == 409)
        dealer.post("/api/exchange/dealer-policy", {"policy": "ACTIVE"})
        record("judge cannot sign in as dealer with a demo cookie", a.post("/api/exchange/dealer-policy",
               {"policy": "PAUSED"})[0] in (403, 409))
    else:
        record("dealer checks skipped (no password env)", True, "SKIPPED")

    # Expired quote.
    if not args.skip_expiry:
        status, placed = place(b, "zwa-gold-reference", 1)
        expired = wait_state(b, placed["id"], {"EXPIRED"}, 240) if status == 200 else None
        record("unconfirmed quote expires and releases", expired and expired["state"] == "EXPIRED",
               f"state={(expired or {}).get('state')}")

    # Process crash and restart in the middle of an order.
    if args.restart_service_cmd:
        status, placed = place(b, "zwa-credit-participation", 1)
        quoted = wait_state(b, placed["id"], {"QUOTE_READY"})
        b.post(f"/api/exchange/orders/{placed['id']}/confirm", {"quoteDigest": quoted["quote"]["digest"]})
        subprocess.run(args.restart_service_cmd, shell=True, check=True)
        survived = wait_state(b, placed["id"], {"RECEIPT_AVAILABLE"})
        record("order completes after service crash and restart", survived and survived["state"] == "RECEIPT_AVAILABLE",
               f"state={(survived or {}).get('state')}")

    # Unavailable node, then restart: no purchase while down, durable receipts survive.
    if args.stop_node_cmd and args.start_node_cmd and done_a:
        subprocess.run(args.stop_node_cmd, shell=True, check=True)
        _, market, _ = a.get("/api/exchange/market")
        record("market reports chain unavailable", market and market.get("chainActive") is False)
        status = a.post("/api/exchange/orders", {"offerId": gold["offerId"], "quantity": 1,
                                                  "idempotencyKey": "node-down-" + secrets.token_hex(4)})[0]
        record("purchase refused while node is down", status != 200, f"status={status}")
        subprocess.run(args.start_node_cmd, shell=True, check=True)
        back = wait_for(lambda: (lambda m: m if m and m.get("chainActive") else None)(a.get("/api/exchange/market")[1]), 120)
        record("chain resumes after restart", bool(back))
        restored = order(a, done_a["id"])
        record("durable receipt survives node restart", bool(restored.get("receipt")) and
               restored["receipt"]["txid"] == done_a["receipt"]["txid"])

    # Session end and repeated sign-ups.
    a.post("/api/demo/end")
    record("ended session can no longer act", a.get("/api/exchange/wallet")[0] != 200)
    burst = Client(base, "203.0.113.77")
    codes = [Client(base, burst.address).post("/api/demo/session", csrf=False)[0] for _ in range(5)]
    record("repeated sign-ups from one client are rate limited", codes.count(429) >= 1, f"codes={codes}")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
