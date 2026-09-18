import importlib.util
import pathlib
import sqlite3
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bitaxe_monitor_incidents", ROOT / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class IncidentClassificationTests(unittest.TestCase):
    def test_calculated_current_uses_power_and_input_voltage(self):
        self.assertAlmostEqual(APP.calculated_current({"power": 19.9, "voltage": 5090}), 3.91, places=2)

    def test_a_controller_online_idle_power_is_mining_stall(self):
        before = {"hashRate": 1120, "power": 21, "voltage": 5000, "uptimeSeconds": 3600}
        during = {"hashRate": 0, "power": 5, "voltage": 5500, "uptimeSeconds": 3630}
        self.assertEqual(APP.classify_incident(before, during)[0], "MINING_STALL")

    def test_b_offline_reboot_and_power_on_is_power_interruption(self):
        before = {"uptimeSeconds": 3600}
        after = {"uptimeSeconds": 8, "resetReason": "Reset due to power-on event"}
        self.assertEqual(APP.classify_incident(before, {}, after, 40)[0], "POWER_INTERRUPTION")

    def test_c_short_api_outage_without_reboot_is_network_outage(self):
        before = {"uptimeSeconds": 3600}
        after = {"uptimeSeconds": 3650, "resetReason": "Reset due to power-on event"}
        self.assertEqual(APP.classify_incident(before, {}, after, 20)[0], "NETWORK_OR_API_OUTAGE")

    def test_d_overheat_is_thermal_event(self):
        self.assertEqual(APP.classify_incident({}, {"overheat_mode": True})[0], "THERMAL_EVENT")

    def test_e_stale_power_on_reason_does_not_create_power_incident(self):
        before = {"uptimeSeconds": 3600}
        during = {"hashRate": 1100, "power": 21, "uptimeSeconds": 3610,
                  "resetReason": "Reset due to power-on event"}
        self.assertEqual(APP.classify_incident(before, during)[0], "UNKNOWN")

    def test_f_single_reject_is_not_an_incident(self):
        before = {"sharesRejected": 0, "uptimeSeconds": 3600}
        during = {"sharesRejected": 1, "hashRate": 1100, "power": 21, "uptimeSeconds": 3610}
        self.assertEqual(APP.classify_incident(before, during)[0], "UNKNOWN")

    def test_real_outage_pattern_is_backfilled_from_existing_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "history.sqlite3")
            try:
                APP.init_db()
                base = int(time.time()) - 600
                APP.save({"hashRate": 1110, "power": 21.47, "voltage": 4922,
                          "temp": 60, "uptimeSeconds": 32379}, base)
                for offset in (10, 20, 30, 40):
                    APP.save({"hashRate": 0, "power": 5.0, "voltage": 5523,
                              "temp": 23, "uptimeSeconds": 32379 + offset}, base + offset)
                APP.save({"hashRate": 1100, "power": 20, "voltage": 5090,
                          "uptimeSeconds": 20, "resetReason": "Software reset via esp_restart"}, base + 50)
                APP.backfill_historical_incidents()
                with sqlite3.connect(APP.DB_PATH) as con:
                    row = con.execute("SELECT kind,before_sample,pre_stats FROM incidents").fetchone()
                self.assertEqual(row[0], "MINING_STALL")
                self.assertIn('"hashRate":1110', row[1])
                self.assertIn('"power"', row[2])
            finally:
                APP.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
