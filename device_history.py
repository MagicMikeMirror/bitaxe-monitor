"""Generation catalog. Each generation owns a complete, isolated telemetry database."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager


@contextmanager
def connection(path):
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()


def backup(source, destination):
    """Online backup includes committed WAL pages; never replace an existing backup."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Backup existiert bereits')
    if shutil.disk_usage(destination.parent).free < source.stat().st_size * 2 + 10_000_000:
        raise RuntimeError('Zu wenig freier Speicher für ein geprüftes Backup')
    temporary = destination.with_suffix('.partial')
    try:
        with connection(source) as src, connection(temporary) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup-Integritätsprüfung fehlgeschlagen')
            tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {}
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                count = src.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]
                if count != dst.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]:
                    raise RuntimeError('Backup-Zählprüfung fehlgeschlagen')
                counts[table] = count
        with temporary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        os.replace(temporary, destination)
        destination.with_suffix('.json').write_text(json.dumps({'sha256': digest, 'tables': counts}), encoding='utf-8')
        return {'file': destination.name, 'sha256': digest, 'tables': counts}
    finally:
        if temporary.exists():
            temporary.unlink()


class Catalog:
    def __init__(self, legacy_path):
        self.legacy = Path(legacy_path).resolve()
        self.root = self.legacy.parent
        self.path = self.root / 'generations.sqlite3'
        self.key_path = self.root / '.device-identity.key'
        self.root.mkdir(parents=True, exist_ok=True)
        with connection(self.path) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS generations(
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, file TEXT NOT NULL UNIQUE,
                    fingerprint TEXT, started_at INTEGER NOT NULL, ended_at INTEGER,
                    source TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY, old_id TEXT NOT NULL, new_id TEXT NOT NULL,
                    stamp INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pending(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), fingerprint TEXT,
                    observed_at INTEGER NOT NULL, info TEXT NOT NULL);
            ''')
            if not con.execute('SELECT 1 FROM generations').fetchone():
                stamp = int(time.time())
                if self.legacy.exists():
                    with connection(self.legacy) as old:
                        if old.execute("SELECT 1 FROM sqlite_master WHERE name='samples'").fetchone():
                            stamp = old.execute('SELECT MIN(ts) FROM samples').fetchone()[0] or stamp
                con.execute('INSERT INTO generations VALUES(?,?,?,?,?,NULL,?)',
                            ('legacy', 'Gamma 1 – bestehende Historie', self.legacy.name, None, stamp, 'legacy-unverified'))
                con.execute("INSERT INTO settings VALUES('active','legacy')")
            has_identity = con.execute('SELECT 1 FROM generations WHERE fingerprint IS NOT NULL').fetchone()
        if not self.key_path.exists():
            if has_identity:
                raise RuntimeError('Identitätsschlüssel fehlt; Sicherung wiederherstellen')
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as key:
                key.write(secrets.token_bytes(32))
                key.flush()
                os.fsync(key.fileno())
        self.key = self.key_path.read_bytes()
        if len(self.key) != 32:
            raise RuntimeError('Ungültiger Identitätsschlüssel')

    def fingerprint(self, raw):
        mac = str(raw.get('macAddr') or '').replace(':', '').replace('-', '').upper()
        if not re.fullmatch('[0-9A-F]{12}', mac) or mac in {'000000000000', 'FFFFFFFFFFFF'}:
            return None
        return hmac.new(self.key, mac.encode(), hashlib.sha256).hexdigest()

    def active(self):
        with connection(self.path) as con:
            return con.execute("SELECT value FROM settings WHERE key='active'").fetchone()[0]

    def get(self, generation=None):
        with connection(self.path) as con:
            row = con.execute('SELECT * FROM generations WHERE id=?', (generation or self.active(),)).fetchone()
        if not row:
            raise ValueError('Unbekannte Gerätegeneration')
        return dict(row)

    def database(self, generation=None):
        path = (self.root / self.get(generation)['file']).resolve()
        if path.parent != self.root:
            raise ValueError('Ungültiger Datenbankpfad')
        return str(path)

    def public(self):
        with connection(self.path) as con:
            rows = con.execute('SELECT id,label,started_at,ended_at,source FROM generations ORDER BY started_at,id').fetchall()
            pending = con.execute('SELECT observed_at,info FROM pending').fetchone()
        return {'active': self.active(), 'generations': [dict(r) for r in rows],
                'pending': {'observed_at': pending['observed_at'], **json.loads(pending['info'])} if pending else None,
                'identity_confirmed': self.get()['fingerprint'] is not None}

    def observe(self, raw, safe_info):
        fingerprint = self.fingerprint(raw)
        if fingerprint and fingerprint == self.get()['fingerprint']:
            with connection(self.path) as con:
                con.execute('DELETE FROM pending')
            return True
        # No unverified sample ever enters the old generation, including the first after upgrade.
        info = {k: safe_info.get(k) for k in ('version', 'boardVersion', 'ASICModel')}
        info['identity_available'] = fingerprint is not None
        with connection(self.path) as con:
            con.execute('INSERT OR REPLACE INTO pending VALUES(1,?,?,?)',
                        (fingerprint, int(time.time()), json.dumps(info)))
        return False

    def preview(self):
        result = self.public()
        with connection(self.path) as con:
            pending = con.execute('SELECT fingerprint FROM pending').fetchone()
            ticket = {'token': uuid.uuid4().hex, 'created_at': int(time.time()),
                      'fingerprint': pending['fingerprint'] if pending else self.get()['fingerprint']}
            con.execute("INSERT OR REPLACE INTO settings VALUES('preview',?)", (json.dumps(ticket),))
        result['token'] = ticket['token']
        with connection(self.database()) as con:
            result['counts'] = {name: con.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                                for name in ('samples', 'incidents', 'events')}
        return result

    def validate_preview(self, token, raw):
        with connection(self.path) as con:
            row = con.execute("SELECT value FROM settings WHERE key='preview'").fetchone()
        ticket = json.loads(row[0]) if row else {}
        if (not token or token != ticket.get('token') or int(time.time()) - ticket.get('created_at', 0) > 600
                or not ticket.get('fingerprint') or ticket['fingerprint'] != self.fingerprint(raw)):
            raise ValueError('Gerät oder Vorschau hat sich geändert; bitte erneut vorbereiten')

    def switch(self, expected, operation, label, raw, initialize, bind_existing=False):
        if not re.fullmatch('[A-Za-z0-9-]{8,80}', operation or ''):
            raise ValueError('Ungültige Vorgangs-ID')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(ord(c) < 32 for c in label):
            raise ValueError('Bitte ein gültiges Geräte-Label angeben')
        with connection(self.path) as con:
            prior = con.execute('SELECT new_id,completed FROM operations WHERE id=?', (operation,)).fetchone()
        if prior:
            self.finish_operations()
            return prior['new_id']
        if expected != self.active():
            raise ValueError('Die aktive Generation hat sich geändert; Vorschau neu laden')
        fingerprint = self.fingerprint(raw)
        if not fingerprint:
            raise ValueError('Keine gültige Geräteidentität erreichbar; Wechsel noch nicht möglich')
        old = self.get()
        if bind_existing:
            if old['fingerprint'] is not None:
                raise ValueError('Diese Generation ist bereits zugeordnet')
            with connection(self.path) as con:
                con.execute("UPDATE generations SET fingerprint=?,source='user-confirmed',label=? WHERE id=?",
                            (fingerprint, label.strip(), expected))
                con.execute('DELETE FROM pending')
            return expected
        stamp = int(time.time())
        backup_dir = self.root / 'backups' / operation
        backup(self.database(), backup_dir / 'telemetry.sqlite3')
        backup(self.path, backup_dir / 'generations.sqlite3')
        shutil.copyfile(self.key_path, backup_dir / '.device-identity.key')
        os.chmod(backup_dir / '.device-identity.key', 0o600)
        new_id = str(uuid.uuid4())
        file = 'generation-' + new_id + '.sqlite3'
        initialize(str(self.root / file))
        with connection(self.root / file) as con:
            con.execute("INSERT OR REPLACE INTO monitor_state VALUES('auto_restart_enabled','false')")
            con.execute("INSERT OR REPLACE INTO monitor_state VALUES('backfill_v11_1','1')")
        # Durable intent: startup finishes this idempotently before starting any poller.
        with connection(self.path) as con:
            con.execute('INSERT INTO generations VALUES(?,?,?,?,?,NULL,?)',
                        (new_id, label.strip(), file, fingerprint, stamp, 'user-confirmed'))
            con.execute('INSERT INTO operations VALUES(?,?,?,?,0)', (operation, expected, new_id, stamp))
        self.finish_operations()
        return new_id

    def finish_operations(self):
        with connection(self.path) as con:
            operations = con.execute('SELECT * FROM operations WHERE completed=0 ORDER BY stamp').fetchall()
        for operation in operations:
            with connection(self.database(operation['old_id'])) as old:
                old.execute("UPDATE incidents SET status='ARCHIVED',ended_at=?,updated_at=?,"
                            "recovery='Monitoring beendet: Gerätegeneration gewechselt' WHERE status='ACTIVE'",
                            (operation['stamp'], operation['stamp']))
                old.execute("INSERT OR REPLACE INTO monitor_state VALUES('generation_ended_at',?)", (str(operation['stamp']),))
            with connection(self.path) as con:
                con.execute('UPDATE generations SET ended_at=? WHERE id=?', (operation['stamp'], operation['old_id']))
                con.execute("UPDATE settings SET value=? WHERE key='active'", (operation['new_id'],))
                con.execute('UPDATE operations SET completed=1 WHERE id=?', (operation['id'],))
                con.execute('DELETE FROM pending')
