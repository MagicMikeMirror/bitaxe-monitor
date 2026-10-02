import pathlib
import tempfile
import unittest
from unittest.mock import patch

import app


DAY = 86400


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_path, self.previous_catalog = app.DB_PATH, app.catalog
        app.DB_PATH = str(pathlib.Path(self.directory.name) / "telemetry.sqlite3")
        app.catalog = None
        app.init_db()

    def tearDown(self):
        app.DB_PATH, app.catalog = self.previous_path, self.previous_catalog
        self.directory.cleanup()

    def sample(self, stamp, hashrate, domain=250):
        app.save({"hashRate": hashrate, "power": 20, "voltage": 5000,
                  "temp": 55, "vrTemp": 57, "fanrpm": 7000,
                  "coreVoltageActual": 1150, "actualFrequency": 525,
                  "errorPercentage": 1, "sharesAccepted": 99, "sharesRejected": 1,
                  "hashrateMonitor": {"asics": [{"domains": [domain] * 4}]}}, stamp)

    def test_hourly_aggregation_is_correct_and_idempotent(self):
        stamp = 500 * DAY
        hour = stamp - 40 * DAY
        self.sample(hour + 10, 1000); self.sample(hour + 20, 1200)
        app.run_retention(stamp=stamp)
        app.run_retention(stamp=stamp)
        with app.db() as con:
            row = con.execute("""SELECT min_value,max_value,avg_value,sample_count
                FROM telemetry_aggregates WHERE period='hour' AND metric='hashrate'""").fetchone()
            self.assertEqual(tuple(row), (1000, 1200, 1100, 2))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
            self.assertEqual(con.execute("""SELECT COUNT(*) FROM telemetry_aggregates
                WHERE period='hour' AND metric='hashrate'""").fetchone()[0], 1)

    def test_daily_aggregation_from_hourly_is_weighted(self):
        stamp = 500 * DAY
        old = stamp - 400 * DAY
        self.sample(old + 10, 1000); self.sample(old + 20, 1200); self.sample(old + 3610, 2000)
        app.run_retention(stamp=stamp)
        with app.db() as con:
            row = con.execute("""SELECT min_value,max_value,avg_value,sample_count
                FROM telemetry_aggregates WHERE period='day' AND metric='hashrate'""").fetchone()
            self.assertEqual((row["min_value"], row["max_value"], row["sample_count"]), (1000, 2000, 3))
            self.assertAlmostEqual(row["avg_value"], 1400)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM telemetry_aggregates WHERE period='hour'").fetchone()[0], 0)

    def test_failure_rolls_back_aggregates_and_raw_deletion(self):
        stamp = 500 * DAY
        self.sample(stamp - 40 * DAY, 1000)
        with self.assertRaises(RuntimeError):
            app.run_retention(stamp=stamp, fail_after_aggregation=True)
        with app.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM telemetry_aggregates").fetchone()[0], 0)

    def test_generation_databases_and_incidents_remain_isolated(self):
        stamp = 500 * DAY
        second = str(pathlib.Path(self.directory.name) / "second.sqlite3")
        self.sample(stamp - 40 * DAY, 1000)
        app.create_incident(stamp - 50 * DAY, "OFFLINE")
        with app.use_database(second):
            app.init_db(); self.sample(stamp - 40 * DAY, 2000)
        app.run_retention(app.DB_PATH, stamp)
        with app.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
        with app.use_database(second):
            with app.db() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM telemetry_aggregates").fetchone()[0], 0)

    def test_chart_blends_daily_hourly_and_raw(self):
        stamp = 500 * DAY
        self.sample(stamp - 400 * DAY, 800)
        self.sample(stamp - 40 * DAY, 1000)
        self.sample(stamp - DAY, 1200)
        app.run_retention(stamp=stamp)
        with patch.object(app, "now", return_value=stamp):
            rows = [row for row in app.chart_history(stamp - 450 * DAY, stamp, DAY) if not row.get("gap")]
        self.assertEqual({round(row["hashrate"]) for row in rows}, {800, 1000, 1200})

    def test_migration_keeps_existing_samples(self):
        self.sample(100, 1000)
        app.init_db()
        with app.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
            self.assertTrue(con.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone())

    def test_online_restored_classification(self):
        network = app.online_restored_details({"uptimeSeconds": 1000}, {"uptimeSeconds": 1060}, 100, 160)
        reboot = app.online_restored_details({"uptimeSeconds": 1000}, {"uptimeSeconds": 10}, 100, 160)
        self.assertEqual(network["classification"], "LIKELY_NETWORK_OR_API_OUTAGE")
        self.assertFalse(network["uptime_reset"])
        self.assertEqual(reboot["classification"], "POSSIBLE_DEVICE_REBOOT_OR_POWER_CYCLE")
        self.assertTrue(reboot["uptime_reset"])
        app.record_online_restored({"uptimeSeconds": 1000}, {"uptimeSeconds": 1060}, 100, 160)
        with app.db() as con:
            event = con.execute("SELECT kind,severity,details FROM events").fetchone()
        self.assertEqual((event["kind"], event["severity"]), ("ONLINE_RESTORED", "info"))
        self.assertIn("LIKELY_NETWORK_OR_API_OUTAGE", event["details"])


if __name__ == "__main__":
    unittest.main()
