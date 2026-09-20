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
    def test_auto_restart_detects_safe_partial_hashrate_loss(self):
        original = APP.AUTO_RESTART_MIN_UPTIME
        APP.AUTO_RESTART_MIN_UPTIME = 900
        try:
            evidence = APP.hashrate_degradation({
                "hashRate": 300, "expectedHashrate": 1224, "uptimeSeconds": 3600,
                "power": 22.5, "actualFrequency": 600, "miningPaused": False,
                "isUsingFallbackStratum": False, "overheat_mode": 0,
            })
            self.assertAlmostEqual(evidence["threshold"], 367.2)
            self.assertAlmostEqual(evidence["loss_pct"], 75.49, places=1)
        finally:
            APP.AUTO_RESTART_MIN_UPTIME = original

    def test_auto_restart_refuses_faults_warmup_and_idle_power(self):
        base = {"hashRate": 300, "expectedHashrate": 1224, "uptimeSeconds": 3600,
                "power": 22.5, "actualFrequency": 600}
        for change in ({"power_fault": "fault"}, {"overheat_mode": 1},
                       {"miningPaused": True}, {"power": 5}, {"uptimeSeconds": 30}):
            self.assertIsNone(APP.hashrate_degradation(base | change))

    def test_restart_url_is_derived_without_retaining_info_path(self):
        self.assertEqual(APP.axeos_restart_url("http://192.168.1.131/api/system/info"),
                         "http://192.168.1.131/api/system/restart")

    def test_persistent_auto_restart_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            original_path, original_env = APP.DB_PATH, APP.AUTO_RESTART_ENABLED
            APP.DB_PATH = str(pathlib.Path(directory) / "setting.sqlite3")
            APP.AUTO_RESTART_ENABLED = False
            try:
                APP.init_db()
                self.assertFalse(APP.auto_restart_enabled())
                APP.set_state_value("auto_restart_enabled", "true")
                self.assertTrue(APP.auto_restart_enabled())
            finally:
                APP.DB_PATH, APP.AUTO_RESTART_ENABLED = original_path, original_env

    def test_existing_database_migrates_without_losing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "legacy.sqlite3")
            try:
                with sqlite3.connect(APP.DB_PATH) as con:
                    con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY,ts INTEGER,kind TEXT,severity TEXT,message TEXT)")
                    con.execute("INSERT INTO events VALUES(1,100,'START','info','old event')")
                APP.init_db()
                with APP.db() as con:
                    columns = {row[1] for row in con.execute("PRAGMA table_info(events)")}
                    message = con.execute("SELECT message FROM events WHERE id=1").fetchone()[0]
                    diagnostics = con.execute("SELECT name FROM sqlite_master WHERE name='incident_diagnostics'").fetchone()
                self.assertTrue({"incident_id", "automatic", "details"}.issubset(columns))
                self.assertEqual(message, "old event")
                self.assertIsNotNone(diagnostics)
            finally:
                APP.DB_PATH = original

    def test_auto_restart_outcome_retries_once_then_locks(self):
        verify = APP.AUTO_RESTART_VERIFY_SECONDS
        self.assertEqual(APP.auto_restart_outcome(1, verify - 1, False), "waiting")
        self.assertEqual(APP.auto_restart_outcome(1, verify, False), "retry")
        self.assertEqual(APP.auto_restart_outcome(2, verify, False), "lock")
        self.assertEqual(APP.auto_restart_outcome(2, verify, True), "recovered")

    def test_domain_values_are_kept_with_exact_paths_and_not_summed(self):
        data = APP.clean({"hashrateMonitor": {"asics": [{"total": 1245.5,
            "domains": [296.3, 328.5, 315.6, 287.7]}]}})
        paths = APP.domain_register_paths(data)
        self.assertEqual(paths["hashrateMonitor.asics[0].domains[3]"], 287.7)
        self.assertNotIn("sum", APP.diagnostic_snapshot(data)["derived"])

    def test_domain_stall_detects_three_dead_domains_despite_hashrate_spike(self):
        data = {"hashRate": 390, "uptimeSeconds": 3600, "power": 22.6,
                "actualFrequency": 600, "miningPaused": False,
                "isUsingFallbackStratum": False, "overheat_mode": 0,
                "hashrateMonitor": {"asics": [{"domains": [0, 390, 0, 0]}]}}
        evidence = APP.domain_stall_evidence(data)
        self.assertEqual(evidence["stalled_indexes"], [0, 2, 3])
        self.assertEqual(evidence["active_count"], 1)

    def test_domain_stall_respects_safety_gates_and_requires_all_domains_for_recovery(self):
        base = {"uptimeSeconds": 3600, "power": 22.6, "actualFrequency": 600,
                "hashrateMonitor": {"asics": [{"domains": [0, 330, 0, 0]}]}}
        for change in ({"power_fault": "fault"}, {"overheat_mode": 1},
                       {"miningPaused": True}, {"power": 5}, {"uptimeSeconds": 30}):
            self.assertIsNone(APP.domain_stall_evidence(base | change))
        self.assertFalse(APP.domains_recovered(base))
        self.assertTrue(APP.domains_recovered({"hashrateMonitor": {"asics": [
            {"domains": [280, 310, 340, 290]}]}}))

    def test_snapshot_and_event_commit_before_restart_request(self):
        with tempfile.TemporaryDirectory() as directory:
            original_path, original_fetch, original_restart = APP.DB_PATH, APP.fetch_bitaxe_info, APP.request_axeos_restart
            APP.DB_PATH = str(pathlib.Path(directory) / "forensics.sqlite3")
            APP.init_db()
            incident_id = APP.create_incident(100, "HASHRATE_DEGRADATION")
            order = []
            APP.fetch_bitaxe_info = lambda: {"hashRate": 300, "hashrateMonitor": {"asics": [{"domains": [1,2,3,4]}]}}
            def restart():
                with APP.db() as con:
                    order.append(con.execute("SELECT COUNT(*) FROM incident_diagnostics").fetchone()[0])
                    order.append(con.execute("SELECT COUNT(*) FROM events WHERE kind='AUTO_RECOVERY_RESTART'").fetchone()[0])
            APP.request_axeos_restart = restart
            try:
                APP.restart_with_persisted_evidence(incident_id, {}, {"reason": "test", "attempt": 1}, 200)
                self.assertEqual(order, [1, 1])
            finally:
                APP.DB_PATH, APP.fetch_bitaxe_info, APP.request_axeos_restart = original_path, original_fetch, original_restart

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
                APP.save({"hashRate": 0, "power": 8.88, "voltage": 5406,
                          "uptimeSeconds": 0, "resetReason": "Software reset via esp_restart"}, base + 50)
                APP.save({"hashRate": 1100, "power": 20, "voltage": 5090,
                          "uptimeSeconds": 20, "resetReason": "Software reset via esp_restart"}, base + 60)
                APP.backfill_historical_incidents()
                with sqlite3.connect(APP.DB_PATH) as con:
                    row = con.execute("SELECT kind,before_sample,pre_stats,ended_at,after_sample FROM incidents").fetchone()
                self.assertEqual(row[0], "MINING_STALL")
                self.assertIn('"hashRate":1110', row[1])
                self.assertIn('"power"', row[2])
                self.assertEqual(row[3], base + 60)
                self.assertIn('"hashRate":1100', row[4])
            finally:
                APP.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
