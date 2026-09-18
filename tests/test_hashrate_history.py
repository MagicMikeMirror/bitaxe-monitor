import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bitaxe_monitor_hashrate", ROOT / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


def samples(start, end, rate=1120, step=10):
    return [(stamp, rate) for stamp in range(start, end, step)]


class TimeWeightedHashrateTests(unittest.TestCase):
    def test_a_continuous_24h_is_full_rate(self):
        end = 86400
        value = APP.time_weighted_hashrate(samples(0, end), 0, end)
        self.assertAlmostEqual(value["average"], 1120)
        self.assertEqual(value["coverage"], end)

    def test_b_half_online_half_offline_is_half_rate(self):
        end = 86400
        value = APP.time_weighted_hashrate(samples(0, end // 2), 0, end,
                                           [(end // 2, end)])
        self.assertAlmostEqual(value["average"], 560)
        self.assertEqual(value["coverage"], end)

    def test_c_device_reboot_does_not_reset_history(self):
        end = 86400
        before = samples(0, 43200)
        after = samples(43230, end)
        value = APP.time_weighted_hashrate(before + after, 0, end,
                                           [(43200, 43230)])
        self.assertAlmostEqual(value["average"], 1119.61, places=1)
        self.assertEqual(value["coverage"], end)

    def test_d_database_reopen_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "history.sqlite3")
            try:
                APP.init_db()
                for stamp, rate in samples(1000, 1100):
                    APP.save({"hashRate": rate}, stamp)
                APP.init_db()
                with APP.db() as con:
                    count = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                self.assertEqual(count, 10)
            finally:
                APP.DB_PATH = original

    def test_e_six_hours_reports_partial_coverage(self):
        end = 86400
        value = APP.time_weighted_hashrate(samples(end - 21600, end), 0, end)
        self.assertAlmostEqual(value["average"], 1120)
        self.assertEqual(value["coverage"], 21600)

    def test_f_one_missed_poll_is_not_an_outage(self):
        data = samples(0, 3600)
        data.remove((1800, 1120))
        value = APP.time_weighted_hashrate(data, 0, 3600)
        self.assertAlmostEqual(value["average"], 1120)
        self.assertEqual(value["coverage"], 3600)

    def test_g_seven_days_include_multiple_offline_phases(self):
        day = 86400
        end = 7 * day
        offline = [(day, 2 * day), (4 * day, 5 * day)]
        data = samples(0, day) + samples(2 * day, 4 * day) + samples(5 * day, end)
        value = APP.time_weighted_hashrate(data, 0, end, offline)
        self.assertAlmostEqual(value["average"], 800)
        self.assertEqual(value["coverage"], end)

    def test_h_database_summary_reads_confirmed_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            original = APP.DB_PATH
            APP.DB_PATH = str(pathlib.Path(directory) / "history.sqlite3")
            try:
                APP.init_db()
                end = 100000
                for stamp, rate in samples(end - 3600, end):
                    APP.save({"hashRate": rate}, stamp)
                with APP.db() as con:
                    con.execute("""INSERT INTO incidents
                        (started_at,ended_at,status,kind,severity,title,summary,facts,created_at,updated_at)
                        VALUES(?,?,'RESOLVED','NETWORK_OR_API_OUTAGE','warning','offline','offline','{}',?,?)""",
                        (end - 1800, end - 1200, end, end))
                result = APP.historical_hashrate(end)
                self.assertIsNotNone(result["avg_24h"])
                self.assertGreater(result["coverage_24h"], 0)
            finally:
                APP.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
