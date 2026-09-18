import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bitaxe_monitor", ROOT / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class PrivacyTests(unittest.TestCase):
    def test_block_subsidy_follows_halvings(self):
        self.assertEqual(APP.block_subsidy(839999), 6.25)
        self.assertEqual(APP.block_subsidy(840000), 3.125)

    def test_market_candles_are_sorted_and_summarized(self):
        candles = [[300, 99, 112, 100, 110, 1], [100, 89, 101, 90, 100, 1],
                   [200, 94, 106, 95, 105, 1], [10, 1, 2, 1, 2, 1]]
        history, change, low, high = APP.summarize_candles(candles, 100)
        self.assertEqual([point["ts"] for point in history], [100, 200, 300])
        self.assertEqual(change, 10)
        self.assertEqual(low, 89)
        self.assertEqual(high, 112)

    def test_mining_address_is_transient_only(self):
        raw = {"stratumUser": "bc1q-test-address.worker"}
        self.assertEqual(APP.mining_address(raw), "bc1q-test-address")
        self.assertNotIn("stratumUser", APP.clean(raw))

    def test_sensitive_api_fields_are_discarded(self):
        raw = {
            "power": 21.5,
            "hashRate": 1100,
            "stratumUser": "bc1q-private-wallet.worker",
            "stratumPassword": "secret",
            "stratumURL": "private.pool.example",
            "ssid": "private-wifi",
            "macAddr": "00:11:22:33:44:55",
            "ipv4": "192.168.1.99",
            "pools": [{"stratumUser": "bc1q-private-wallet"}],
        }
        encoded = json.dumps(APP.clean(raw))
        for secret in ("bc1q", "secret", "private.pool", "private-wifi", "00:11", "192.168.1.99"):
            self.assertNotIn(secret, encoded)

    def test_power_fault_is_observed_cause(self):
        self.assertIn("Power Fault detected", APP.observed_cause({"power_fault": "Power Fault Detected."}))

    def test_metrics_do_not_invent_a_cause(self):
        data = {"hashRate": 0, "power": 5, "voltage": 5530, "uptimeSeconds": 34000,
                "resetReason": "Reset due to power-on event"}
        self.assertIsNone(APP.observed_cause(data))

    def test_replay_of_2026_09_17_incident_stays_unknown_without_fault_field(self):
        before = {"hashRate": 1110.25, "power": 21.47, "voltage": 4921.88,
                  "temp": 60, "uptimeSeconds": 32379}
        stopped = {"hashRate": 0, "power": 5.0, "voltage": 5523.43,
                   "temp": 22.75, "uptimeSeconds": 32519,
                   "resetReason": "Reset due to power-on event"}
        restarted = {"hashRate": 0, "power": 8.88, "voltage": 5406.25,
                     "uptimeSeconds": 0, "resetReason": "Software reset via esp_restart"}
        self.assertIsNone(APP.observed_cause(stopped))
        self.assertIsNone(APP.observed_cause(restarted))
        self.assertIn("1110 GH/s", APP.metrics_text(before))


if __name__ == "__main__":
    unittest.main()
