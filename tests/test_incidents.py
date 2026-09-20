import importlib.util
import inspect
import json
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
    def test_efficiency_j_th_and_zero_hashrate(self):
        self.assertAlmostEqual(APP.mining_efficiency(23.0, 1210), 19.0, places=1)
        self.assertIsNone(APP.mining_efficiency(23.0, 0))
        self.assertIsNone(APP.mining_efficiency(None, 1210))

    def test_diagnostic_snapshot_keeps_full_available_fields_and_zero_domains(self):
        data = {"hashRate": 270, "hashRate_1m": 300, "hashRate_10m": 800,
                "hashRate_1h": 1100, "expectedHashrate": 1224, "power": 23,
                "voltage": 4860, "temp": 61, "vrTemp": 63, "fanrpm": 7000,
                "fanspeed": 72, "actualFrequency": 600, "coreVoltage": 1150,
                "coreVoltageActual": 1144, "errorPercentage": 0.2,
                "sharesAccepted": 100, "sharesRejected": 1, "workReceived": 50,
                "responseTime": 120, "uptimeSeconds": 3600,
                "resetReason": "Software reset", "version": "v2.15.1",
                "hashrateMonitor": {"asics": [{"errorCount": 1567,
                                                 "domains": [0, 287, 0, 0]}]}}
        snapshot = APP.diagnostic_snapshot(data)["observed"]
        self.assertEqual(snapshot["hashrateMonitor"]["asics"][0]["domains"], [0, 287, 0, 0])
        self.assertEqual(snapshot["hashrateMonitor"]["asics"][0]["errorCount"], 1567)
        for field in ("hashRate", "hashRate_1m", "hashRate_10m", "hashRate_1h",
                      "expectedHashrate", "power", "voltage", "temp", "vrTemp",
                      "fanrpm", "fanspeed", "actualFrequency", "coreVoltage",
                      "coreVoltageActual", "sharesAccepted", "sharesRejected",
                      "workReceived", "responseTime", "uptimeSeconds", "version"):
            self.assertIn(field, snapshot)

    def test_diagnostic_snapshot_tolerates_missing_optional_fields(self):
        snapshot = APP.diagnostic_snapshot({"hashRate": 0})["observed"]
        self.assertEqual(snapshot["hashRate"], 0)
        self.assertIsNone(snapshot["efficiencyJTh"])

    def test_error_delta_minima_window_and_counter_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "metrics.sqlite3")
            try:
                APP.init_db()
                start = 10_000
                for offset, voltage, errors in ((-300, 4900, 900), (-50, 4830, 950), (-10, 4860, 980)):
                    APP.save({"hashRate": 1200, "power": 23, "voltage": voltage,
                              "hashrateMonitor": {"asics": [{"errorCount": errors,
                                  "domains": [300, 300, 300, 300]}]}}, start + offset)
                metrics = APP.incident_metrics(start, {"voltage": 4860,
                    "hashrateMonitor": {"asics": [{"errorCount": 1567,
                                                    "domains": [0, 287, 0, 0]}]}})
                self.assertEqual(metrics["voltage_min_60s"], 4830)
                self.assertEqual(metrics["voltage_min_5m"], 4830)
                self.assertEqual(metrics["error_count_delta"], 587)
                self.assertEqual(metrics["domains"], [0, 287, 0, 0])
                reset = APP.incident_metrics(start, {"hashrateMonitor": {"asics": [
                    {"errorCount": 4, "domains": [0, 287, 0, 0]}]}})
                self.assertIsNone(reset["error_count_delta"])
            finally:
                APP.DB_PATH = original

    def test_incident_window_is_exactly_five_minutes_around_incident(self):
        self.assertEqual(APP.incident_window(1000, 1120), {"start": 700, "end": 1420})
        self.assertIn('id="incidentHash"', APP.HTML)
        self.assertIn('id="incidentErrors"', APP.HTML)
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("ASIC_DOMAIN_STALL_DETECTED", source)
        self.assertNotIn("ASIC_DOMAIN_STALL_DETECASIC", source)

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
                    layouts = con.execute("SELECT name FROM sqlite_master WHERE name='dashboard_layouts'").fetchone()
                self.assertTrue({"incident_id", "automatic", "details"}.issubset(columns))
                self.assertEqual(message, "old event")
                self.assertIsNotNone(diagnostics)
                self.assertIsNotNone(layouts)
            finally:
                APP.DB_PATH = original

    def test_named_layout_validation_requires_every_widget_once(self):
        layout = [{"id": widget, "collapsed": index == 0, "hidden": index == 1}
                  for index, widget in enumerate(APP.LAYOUT_WIDGETS)]
        validated = APP.validate_dashboard_layout(layout)
        self.assertEqual([item["id"] for item in validated], list(APP.LAYOUT_WIDGETS))
        self.assertTrue(validated[0]["collapsed"])
        self.assertTrue(validated[1]["hidden"])
        with self.assertRaises(ValueError):
            APP.validate_dashboard_layout(layout[:-1])
        with self.assertRaises(ValueError):
            APP.validate_dashboard_layout(layout[:-1] + [layout[0]])

    def test_standard_layout_is_not_written_to_database(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "layouts.sqlite3")
            try:
                APP.init_db()
                with APP.db() as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM dashboard_layouts").fetchone()[0], 0)
            finally:
                APP.DB_PATH = original

    def test_custom_layout_requires_own_non_standard_name(self):
        self.assertEqual(APP.validate_layout_name("  TV Ansicht  "), "TV Ansicht")
        for value in (None, "", "Standard", "standardlayout", "x" * 41):
            with self.assertRaises(ValueError):
                APP.validate_layout_name(value)

    def test_named_custom_layout_can_be_updated_but_not_created_by_overwrite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "layouts.sqlite3")
            try:
                APP.init_db()
                first = APP.validate_dashboard_layout([
                    {"id": widget, "collapsed": False, "hidden": False}
                    for widget in APP.LAYOUT_WIDGETS])
                changed = APP.validate_dashboard_layout([
                    {"id": widget, "collapsed": index == 0, "hidden": index == 1}
                    for index, widget in enumerate(APP.LAYOUT_WIDGETS)])
                self.assertTrue(APP.store_dashboard_layout("Andi", first))
                self.assertTrue(APP.store_dashboard_layout("Andi", changed, overwrite=True))
                self.assertFalse(APP.store_dashboard_layout("Fehlt", changed, overwrite=True))
                with APP.db() as con:
                    saved = json.loads(con.execute(
                        "SELECT layout_json FROM dashboard_layouts WHERE name='Andi'").fetchone()[0])
                self.assertTrue(saved[0]["collapsed"])
                self.assertTrue(saved[1]["hidden"])
            finally:
                APP.DB_PATH = original

    def test_layout_ui_offers_update_and_copy_actions(self):
        self.assertIn('id="layoutUpdate" hidden>Änderungen speichern', APP.HTML)
        self.assertIn('id="layoutSave" hidden>Als neues Layout speichern', APP.HTML)
        self.assertIn("overwrite:true", APP.HTML)

    def test_mining_profiles_keep_cooling_and_power_settings_together(self):
        expected = {
            "eco": (490, 1100, 65, 1), "standard": (525, 1150, 65, 1),
            "oc": (650, 1180, 60, 1), "performance": (725, 1220, 57, 1),
        }
        for key, values in expected.items():
            profile = APP.MINING_PROFILES[key]
            self.assertEqual((profile["frequency"], profile["coreVoltage"],
                              profile["temptarget"], profile["overclockEnabled"]), values)
            self.assertEqual(profile["autofanspeed"], 1)

    def test_active_profile_requires_frequency_voltage_and_cooling_match(self):
        standard = {"frequency": 525, "coreVoltage": 1150, "temptarget": 65,
                    "autofanspeed": 1, "overclockEnabled": 1}
        self.assertEqual(APP.active_mining_profile(standard), "standard")
        self.assertIsNone(APP.active_mining_profile(standard | {"autofanspeed": 0}))
        self.assertIsNone(APP.active_mining_profile(standard | {"temptarget": 60}))

    def test_profile_update_sends_one_complete_patch(self):
        captured = {}
        original = APP.urllib.request.urlopen
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def fake(request, timeout):
            captured.update(json.loads(request.data))
            self.assertEqual(request.method, "PATCH")
            return Response()
        APP.urllib.request.urlopen = fake
        try:
            APP.update_axeos_profile(APP.MINING_PROFILES["oc"])
        finally:
            APP.urllib.request.urlopen = original
        self.assertEqual(captured, {"frequency": 650, "coreVoltage": 1180,
                                   "temptarget": 60, "autofanspeed": 1,
                                   "overclockEnabled": 1})

    def test_profile_ui_and_handler_do_not_restart_axeos(self):
        self.assertIn("ohne Neustart", APP.HTML)
        profile_handler = inspect.getsource(APP.Handler.do_POST)
        profile_section = profile_handler.split('if path == "/api/settings/mining-profile":', 1)[1]
        profile_section = profile_section.split('if path == "/api/layouts":', 1)[0]
        self.assertNotIn("request_axeos_restart", profile_section)
        self.assertIn('"status": "applied"', profile_section)

    def test_hidden_and_collapsed_cards_have_non_empty_view_states(self):
        self.assertIn(".card[hidden]{display:none!important}", APP.HTML)
        self.assertIn("card.hidden=card.classList.contains('is-hidden')", APP.HTML)
        self.assertIn(".card.is-collapsed .widgetbar{display:flex}", APP.HTML)

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
