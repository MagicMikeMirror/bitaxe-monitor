import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
from device_history import Catalog, backup, connection
from telemetry import summarize


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.previous_path, self.previous_catalog = app.DB_PATH, app.catalog
        app.DB_PATH, app.catalog = str(self.root / 'bitaxe.sqlite3'), None
        app.init_db()
        self.raw = {'macAddr': '00:11:22:33:44:55', 'hashRate': 1000, 'power': 20,
                    'voltage': 5000, 'uptimeSeconds': 100, 'frequency': 525, 'coreVoltage': 1150,
                    'sharesAccepted': 1, 'sharesRejected': 0,
                    'hashrateMonitor': {'asics': [{'domains': [250]*4, 'errorCount': 10}]}}

    def tearDown(self):
        app.DB_PATH, app.catalog = self.previous_path, self.previous_catalog
        self.directory.cleanup()

    def catalog(self):
        app.catalog = Catalog(app.DB_PATH)
        return app.catalog

    def test_unknown_device_never_adopts_legacy_identity(self):
        app.save(app.clean(self.raw), 100)
        catalog = self.catalog()
        self.assertFalse(catalog.observe(self.raw, app.clean(self.raw)))
        self.assertIsNone(catalog.get()['fingerprint'])
        self.assertEqual(catalog.active(), 'legacy')
        self.assertNotIn(self.raw['macAddr'], json.dumps(catalog.public()))

    def test_offline_start_does_not_create_replacement_generation(self):
        app.initialize_storage()
        self.assertEqual(len(app.catalog.public()['generations']), 1)
        with patch.object(app, 'fetch_bitaxe_info', side_effect=TimeoutError):
            app.poll_generation()
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM incidents').fetchone()[0], 0)
        self.assertEqual(app.state_value('health_state'), 'WAITING FOR DEVICE')

    def test_switch_preserves_old_data_and_resets_state(self):
        app.save(app.clean(self.raw), 100)
        app.set_state_value('auto_restart_enabled', 'true')
        app.set_state_value('user_pause_active', 'true')
        incident = app.create_incident(110)
        catalog = self.catalog()
        catalog.switch('legacy', 'binding-0001', 'Altgerät', self.raw, app.initialize_database, True)
        new_raw = {**self.raw, 'macAddr': '00:11:22:33:44:66'}
        new = catalog.switch('legacy', 'switch-0001', 'Austausch', new_raw, app.initialize_database)
        self.assertNotEqual(new, 'legacy')
        self.assertIsNone(app.previous())
        self.assertFalse(app.auto_restart_enabled())
        self.assertFalse(app.user_pause_active())
        app.save(app.clean(new_raw), 100)  # same timestamp in another generation is independent
        with app.use_database(catalog.database('legacy')):
            self.assertEqual(app.previous()['hashRate'], 1000)
            with app.db() as con:
                row = con.execute('SELECT status,recovery FROM incidents WHERE id=?', (incident,)).fetchone()
            self.assertEqual(row['status'], 'ARCHIVED')
            self.assertIn('Monitoring beendet', row['recovery'])
        self.assertEqual(catalog.switch('legacy', 'switch-0001', 'Austausch', new_raw, app.initialize_database), new)
        self.assertEqual(len(catalog.public()['generations']), 2)
        self.assertTrue((self.root/'backups/switch-0001/.device-identity.key').exists())

    def test_backup_failure_keeps_generation_active(self):
        catalog = self.catalog()
        with patch('device_history.backup', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                catalog.switch('legacy', 'switch-0002', 'Austausch', self.raw, app.initialize_database)
        self.assertEqual(catalog.active(), 'legacy')
        self.assertEqual(len(catalog.public()['generations']), 1)

    def test_crash_after_intent_is_finished_on_restart(self):
        catalog = self.catalog()
        with patch.object(catalog, 'finish_operations', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                catalog.switch('legacy', 'switch-0003', 'Austausch', self.raw, app.initialize_database)
        self.assertEqual(catalog.active(), 'legacy')
        reopened = Catalog(app.DB_PATH)
        reopened.finish_operations()
        self.assertNotEqual(reopened.active(), 'legacy')
        reopened.finish_operations()
        self.assertEqual(len(reopened.public()['generations']), 2)

    def test_backup_restores_committed_wal_and_counts(self):
        app.save(app.clean(self.raw), 100)
        target = self.root/'backup.sqlite3'
        result = backup(app.DB_PATH, target)
        self.assertEqual(result['tables']['samples'], 1)
        with app.use_database(str(target)):
            app.init_db()
            self.assertEqual(app.previous()['hashRate'], 1000)
        self.assertEqual(len(result['sha256']), 64)

    def test_duplicate_timestamps_do_not_replace_samples(self):
        app.save({'hashRate': 1000}, 100)
        app.save({'hashRate': 900}, 100)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM samples').fetchone()[0], 2)

    def test_boot_and_counter_epoch_boundaries(self):
        first = app.clean(self.raw)
        app.annotate_sample(first, 100);app.save(first, 100)
        second = app.clean({**self.raw, 'uptimeSeconds': 110})
        app.annotate_sample(second, 110);app.save(second, 110)
        self.assertEqual(first['bootSession'], second['bootSession'])
        self.assertEqual(first['counterEpoch'], second['counterEpoch'])
        paused = app.clean({**self.raw, 'uptimeSeconds': 120, 'miningPaused': True})
        app.annotate_sample(paused, 120);app.save(paused, 120)
        self.assertEqual(second['bootSession'], paused['bootSession'])
        self.assertNotEqual(second['counterEpoch'], paused['counterEpoch'])
        rebooted = app.clean({**self.raw, 'uptimeSeconds': 2})
        app.annotate_sample(rebooted, 130)
        self.assertNotEqual(paused['bootSession'], rebooted['bootSession'])

    def test_invalid_domain_keeps_position_and_never_triggers_restart(self):
        sample = app.clean({'hashrateMonitor': {'asics': [{'domains': [300, None, 300, 0]}]}})
        self.assertEqual(sample['hashrateMonitor']['asics'][0]['domains'], [300,None,300,0])
        self.assertIsNone(app.domain_stall_evidence(sample))

    def test_counter_reset_and_pause_do_not_become_error_delta(self):
        a = {**app.clean(self.raw), 'ts': 100}
        b = {**app.clean({**self.raw, 'uptimeSeconds':110}), 'ts':110}
        b['hashrateMonitor']['asics'][0]['errorCount'] = 20
        c = {**app.clean({**self.raw, 'uptimeSeconds':120}), 'ts':120}
        c['hashrateMonitor']['asics'][0]['errorCount'] = 1
        result = summarize([a,b,c],120,60)
        self.assertEqual(result['error_delta'],10)
        self.assertTrue(result['counter_interrupted'])
        self.assertIsNone(result['reject_pct'])
        self.assertAlmostEqual(result['efficiency_jth'],20)

    def test_missing_identity_blocks_switch(self):
        catalog = self.catalog()
        with self.assertRaises(ValueError):
            catalog.switch('legacy','switch-0004','Austausch',{},app.initialize_database)
        self.assertEqual(catalog.active(),'legacy')

    def test_missing_key_fails_closed(self):
        catalog = self.catalog()
        catalog.switch('legacy','binding-0002','Altgerät',self.raw,app.initialize_database,True)
        catalog.key_path.unlink()
        with self.assertRaises(RuntimeError):
            Catalog(app.DB_PATH)

    def test_changed_device_invalidates_preview(self):
        catalog = self.catalog()
        catalog.observe(self.raw,app.clean(self.raw))
        preview=catalog.preview()
        catalog.validate_preview(preview['token'],self.raw)
        with self.assertRaises(ValueError):
            catalog.validate_preview(preview['token'],{**self.raw,'macAddr':'00:11:22:33:44:77'})

    def test_new_generation_disables_environment_auto_restart(self):
        catalog=self.catalog()
        catalog.switch('legacy','switch-environment-1','Austausch',self.raw,app.initialize_database)
        with patch.object(app,'AUTO_RESTART_ENABLED',True):
            self.assertFalse(app.auto_restart_enabled())

    def test_regulator_fault_is_not_external_power_loss(self):
        self.assertEqual(app.classify_incident({}, {'power_fault':'Regulator fault'})[0], 'REGULATOR_FAULT')

    def test_http_reads_empty_and_archived_generation(self):
        import threading
        import urllib.request
        self.catalog()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(app.Handler, 'log_message'):
            thread.start()
            try:
                for endpoint in ('/api/generations','/api/current','/api/diagnostics','/api/timeline',
                                 '/api/history','/api/storage','/api/layouts','/api/generations/preview','/healthz'):
                    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}'+endpoint, timeout=5) as response:
                        self.assertEqual(response.status,200,endpoint)
                        json.load(response)
                app.catalog.switch('legacy','http-switch-01','Austausch',self.raw,app.initialize_database)
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/current?generation=legacy',timeout=5) as response:
                    self.assertEqual(response.status,200)
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/settings/auto-restart?generation=legacy',
                                               data=b'{"enabled":true}',headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request,timeout=5)
                self.assertEqual(error.exception.code,409)
                error.exception.close()
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
