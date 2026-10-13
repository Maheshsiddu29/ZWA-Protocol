# Checkout read-only Postman checks

Import `ZWA-Checkout-ReadOnly.postman_collection.json` as a separate Collection v2.1 asset. It contains only GET requests. From `ZWA-Protocol/`, run:

```sh
python3 -m checkout.api --demo --port 18745 --rpc-port 18255
```

The collection defaults to `http://127.0.0.1:18745`. The owner-run node was stopped, so the recorded Phase 3.5H session and receipt return HTTP 200 with `RECORDED_REGTEST_EVIDENCE`, while the old digest requested as a live session returns HTTP 503 `NODE_UNAVAILABLE`. The collection's fixed txid and block hash refer only to the owner-run transaction documented in `docs/checkout-phase35h-human-approval.md`.

For the **Receipt not yet available** request, set `quote_ready_session_id` to the real digest from a fresh active local coordinator after `prepare-sale` and before execution. Skip this request without such a session. The **Unavailable backend** request intentionally targets an unused loopback port; Postman reports a transport error and does not run an assertion script. The UI must interpret it as unavailable. The `Live session after node stopped` assertion is valid only while the original node remains stopped. Do not use a newly initialized chain to interpret that historical txid.

Postman/Newman was not installed for this validation. The collection JSON parsed and all eight embedded assertion scripts compiled with Node; equivalent real localhost HTTP checks ran against the checkout service. No Postman Runner pass is claimed.
