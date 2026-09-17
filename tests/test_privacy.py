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

    def test_old_power_on_reason_is_not_current_power_failure(self):
        start = {"voltage": 5520, "uptimeSeconds": 32000}
        recovery = {"resetReason": "Reset due to power-on event", "uptimeSeconds": 34000}
        self.assertNotIn("Stromversorgung", APP.incident_cause(start, recovery))

    def test_new_power_on_reset_is_power_failure(self):
        start = {"voltage": 5000, "uptimeSeconds": 32000}
        recovery = {"resetReason": "Reset due to power-on event", "uptimeSeconds": 4}
        self.assertIn("Stromversorgung", APP.incident_cause(start, recovery))

    def test_software_reset_after_stall_is_firmware_incident(self):
        start = {"voltage": 5520, "uptimeSeconds": 32000}
        recovery = {"resetReason": "Software reset via esp_restart", "uptimeSeconds": 2}
        self.assertIn("ASIC/Firmware", APP.incident_cause(start, recovery))


if __name__ == "__main__":
    unittest.main()
