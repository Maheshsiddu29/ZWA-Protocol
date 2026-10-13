"""Security regressions for price provenance and multi-asset identity."""

import unittest

from exchange.pricing import (InvalidPrice, IssuerNAVProvider, PrivateQuoteProvider,
                              XAU_USD_FEED_ID, parse_pyth_xau, sign_statement)
from exchange.registry import (InvalidInstrument, assert_distinct_assets,
                               load_catalog, with_issuance)

NOW = 1_800_000_000
KEY = b"k" * 32


class PriceTests(unittest.TestCase):
    def test_pyth_identity_unit_exponent_freshness_and_confidence(self):
        payload = {"parsed": [{"id": XAU_USD_FEED_ID, "price": {
            "price": "275012345000", "conf": "120000000", "expo": -8,
            "publish_time": NOW - 15}}]}
        good = parse_pyth_xau(payload, now=NOW)
        self.assertEqual(str(good.decimal_value), "2750.12345000")
        self.assertEqual(good.unit, "USD_PER_TROY_OUNCE")
        self.assertEqual(good.trust, "HTTPS_TRANSPORT_ONLY")
        for field, bad in (("id", "0" * 64),):
            mutated = {"parsed": [{**payload["parsed"][0], field: bad}]}
            with self.assertRaises(InvalidPrice): parse_pyth_xau(mutated, now=NOW)
        for field, bad in (("expo", -18), ("publish_time", NOW - 3601),
                           ("publish_time", NOW + 31), ("conf", "275012345001"),
                           ("price", "0")):
            point = {**payload["parsed"][0]["price"], field: bad}
            with self.subTest(field=field, bad=bad), self.assertRaises(InvalidPrice):
                parse_pyth_xau({"parsed": [{"id": XAU_USD_FEED_ID, "price": point}]}, now=NOW)

    def test_nav_authentication_and_stale_rejection(self):
        statement = {"version": 1, "instrumentId": "zwa-treasury-style",
                     "issuerId": "local-issuer", "navMicrousdPerUnit": 104200000,
                     "observedAt": NOW - 60, "expiresAt": NOW + 300,
                     "nonce": "a" * 32}
        provider = IssuerNAVProvider({"local-issuer": KEY})
        signature = sign_statement(statement, KEY)
        self.assertEqual(provider.read(statement, signature, now=NOW).unit, "USD_PER_SYNTHETIC_UNIT")
        with self.assertRaises(InvalidPrice):
            provider.read({**statement, "navMicrousdPerUnit": 999000000}, signature, now=NOW)
        with self.assertRaises(InvalidPrice):
            provider.read(statement, signature, now=NOW + 301)
        with self.assertRaises(InvalidPrice):
            IssuerNAVProvider({}).read(statement, signature, now=NOW)

    def test_private_quote_exact_asset_quantity_and_expiry(self):
        statement = {"version": 1, "instrumentId": "zwa-credit-participation",
                     "assetBase": "a" * 64, "sellerId": "local-seller", "quantity": 5,
                     "totalZat": 11500, "observedAt": NOW - 1,
                     "expiresAt": NOW + 100, "nonce": "b" * 32}
        provider = PrivateQuoteProvider({"local-seller": KEY})
        signature = sign_statement(statement, KEY)
        self.assertEqual(provider.read(statement, signature, now=NOW).numerator, 11500)
        for mutation in ({"assetBase": "c" * 64}, {"quantity": 7}, {"totalZat": 1}):
            with self.assertRaises(InvalidPrice):
                provider.read({**statement, **mutation}, signature, now=NOW)
        with self.assertRaises(InvalidPrice):
            provider.read(statement, signature, now=NOW + 101)


class RegistryTests(unittest.TestCase):
    def test_distinct_issuance_and_status(self):
        items = load_catalog()
        self.assertEqual(len(items), 3)
        self.assertTrue(all(item.asset_base is None and item.availability == "CATALOG_ONLY"
                            for item in items.values()))
        gold = with_issuance(items["zwa-gold-reference"],
                             {"asset_base": "a" * 64, "txid": "b" * 64},
                             observed_on_chain=True, seller_inventory_units=100)
        credit = with_issuance(items["zwa-credit-participation"],
                               {"asset_base": "c" * 64, "txid": "d" * 64},
                               observed_on_chain=True, seller_inventory_units=100)
        self.assertEqual(gold.availability, "EXPERIMENTAL_TRADABLE")
        assert_distinct_assets([gold, credit])
        with self.assertRaises(InvalidInstrument):
            assert_distinct_assets([gold, with_issuance(items["zwa-treasury-style"],
                                {"asset_base": "a" * 64, "txid": "e" * 64},
                                observed_on_chain=True)])
        with self.assertRaises(InvalidInstrument):
            with_issuance(items["zwa-gold-reference"],
                          {"asset_base": "a" * 64, "txid": "b" * 64},
                          observed_on_chain=False)


if __name__ == "__main__":
    unittest.main()
