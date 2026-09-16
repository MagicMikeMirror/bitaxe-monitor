import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bitaxe_monitor", ROOT / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class PrivacyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
