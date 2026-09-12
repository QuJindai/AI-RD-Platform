"""Transactional metadata and immutable content-addressed objects."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / 'objects'
        self.objects.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self._transactions = threading.local()
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, data TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS records_project ON records(project_id, kind);
              CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT NOT NULL,
                project_id TEXT, detail TEXT NOT NULL, previous_hash TEXT NOT NULL,
                hash TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        with self.lock:
            active = getattr(self._transactions, 'db', None)
            if active is not None:
                yield active
                return
            db = sqlite3.connect(self.root / 'platform.sqlite3', timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    @contextmanager
    def transaction(self):
        """Commit related records and audits together; nested operations share SQLite."""
        active = getattr(self._transactions, 'db', None)
        if active is not None:
            savepoint = 'nested_' + uuid.uuid4().hex
            active.execute('SAVEPOINT ' + savepoint)
            try:
                yield active
            except BaseException:
                active.execute('ROLLBACK TO ' + savepoint)
                raise
            finally:
                active.execute('RELEASE ' + savepoint)
            return
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._transactions.db = db
            try:
                yield db
            finally:
                del self._transactions.db

    @staticmethod
    def _record(row):
        if row is None:
            raise KeyError('record not found')
        r = dict(row)
        data = json.loads(r.pop('data'))
        return {**data, **r}

    def get(self, entity_id, kind=None):
        with self.connect() as db:
            record = self._record(db.execute('SELECT * FROM records WHERE id=?', (entity_id,)).fetchone())
        if kind and record['kind'] != kind:
            raise KeyError('record not found')
        return record

    def list(self, kind=None, project_id=None):
        where, args = [], []
        if kind:
            where.append('kind=?'); args.append(kind)
        if project_id:
            where.append('project_id=?'); args.append(project_id)
        sql = 'SELECT * FROM records' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY created_at DESC, id'
        with self.connect() as db:
            return [self._record(row) for row in db.execute(sql, args)]

    def _audit(self, db, actor, action, entity_id, project_id, detail):
        prev = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        prev = prev[0] if prev else '0' * 64
        payload = dict(at=now(), actor=actor, action=action, entity_id=entity_id,
                       project_id=project_id, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True), previous_hash=prev)
        digest = hashlib.sha256(encode(payload)).hexdigest()
        db.execute('INSERT INTO audit(at,actor,action,entity_id,project_id,detail,previous_hash,hash) VALUES (?,?,?,?,?,?,?,?)',
                   (*payload.values(), digest))

    def create(self, kind, project_id, data, actor, check=None):
        entity_id, at = uuid.uuid4().hex, now()
        payload = encode(data).decode()
        with self.transaction() as db:
            if check:
                check(db)
            db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?)', (entity_id, kind, project_id, 1, at, at, payload))
            self._audit(db, actor, 'create:' + kind, entity_id, project_id, {'data_sha256': hashlib.sha256(payload.encode()).hexdigest()})
        return self.get(entity_id)

    def update(self, entity_id, changes, actor, expected_revision=None):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM records WHERE id=?', (entity_id,)).fetchone()
            old = self._record(row)
            if expected_revision is not None and old['revision'] != expected_revision:
                raise ValueError('revision conflict; reload and retry')
            if old['kind'] in ('dataset', 'model', 'document', 'workflow', 'skill', 'integration_package',
                               'model_baseline', 'model_evaluation', 'model_comparison', 'source_snapshot'):
                raise ValueError('immutable version; create a new version')
            data = json.loads(row['data'])
            if any(k in changes for k in ('id', 'kind', 'project_id', 'revision', 'created_at', 'updated_at')):
                raise ValueError('reserved metadata field')
            data.update(changes)
            db.execute('UPDATE records SET revision=?,updated_at=?,data=? WHERE id=?',
                       (old['revision'] + 1, now(), encode(data).decode(), entity_id))
            self._audit(db, actor, 'update:' + old['kind'], entity_id, old['project_id'],
                        {'revision': old['revision'] + 1, 'fields': sorted(changes)})
        return self.get(entity_id)

    def blob(self, content):
        digest = hashlib.sha256(content).hexdigest()
        target = self.objects / digest
        with self.lock:
            if not target.exists():
                temporary = self.objects / ('.' + uuid.uuid4().hex)
                temporary.write_bytes(content)
                temporary.replace(target)
        return digest

    def read_blob(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('invalid content digest')
        raw = (self.objects / digest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('content integrity check failed')
        return raw

    def audits(self, project_ids=None, limit=200):
        with self.connect() as db:
            rows = [dict(x) for x in db.execute('SELECT * FROM audit ORDER BY seq DESC')]
        if project_ids is not None:
            rows = [r for r in rows if r['project_id'] in project_ids]
        for row in rows:
            row['detail'] = json.loads(row['detail'])
        return rows[:limit]

    def verify_audit(self):
        prev, count = '0' * 64, 0
        with self.connect() as db:
            for row in db.execute('SELECT * FROM audit ORDER BY seq'):
                record = dict(row)
                seq, digest = record.pop('seq'), record.pop('hash')
                if record['previous_hash'] != prev or hashlib.sha256(encode(record)).hexdigest() != digest:
                    return {'valid': False, 'checked': count, 'first_invalid_seq': seq}
                count += 1; prev = digest
        return {'valid': True, 'checked': count, 'head_hash': prev,
                'scope': 'local hash chain; external anchoring is not configured'}
