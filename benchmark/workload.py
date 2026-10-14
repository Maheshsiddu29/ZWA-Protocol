"""Benchmark levels: real purchases through the real portal, watched at 1 Hz.

Each purchase follows the exchange's own path; the harness only acts where a
browser would (start a demo session, place an order, confirm the exact quote,
read status) and never touches a wallet, proof or signer:

  demo-start -> wallet READY -> buy (+ one retried duplicate) -> QUOTE_READY
  -> confirm(quoteDigest) -> RECEIPT_AVAILABLE -> verify-tx -> durability

Stop discipline (see docs/benchmark-runbook.md, "Safe cancellation"):
* the gate is checked before every submission (session, order, confirmation);
* after a stop, an order still before confirmation is cancelled through the
  portal's own pre-signing cancel, which releases its reservation;
* a confirmed order is never cancelled or interrupted: the harness keeps
  observing it until it settles or fails on its own, or the drain window
  ends, and the run is then PARTIAL and needs `reconcile`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import threading
import time
from typing import Callable, Protocol

from . import analysis, correlation
from .engine import SETTLED, TERMINAL_FAILURE, EngineError, PortalRefused
from .evidence import JsonLines, Redactor
from .safety import LEVELS, check_genesis, check_level
from .watchdog import StopController, Thresholds, Watchdog

# One unit of a different instrument per buyer: equal cryptographic work per
# purchase, and no buyer waits on another's offer row.
INSTRUMENTS = ("zwa-gold-reference", "zwa-treasury-style", "zwa-credit-participation")
WALLET_STATES = ("REQUESTED", "CREATED", "FUNDING", "READY")
PORTAL_REFUSAL_LIMIT = 5
BUY_ATTEMPTS = 3  # same key and payload every time
# Every purchase outcome (summary.json transactions[].outcome; pinned by the schema test).
OUTCOMES = ("NOT_STARTED", "ABANDONED_BEFORE_PURCHASE", "WALLET_FAILED", "WALLET_TIMEOUT", "NO_INVENTORY",
            "DUPLICATE_MISMATCH", "FAILED_BEFORE_QUOTE", "QUOTE_TIMEOUT", "QUOTE_MISMATCH",
            "CANCELLED_BEFORE_SIGNING", "UNCONFIRMED_NEEDS_RECONCILE", "CONFIRM_REFUSED", "FAILED_AFTER_CONFIRM",
            "UNCERTAIN", "SETTLED_VERIFIED", "SETTLED_WITH_PROBLEMS", "IN_PROGRESS_AT_DRAIN_END", "PORTAL_REFUSED",
            "HARNESS_ERROR", "SETTLED_NOT_DURABLE", "PURCHASE_AMBIGUOUS")
RESULTS = ("PASS", "STOPPED", "PARTIAL")


@dataclass
class Timing:
    """Wall clock and sleeping, injectable so tests run without real waits.

    `wait` returns early when a stop is triggered (used while nothing is
    confirmed); `sleep` always sleeps (used while draining confirmed work).
    """
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    interruptible: bool = True

    def wait(self, controller: StopController, seconds: float) -> bool:
        if self.interruptible:
            return controller.wait(seconds)
        self.sleep(seconds)
        return controller.stopped


# ---------------------------------------------------------------- monitor

class Monitor(Protocol):
    samples: list[dict]

    def start(self) -> None: ...

    def stop(self) -> None: ...


class ResourceMonitor:
    """Samples host, container and process resources at a fixed interval in its own thread.

    `host` and each container sampler are injected; `inspect` returns the
    Docker state of each long-running service and is called every
    `inspect_every` samples, or at once when a container's cgroup or init
    process disappears.
    """

    def __init__(self, *, host, containers, inspect, watchdog: Watchdog, sink: JsonLines,
                 interval: float = 1.0, inspect_every: int = 5, clock=time.time, rebuild=None):
        self.host = host
        self.containers = containers  # service -> sampler with .sample()
        self.inspect = inspect
        self.rebuild = rebuild  # service, state -> new sampler (after a restart), optional
        self.watchdog = watchdog
        self.sink = sink
        self.interval = interval
        self.inspect_every = max(1, inspect_every)
        self.clock = clock
        self.samples: list[dict] = []
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._states: dict[str, dict] = {}
        self.index = 0

    def sample_once(self) -> dict | None:
        index = self.index
        self.index += 1
        try:
            if index % self.inspect_every == 0 or not self._states:
                self._states = self.inspect()
            containers = []
            for service, sampler in list(self.containers.items()):
                item = sampler.sample()
                if not item.get("present") or item.get("initPidAlive") is False:
                    self._states = self.inspect()
                    state = self._states.get(service) or {}
                    if self.rebuild and state.get("status") == "running":
                        self.containers[service] = self.rebuild(service, state)
                item["state"] = self._states.get(service) or {"status": "missing"}
                containers.append(item)
            sample = {"t": round(self.clock(), 3), "index": index, "host": self.host.sample(),
                      "containers": containers}
        except Exception as exc:  # noqa: BLE001 - any sampling failure is recorded and counted
            self.watchdog.sampler_failed(f"{type(exc).__name__}: {exc}")
            error = f"{type(exc).__name__}: {exc}"[:300]
            self.sink.write({"t": round(self.clock(), 3), "index": index, "error": error})
            return None
        self.samples.append(sample)
        self.sink.write(sample)
        self.watchdog.evaluate(sample)
        return sample

    def _loop(self) -> None:
        next_at = time.monotonic()
        while not self._halt.is_set():
            self.sample_once()
            next_at += self.interval
            delay = next_at - time.monotonic()
            if delay < 0:
                next_at = time.monotonic()  # fell behind; never burst to catch up
                delay = 0
            self._halt.wait(delay)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="resource-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        if self._thread:
            self._thread.join(timeout=30)


# ---------------------------------------------------------------- purchase

@dataclass
class Purchase:
    label: str
    instrument: str
    quantity: int
    timeline: dict = field(default_factory=dict)
    states: list = field(default_factory=list)
    order_id: str | None = None
    account: str | None = None
    payment: int | None = None
    offer_id: str | None = None
    txid: str | None = None
    outcome: str = "NOT_STARTED"
    problems: list = field(default_factory=list)
    view: dict | None = None
    verify: dict | None = None
    durability_required: bool = True
    durability_observed: bool = False
    final_check: dict | None = None

    def public(self) -> dict:
        receipt = (self.view or {}).get("receipt") or {}
        return {"label": self.label, "instrument": self.instrument, "quantity": self.quantity,
                "orderId": self.order_id, "account": self.account, "offerId": self.offer_id,
                "paymentZat": self.payment, "txid": self.txid, "height": receipt.get("height"),
                "blockHash": receipt.get("blockHash"), "confirmations": receipt.get("confirmations"),
                "durability": receipt.get("durability"), "outcome": self.outcome,
                "finalState": (self.view or {}).get("state"), "timeline": self.timeline,
                "observedStates": self.states, "problems": self.problems,
                "verifyTx": self.verify, "durabilityRequired": self.durability_required,
                "durabilityObserved": self.durability_observed, "finalCheck": self.final_check}


class Worker:
    """Drives one purchase through the portal. Never cancels after confirmation."""

    def __init__(self, purchase: Purchase, *, relay, controller: StopController, thresholds: Thresholds,
                 events: JsonLines, redactor: Redactor, queue_position: int, wait_durability: bool,
                 poll: float = 1.0, timing: Timing | None = None,
                 correlations: correlation.CorrelationStore | None = None):
        self.p = purchase
        self.correlations = correlations
        self.relay = relay
        self.controller = controller
        self.t = thresholds
        self.events = events
        self.redactor = redactor
        self.queue_position = queue_position
        self.wait_durability = wait_durability
        self.poll = poll
        self.timing = timing or Timing()
        self.clock = self.timing.now
        self.session = redactor.register(secrets.token_hex(32))
        self.client = redactor.register(secrets.token_hex(32))
        self.drain_until: float | None = None
        self.refusals = 0
        self.p.durability_required = wait_durability

    # ------------------------------------------------------------ helpers

    def mark(self, name: str, **extra) -> float:
        now = round(self.clock(), 3)
        self.p.timeline.setdefault(name, now)
        self.events.write({"t": now, "purchase": self.p.label, "event": name, **extra})
        return now

    def call(self, action: str, payload: dict | None = None, *, anonymous: bool = False) -> dict:
        if anonymous:
            return self.relay.portal(action, session=self.session, payload=payload)
        return self.relay.portal(action, actor=self.p.account, role="buyer", session=self.session, payload=payload)

    def poll_call(self, action: str, payload: dict | None = None) -> dict | None:
        """A read; transient transport failures return None. Any refusal is recorded and stops the run (the
        portal refuses only definite errors); observation continues until repeated refusals end it."""
        try:
            data = self.call(action, payload)
            self.refusals = 0
            return data
        except PortalRefused as exc:
            self.refusals += 1
            if self.refusals == 1:
                self.fail("PORTAL_REFUSED", f"{action}: {exc}")
            if self.refusals >= PORTAL_REFUSAL_LIMIT:
                raise
            return None
        except EngineError:
            return None

    def fail(self, code: str, detail: str) -> None:
        self.p.problems.append({"code": code, "detail": detail[:300]})
        self.controller.trigger(code, f"{self.p.label}: {detail}", purchase=self.p.label, orderId=self.p.order_id)

    def note_state(self, state: str | None) -> None:
        if not self.p.states or self.p.states[-1][0] != state:
            self.p.states.append([state, round(self.clock(), 3)])
            self.events.write({"t": round(self.clock(), 3), "purchase": self.p.label, "event": "STATE",
                               "state": state, "orderId": self.p.order_id})

    def cancel_unconfirmed(self, why: str, order_id: str | None = None) -> None:
        """The exchange's own pre-signing cancel. Only before confirmation."""
        order_id = order_id or self.p.order_id
        if not order_id or (order_id == self.p.order_id and "confirmed" in self.p.timeline):
            return
        try:
            view = self.call("cancel", {"orderId": order_id})
            if order_id == self.p.order_id:
                self.p.view = view
                self.note_state(view.get("state"))
            if self.p.outcome == "NOT_STARTED":
                self.p.outcome = "CANCELLED_BEFORE_SIGNING"
            self.mark("cancelled", reason=why, orderId=order_id)
        except (PortalRefused, EngineError) as exc:
            self.p.problems.append({"code": "CANCEL_REFUSED", "detail": str(exc)[:200]})
            if self.p.outcome == "NOT_STARTED":
                self.p.outcome = "UNCONFIRMED_NEEDS_RECONCILE"

    # ------------------------------------------------------------ steps

    def run(self) -> None:
        try:
            if self.start_wallet() and self.place() and self.quote() and self.confirm():
                self.settle()
        except PortalRefused as exc:
            if not any(item["code"] == "PORTAL_REFUSED" for item in self.p.problems):
                self.fail("PORTAL_REFUSED", str(exc))
            if self.p.outcome == "NOT_STARTED":
                self.p.outcome = "PORTAL_REFUSED"
        except Exception as exc:  # noqa: BLE001 - recorded, and the run stops
            self.fail("HARNESS_ERROR", f"{type(exc).__name__}: {exc}")
            self.p.outcome = "HARNESS_ERROR"
        finally:
            # A purchase that did not settle and verify always stops the level; nothing ends silently.
            if self.p.outcome != "SETTLED_VERIFIED" and not self.controller.stopped:
                self.fail("ORDER_FAILED", f"purchase ended {self.p.outcome}")

    def start_wallet(self) -> bool:
        if not self.controller.may_submit():
            self.p.outcome = "NOT_STARTED"
            return False
        self.mark("walletRequested")
        started = self.call("demo-start", {"clientHash": self.client}, anonymous=True)
        self.p.account = started["accountId"]
        deadline = self.clock() + self.t.wallet_ready_timeout_s
        while self.clock() < deadline:
            wallet = self.poll_call("wallet")
            state = (wallet or {}).get("state")
            if state in WALLET_STATES:
                self.mark({"CREATED": "walletCreated", "FUNDING": "walletFunding", "READY": "walletReady",
                           "REQUESTED": "walletQueued"}[state])
                if state == "READY" and (wallet or {}).get("nativeZat"):
                    return True
            elif state is not None:
                self.fail("ORDER_FAILED", f"demo wallet reached {state}")
                self.p.outcome = "WALLET_FAILED"
                return False
            if self.timing.wait(self.controller, self.poll):
                self.p.outcome = "ABANDONED_BEFORE_PURCHASE"  # nothing was ordered
                return False
        self.fail("WALLET_READY_TIMEOUT", f"wallet not READY within {self.t.wallet_ready_timeout_s}s")
        self.p.outcome = "WALLET_TIMEOUT"
        return False

    def place(self) -> bool:
        if not self.controller.may_submit():
            self.p.outcome = "ABANDONED_BEFORE_PURCHASE"
            return False
        market = self.call("market")
        offer = next((o for o in market.get("instruments", []) if o.get("instrumentId") == self.p.instrument), None)
        if not market.get("chainActive") or not offer or not offer.get("priceZatPerUnit") \
                or offer.get("availableUnits", 0) < self.p.quantity or not offer.get("inventoryVerifiedAtTip"):
            self.fail("PORTAL_REFUSED", f"no verified, priced inventory for {self.p.instrument}")
            self.p.outcome = "NO_INVENTORY"
            return False
        self.p.offer_id = offer["offerId"]
        self.p.payment = self.p.quantity * offer["priceZatPerUnit"]
        # One key for this logical buy, durably recorded before anything is sent. It is correlation material:
        # registered with the redactor, kept only in private/, never regenerated.
        key = self.redactor.register("bench-" + secrets.token_hex(12))
        payload = {"offerId": offer["offerId"], "quantity": self.p.quantity, "idempotencyKey": key}
        if self.correlations is not None:
            self.correlations.begin(self.p.label, account=self.p.account or "", session=self.session, key=key,
                                    offer_id=offer["offerId"], instrument=self.p.instrument,
                                    quantity=self.p.quantity)
        self.mark("purchaseSubmitted")
        first = self.submit_buy(payload)
        if first is None:
            return False
        self.mark("purchaseAccepted")
        self.p.order_id = first["id"]
        self.note_state(first.get("state"))
        # A retried submission (lost response) must return the same order and reserve nothing more.
        second = self.resend_buy(payload)
        self.mark("duplicateChecked", sameOrder=second.get("id") == first["id"])
        if second.get("id") != first["id"]:
            self.fail("DUPLICATE_SUBMISSION_MISMATCH", "a retried buy with the same idempotency key "
                      "created a different order")
            self.p.outcome = "DUPLICATE_MISMATCH"
            self.cancel_unconfirmed("duplicate mismatch")
            if second.get("id"):
                self.cancel_unconfirmed("duplicate mismatch", second["id"])
            return False
        return True

    def record_correlation(self, status: str, order_id: str | None, reason: str) -> None:
        if self.correlations is not None:
            self.correlations.update(self.p.label, status=status, order_id=order_id, reason=reason)

    def submit_buy(self, payload: dict) -> dict | None:
        """Submit the one logical buy. Unanswered attempts are resent with the same key and payload; anything
        still unknown is resolved by reading the buyer's orders, never by another purchase."""
        refused: PortalRefused | None = None
        for attempt in range(BUY_ATTEMPTS):
            try:
                view = self.call("buy", payload)
            except PortalRefused as exc:
                refused = exc  # possibly after an earlier attempt was accepted: only a lookup can tell
                break
            except EngineError:
                if attempt + 1 < BUY_ATTEMPTS:
                    self.timing.sleep(self.poll)
                continue
            self.record_correlation("ACCEPTED", view.get("id"), f"buy answered on attempt {attempt + 1}")
            return view
        record = {"account": self.p.account, "sessionHash": self.session}
        status, order_id, reason = correlation.classify(
            correlation.lookup(self.relay, record, self.timing.sleep), instrument=self.p.instrument,
            quantity=self.p.quantity)
        self.record_correlation(status, order_id, ("refused: " if refused else "unanswered: ") + reason)
        if refused is not None:
            self.fail("PORTAL_REFUSED", f"buy: {refused}")
        if status == "ACCEPTED":
            self.mark("purchaseRecoveredByLookup")
            recovered = self.poll_call("order", {"orderId": order_id})
            if recovered is None:
                self.fail("PURCHASE_AMBIGUOUS", "the recovered order could not be read")
                self.p.order_id = order_id
                self.p.outcome = "PURCHASE_AMBIGUOUS"
                return None
            return recovered
        if status == "NOT_ACCEPTED":
            if refused is None:
                self.fail("HARNESS_ERROR", "the buy never reached the exchange (no order exists)")
            self.p.outcome = "PORTAL_REFUSED" if refused is not None else "HARNESS_ERROR"
            return None
        self.fail("PURCHASE_AMBIGUOUS", f"whether the exchange accepted the buy is unknown: {reason}")
        self.p.outcome = "PURCHASE_AMBIGUOUS"
        return None

    def resend_buy(self, payload: dict) -> dict:
        """The deliberate duplicate: the same key and payload, which must return the same order."""
        for attempt in range(BUY_ATTEMPTS):
            try:
                return self.call("buy", payload)
            except EngineError:
                if attempt + 1 < BUY_ATTEMPTS:
                    self.timing.sleep(self.poll)
        raise EngineError("the duplicate-submission check could not be completed")

    def quote(self) -> bool:
        # Purchases queue on the single dealer wallet; later positions wait for earlier settlements.
        deadline = self.clock() + self.t.quote_timeout_s + self.queue_position * self.t.settlement_timeout_s
        while self.clock() < deadline:
            view = self.poll_call("order", {"orderId": self.p.order_id})
            if view:
                self.p.view = view
                self.note_state(view["state"])
                if view["state"] == "QUOTE_READY" and view.get("quote"):
                    self.mark("quoteReadySeen")
                    return self.check_quote(view["quote"])
                if view["state"] in TERMINAL_FAILURE:
                    self.fail("ORDER_FAILED", f"order ended {view['state']} before a quote: {view.get('failure')}")
                    self.p.outcome = "FAILED_BEFORE_QUOTE"
                    return False
            if self.timing.wait(self.controller, self.poll):
                self.cancel_unconfirmed("stop condition before quote")
                return False
        self.fail("QUOTE_TIMEOUT", "no quote in time")
        self.p.outcome = "QUOTE_TIMEOUT"
        self.cancel_unconfirmed("quote timeout")
        return False

    def check_quote(self, quote: dict) -> bool:
        if quote.get("quantity") != self.p.quantity or quote.get("paymentZat") != self.p.payment \
                or not isinstance(quote.get("digest"), str) or len(quote["digest"]) != 64 \
                or quote.get("network") != "regtest":
            self.fail("ORDER_FAILED", "server quote differs from the requested purchase or is not regtest")
            self.p.outcome = "QUOTE_MISMATCH"
            self.cancel_unconfirmed("quote mismatch")
            return False
        return True

    def confirm(self) -> bool:
        if not self.controller.may_submit():  # the last point at which nothing has been authorized
            self.cancel_unconfirmed("stop condition before confirmation")
            return False
        try:
            digest = ((self.p.view or {}).get("quote") or {}).get("digest")
            view = self.call("confirm", {"orderId": self.p.order_id, "quoteDigest": digest})
        except PortalRefused as exc:
            self.fail("ORDER_FAILED", f"confirmation refused: {exc}")
            self.p.outcome = "CONFIRM_REFUSED"
            return False
        self.mark("confirmed")
        self.p.view = view
        self.note_state(view.get("state"))
        return True

    def settle(self) -> None:
        start = self.p.timeline["confirmed"]
        deadline = start + self.t.settlement_timeout_s
        timed_out = False
        while True:
            view = self.poll_call("order", {"orderId": self.p.order_id})
            if view:
                self.p.view = view
                self.note_state(view["state"])
                if view["state"] == SETTLED:
                    self.mark("receiptSeen")
                    self.verify_settlement()
                    if self.wait_durability and self.p.outcome == "SETTLED_VERIFIED":
                        self.await_durability()
                    return
                if view["state"] in TERMINAL_FAILURE:
                    self.mark("terminalSeen", state=view["state"])
                    reason = (view.get("failure") or {}).get("reason")
                    self.fail("ORDER_FAILED", f"order ended {view['state']}: {reason}")
                    self.p.outcome = "FAILED_AFTER_CONFIRM" if view["state"] != "UNCERTAIN" else "UNCERTAIN"
                    return
            now = self.clock()
            if not timed_out and now >= deadline:
                timed_out = True
                self.fail("SETTLEMENT_TIMEOUT", f"no receipt {self.t.settlement_timeout_s}s after confirmation")
            if self.controller.stopped and self.drain_until is None:
                self.drain_until = now + self.t.drain_timeout_s
                self.mark("drainStarted")
            if self.drain_until is not None and now >= self.drain_until:
                self.p.outcome = "IN_PROGRESS_AT_DRAIN_END"
                self.mark("drainEnded", state=(self.p.view or {}).get("state"))
                return
            if self.controller.stopped:
                self.timing.sleep(self.poll)  # draining: confirmed work is observed, not abandoned
            else:
                self.timing.wait(self.controller, self.poll)

    def verify_settlement(self) -> None:
        view = self.p.view or {}
        receipt = view.get("receipt") or {}
        self.p.txid = receipt.get("txid")
        verify = None
        if self.p.txid:
            try:
                verify = self.relay.portal("verify-tx", session=self.session, payload={"txid": self.p.txid})
            except (PortalRefused, EngineError) as exc:
                verify = {"error": str(exc)[:200]}
        self.p.verify = verify
        problems = analysis.receipt_problems(view, None, verify, quantity=self.p.quantity,
                                             payment=self.p.payment or 0)
        for code, detail in problems:
            self.fail(code, detail)
        self.p.outcome = "SETTLED_VERIFIED" if not problems else "SETTLED_WITH_PROBLEMS"

    def await_durability(self) -> None:
        deadline = self.clock() + self.t.drain_timeout_s
        while self.clock() < deadline:
            view = self.poll_call("order", {"orderId": self.p.order_id})
            if view and view.get("settlementDropped"):
                self.p.view = view
                self.fail("DURABILITY_LOST", "the settlement block left the chain before durability")
                self.p.outcome = "SETTLED_NOT_DURABLE"
                return
            if view and view.get("state") != SETTLED:
                self.p.view = view
                self.fail("RECEIPT_INCONSISTENT", f"receipt disappeared before durability (state {view.get('state')})")
                self.p.outcome = "SETTLED_WITH_PROBLEMS"
                return
            if view and (view.get("receipt") or {}).get("durability"):
                self.p.view = view
                self.p.durability_observed = True
                self.mark("durableSeen")
                return
            self.timing.sleep(self.poll)
        self.fail("DURABILITY_NOT_OBSERVED", f"no durability within {self.t.drain_timeout_s}s of the receipt")
        self.p.outcome = "SETTLED_NOT_DURABLE"

    def final_check(self) -> None:
        """Read the settled order once more at the end of the run (read-only). Its receipt must still be live,
        unchanged, and durable when durability is required; a durability seen earlier must not have been lost."""
        view = None
        for _ in range(5):
            try:
                view = self.call("order", {"orderId": self.p.order_id})
                break
            except PortalRefused as exc:
                self.fail("PORTAL_REFUSED", f"final order lookup: {exc}")
                break
            except EngineError:
                self.timing.sleep(self.poll)
        now = round(self.clock(), 3)
        if view is None:
            self.fail("RECEIPT_MISSING", "the final order view is unavailable")
            self.p.final_check = {"ok": False, "at": now, "reason": "unavailable"}
            self.p.outcome = "SETTLED_WITH_PROBLEMS"
            return
        self.p.view = view
        receipt = view.get("receipt") or {}
        problems = []
        if view.get("state") != SETTLED or not receipt:
            problems.append(("RECEIPT_MISSING", f"the receipt is no longer available (state {view.get('state')})"))
        elif receipt.get("txid") != self.p.txid:
            problems.append(("RECEIPT_INCONSISTENT", "the receipt's transaction changed"))
        if view.get("settlementDropped"):
            problems.append(("DURABILITY_LOST", "the settlement block is no longer in the chain"))
        elif self.wait_durability and not receipt.get("durability"):
            problems.append(("DURABILITY_LOST" if self.p.durability_observed else "DURABILITY_NOT_OBSERVED",
                             "durability is not present at the final check"))
        for code, detail in problems:
            self.fail(code, detail)
        if problems:
            durability_only = all(code.startswith("DURABILITY") for code, _ in problems)
            self.p.outcome = "SETTLED_NOT_DURABLE" if durability_only else "SETTLED_WITH_PROBLEMS"
        self.p.final_check = {"ok": not problems, "at": now, "state": view.get("state"),
                              "durability": receipt.get("durability"),
                              "settlementDropped": bool(view.get("settlementDropped"))}


# ---------------------------------------------------------------- level

@dataclass
class LevelPlan:
    level: int
    wait_durability: bool = True
    idle_before_s: int = 30
    idle_after_s: int = 30
    baseline_s: int = 300  # level 0 only
    poll_s: float = 1.0

    def purchases(self) -> list[Purchase]:
        count = check_level(self.level)
        return [Purchase(label=f"P{i + 1}", instrument=INSTRUMENTS[i], quantity=1) for i in range(count)]


class LevelRun:
    def __init__(self, plan: LevelPlan, *, thresholds: Thresholds, relay, probe, monitor: Monitor,
                 controller: StopController, events: JsonLines, redactor: Redactor, timing: Timing | None = None,
                 before_submit=None, correlations: correlation.CorrelationStore | None = None):
        self.plan = plan
        self.correlations = correlations
        self.before_submit = before_submit  # e.g. reserve the transaction budget once preconditions pass
        self.t = thresholds
        self.relay = relay
        self.probe = probe
        self.monitor = monitor
        self.controller = controller
        self.events = events
        self.redactor = redactor
        self.timing = timing or Timing()
        self.clock = self.timing.now
        self.purchases: list[Purchase] = []
        self.workers: list[Worker] = []
        self.before: dict | None = None
        self.after: dict | None = None
        self.phases: dict[str, float] = {}

    def phase(self, name: str) -> None:
        self.phases[name] = round(self.clock(), 3)
        self.events.write({"t": self.phases[name], "event": "PHASE", "phase": name})

    def preconditions(self) -> dict:
        before = self.probe(())
        check_genesis((before.get("chain") or {}).get("genesisHash"))
        problems = analysis.chain_problems(before) + analysis.inventory_problems(before)
        active = [o for o in before.get("orderIndex", []) if o["state"] not in TERMINAL_FAILURE + (SETTLED,)
                  and o["state"] != "UNCERTAIN"]
        uncertain = [o for o in before.get("orderIndex", []) if o["state"] == "UNCERTAIN"]
        if active or uncertain:
            problems.append(f"{len(active)} active and {len(uncertain)} uncertain order(s) exist; "
                            "reconcile them before another level")
        if before.get("dealerPolicy") != "ACTIVE":
            problems.append("the demo dealer's standing policy is not ACTIVE")
        if problems:
            raise EngineError("preconditions failed: " + "; ".join(problems))
        return before

    def execute(self) -> dict:
        self.phase("start")
        self.before = self.preconditions()
        if self.before_submit is not None:
            self.before_submit()
        self.monitor.start()
        try:
            self.phase("idleBefore")
            wait = self.plan.baseline_s if self.plan.level == 0 else self.plan.idle_before_s
            self.timing.wait(self.controller, wait)
            if self.plan.level > 0 and self.controller.may_submit():
                self.run_purchases()
            self.phase("idleAfter")
            if not self.controller.stopped:
                self.timing.wait(self.controller, self.plan.idle_after_s)
            self.phase("finalCheck")
            for worker in self.workers:  # read-only; settled receipts must still hold
                if worker.p.outcome == "SETTLED_VERIFIED":
                    worker.final_check()
            self.phase("finalProbe")
            order_ids = [p.order_id for p in self.purchases if p.order_id]
            try:
                self.after = self.probe(tuple(order_ids))
            except Exception as exc:  # noqa: BLE001
                self.controller.trigger("HARNESS_ERROR", f"final probe failed: {exc}")
                self.after = None
        finally:
            self.monitor.stop()
            self.phase("end")
        return self.finish()

    def run_purchases(self) -> None:
        self.purchases = self.plan.purchases()
        workers = [Worker(p, relay=self.relay, controller=self.controller, thresholds=self.t, events=self.events,
                          redactor=self.redactor, queue_position=i, wait_durability=self.plan.wait_durability,
                          poll=self.plan.poll_s, timing=self.timing, correlations=self.correlations)
                   for i, p in enumerate(self.purchases)]
        self.workers = workers
        threads = [threading.Thread(target=w.run, daemon=True, name=f"purchase-{w.p.label}") for w in workers]
        self.phase("purchasesStarted")
        for thread in threads:  # submitted together: this is the concurrency under test
            thread.start()
        deadline = time.monotonic() + self.t.level_timeout_s
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            self.controller.trigger("LEVEL_TIMEOUT", f"level not finished within {self.t.level_timeout_s}s")
            # Workers now drain: confirmed orders are observed, never cancelled.
            for thread in threads:
                thread.join(timeout=self.t.drain_timeout_s + 60)
        for worker, thread in zip(workers, threads):
            if thread.is_alive():
                worker.p.outcome = "IN_PROGRESS_AT_DRAIN_END"
        self.phase("purchasesFinished")

    def _probe_problem(self, p: Purchase, code: str, text: str) -> None:
        if not any(item["code"] == code for item in p.problems):
            p.problems.append({"code": code, "detail": text})
            self.controller.trigger(code, f"{p.label}: {text}", purchase=p.label)

    def finish(self) -> dict:
        problems: list[str] = []
        after = self.after
        if after is not None:
            for text in analysis.chain_problems(after):
                problems.append(text)
            inventory = analysis.inventory_problems(after)
            settled_by_offer: dict[str, int] = {}
            for p in self.purchases:
                if p.outcome in ("SETTLED_VERIFIED", "SETTLED_WITH_PROBLEMS", "SETTLED_NOT_DURABLE") and p.offer_id:
                    settled_by_offer[p.offer_id] = settled_by_offer.get(p.offer_id, 0) + p.quantity
            inventory += analysis.sold_delta_problems(self.before or {}, after, settled_by_offer)
            if inventory:
                self.controller.trigger("INVENTORY_INCONSISTENT", "; ".join(inventory)[:480])
            problems += inventory
            for p in self.purchases:
                detail = (after.get("orders") or {}).get(p.order_id) if p.order_id else None
                if detail and p.outcome.startswith("SETTLED"):
                    for code, text in analysis.receipt_problems(p.view or {}, detail, None, quantity=p.quantity,
                                                                payment=p.payment or 0):
                        if not any(item["detail"] == text for item in p.problems):
                            p.problems.append({"code": code, "detail": text})
                            self.controller.trigger(code, f"{p.label}: {text}", purchase=p.label)
                    artifacts = detail.get("artifactMtimeNs") or {}
                    if "dropped.json" in artifacts:
                        self._probe_problem(p, "DURABILITY_LOST", "the exchange recorded the settlement as dropped")
                    elif p.durability_required and "stabilization.json" not in artifacts:
                        self._probe_problem(p, "DURABILITY_NOT_OBSERVED", "no stabilization record for the order")
        samples = self.monitor.samples
        processes = analysis.processes_from_samples(samples)
        transactions = []
        for p in self.purchases:
            detail = ((after or {}).get("orders") or {}).get(p.order_id) if p.order_id else None
            latency = analysis.stage_latencies(detail or {}, p.timeline, processes)
            state = (detail or {}).get("state") or (p.view or {}).get("state")
            transactions.append({**p.public(), **latency, "finalEvidence": detail is not None,
                                 "reconciliation": analysis.reconcile(detail, state) if p.order_id else
                                 analysis.reconcile(None, None)})
        summary = {"level": self.plan.level, "concurrency": LEVELS[self.plan.level],
                   "phases": self.phases, "transactions": transactions, "consistencyProblems": problems,
                   "waitDurability": self.plan.wait_durability, "finalProbePresent": after is not None,
                   "monitoring": analysis.monitoring_validity(samples, self.phases,
                                                              getattr(self.monitor, "interval", 1.0)),
                   "resources": analysis.summarize_resources(samples), "relay": {
                       "calls": getattr(self.relay, "calls", None), "restarts": getattr(self.relay, "restarts", None)}}
        # Anything short of the acceptance rules is itself a stop condition, so no failure is silent.
        unmet = analysis.acceptance_problems(summary, include_stops=False)
        if unmet:
            self.controller.trigger("ACCEPTANCE_FAILED", "; ".join(unmet)[:480])
        summary["stopReason"], summary["stopReasons"] = self.controller.first, self.controller.reasons
        acceptance = analysis.acceptance_problems(summary)
        summary["acceptance"] = {"passed": not acceptance, "problems": acceptance,
                                 "durabilityRequired": self.plan.wait_durability}
        partial = any(t["reconciliation"]["class"] in ("IN_PROGRESS", "UNCERTAIN", "UNCONFIRMED")
                      or t["outcome"] in ("IN_PROGRESS_AT_DRAIN_END", "PURCHASE_AMBIGUOUS",
                                          "UNCONFIRMED_NEEDS_RECONCILE") for t in transactions)
        summary["result"] = "PASS" if not acceptance else ("PARTIAL" if partial else "STOPPED")
        return summary
