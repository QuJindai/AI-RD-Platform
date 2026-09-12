import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from ard.api import create_app
from ard.features import operations
from ard.features.operation_backup import restore_backup


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / 'runtime')
    operations.install(app)
    operations.install(app)
    with TestClient(app) as c:
        yield c


def project(c, name='项目运维验证'):
    result = c.post('/api/projects', json={'name': name, 'quota_bytes': 1048576})
    assert result.status_code == 201, result.text
    return result.json()


def settings_body(p, **changes):
    return {'name': p['name'], 'description': p.get('description', ''),
            'quota_bytes': p['quota_bytes'], 'max_jobs': p['max_jobs'],
            'expected_revision': p['revision'], **changes}


def host(value=80):
    return {'metrics': {'cpu_percent': value, 'memory_percent': 42, 'disk_percent': 50},
            'unavailable': {}, 'source': 'unit-test-host-collector', 'scope': 'test fixture', 'cpu_window_seconds': .1}


@pytest.fixture
def fast_host(monkeypatch):
    values = {'cpu': 80}
    monkeypatch.setattr(operations, 'collect_host_metrics', lambda root: host(values['cpu']))
    monkeypatch.setattr(operations, 'MIN_SAMPLE_INTERVAL_SECONDS', 0)
    return values


def sample(c, p, evaluate=True):
    result = c.post(f'/api/projects/{p["id"]}/telemetry/sample', json={'evaluate_alerts': evaluate})
    assert result.status_code == 201, result.text
    return result.json()


def rule(c, p, **changes):
    result = c.post(f'/api/projects/{p["id"]}/alert-rules', json={
        'name': 'CPU 高负载', 'metric': 'cpu_percent', 'operator': 'gte', 'threshold': 70, **changes})
    assert result.status_code == 201, result.text
    return result.json()


def test_install_once_and_settings_update(client):
    count = len(client.app.routes)
    operations.install(client.app)
    assert len(client.app.routes) == count
    assert 'operations' in client.app.state.features
    p = project(client)
    initial = client.get(f'/api/projects/{p["id"]}/settings').json()
    assert initial['usage'] == {'stored_bytes': 0, 'active_jobs': 0}
    result = client.patch(f'/api/projects/{p["id"]}/settings', json=settings_body(p, name='已更新项目',
                          description='持久化说明', telemetry_retention_count=10, telemetry_retention_hours=24))
    assert result.status_code == 200, result.text
    assert result.json()['project']['revision'] == 2
    assert result.json()['project']['description'] == '持久化说明'
    assert result.json()['telemetry']['retention_count'] == 10
    assert client.patch(f'/api/projects/{p["id"]}/settings', json=settings_body(p)).status_code == 409
    assert client.get(f'/api/projects/{p["id"]}/settings').json()['project']['name'] == '已更新项目'
    assert client.get('/api/audit/verify').json()['valid']


def test_settings_atomic_revision_and_quota_floors(client):
    p = project(client)
    path = f'/api/projects/{p["id"]}/settings'
    asset = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': '占用空间', 'rows': [{'text': 'x' * 2000}]}).json()
    blocked = client.patch(path, json=settings_body(p, name='不应保存', quota_bytes=1024))
    assert blocked.status_code == 409 and '已使用' in blocked.text
    assert client.get(path).json()['project']['name'] == p['name']
    assert client.get(path).json()['usage']['stored_bytes'] == asset['size_bytes']
    store = client.app.state.service.store
    for status in ('WAITING_APPROVAL', 'PAUSED'):
        store.create('job', p['id'], {'status': status}, 'test')
    blocked = client.patch(path, json=settings_body(p, name='不应保存', max_jobs=1))
    assert blocked.status_code == 409 and '活动任务' in blocked.text
    assert client.get(path).json()['project']['revision'] == p['revision']
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda name: client.patch(path, json=settings_body(p, name=name)), ['并发一', '并发二']))
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_operations_permissions_and_project_scope(tmp_path, fast_host):
    identities = {
        'admin-token-000000': {'user': 'admin', 'role': 'admin', 'projects': ['*']},
        'developer-token-000': {'user': 'dev', 'role': 'developer', 'projects': ['*']},
        'reviewer-token-0000': {'user': 'review', 'role': 'reviewer', 'projects': ['*']},
        'auditor-token-00000': {'user': 'audit', 'role': 'auditor', 'projects': ['*']},
        'scoped-admin-000000': {'user': 'scope', 'role': 'admin', 'projects': []},
    }
    app = create_app(tmp_path, identities)
    operations.install(app)
    with TestClient(app) as c:
        c.headers['Authorization'] = 'Bearer admin-token-000000'
        p = project(c)
        r = rule(c, p)
        alert = sample(c, p)['evaluation'][0]['alert_id']
        for token in ('developer-token-000', 'reviewer-token-0000', 'auditor-token-00000'):
            c.headers['Authorization'] = 'Bearer ' + token
            for endpoint in ('settings', 'audit', 'audit/export', 'telemetry', 'telemetry/export', 'alert-rules', 'alerts'):
                assert c.get(f'/api/projects/{p["id"]}/{endpoint}').status_code == 200
            assert c.patch(f'/api/projects/{p["id"]}/settings', json=settings_body(p)).status_code == 403
            assert c.post(f'/api/projects/{p["id"]}/alert-rules', json={'name': 'x', 'metric': 'cpu_percent', 'threshold': 50}).status_code == 403
            assert c.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'}).status_code == 403
            if not token.startswith('developer'):
                assert c.post(f'/api/projects/{p["id"]}/telemetry/sample', json={}).status_code == 403
                assert c.post(f'/api/projects/{p["id"]}/alerts/{alert}/actions', json={'action': 'resolve', 'expected_revision': 1}).status_code == 403
        c.headers['Authorization'] = 'Bearer scoped-admin-000000'
        for endpoint in ('settings', 'audit', 'audit/export', 'telemetry', 'telemetry/export', 'alert-rules', 'alerts'):
            assert c.get(f'/api/projects/{p["id"]}/{endpoint}').status_code == 403
        identities['scoped-admin-000000']['projects'] = [p['id']]
        assert c.get(f'/api/projects/{p["id"]}/settings').status_code == 200
        assert c.patch(f'/api/projects/{p["id"]}/settings', json=settings_body(p)).status_code == 200
        assert c.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'}).status_code == 403


def test_audit_filter_time_pagination_and_formula_escape(client):
    p, other = project(client), project(client, '其他项目')
    store = client.app.state.service.store
    actor = '  =HYPERLINK("https://example.invalid")'
    note = store.create('note', p['id'], {'value': 1}, actor)
    store.create('note', other['id'], {'value': 2}, actor)
    records = client.get(f'/api/projects/{p["id"]}/audit', params={'actor': actor, 'action': 'create:note'}).json()
    assert records['total'] == 1 and records['items'][0]['entity_id'] == note['id']
    at = records['items'][0]['at']
    exact = client.get(f'/api/projects/{p["id"]}/audit', params={'since': at, 'until': at, 'actor': actor}).json()
    assert exact['total'] == 1
    csv_result = client.get(f'/api/projects/{p["id"]}/audit/export', params={'actor': actor, 'action': 'create:note'})
    parsed = list(csv.DictReader(io.StringIO(csv_result.content.decode('utf-8-sig'))))
    assert parsed[0]['actor'] == "'" + actor
    assert csv_result.headers['x-total-count'] == '1'
    assert client.get(f'/api/projects/{p["id"]}/audit', params={'since': '2026-01-01'}).status_code == 422
    assert client.get(f'/api/projects/{p["id"]}/audit', params={'since': '2026-02-01T00:00:00Z', 'until': '2026-01-01T00:00:00Z'}).status_code == 422
    paged = client.get(f'/api/projects/{p["id"]}/audit', params={'limit': 1, 'offset': 1}).json()
    assert len(paged['items']) == 1 and paged['items'][0]['action'] == 'create:project'
    for text in ('=1+1', '+1', '-1', '@SUM(1)', '\tplain', '\nplain', ' \r=2'):
        escaped = list(csv.DictReader(io.StringIO(operations._csv(('v',), [{'v': text}]).decode('utf-8-sig'))))[0]['v']
        assert escaped == "'" + text


def test_real_host_metrics_and_sampling_interval(client):
    p = project(client)
    result = sample(client, p)
    metrics = result['sample']['metrics']
    assert result['policy']['sampling'] == 'manual'
    assert result['sample']['source'].startswith('host:')
    assert metrics['disk_total_bytes'] > 0 and 0 <= metrics['disk_percent'] <= 100
    if Path('/proc/meminfo').exists():
        assert metrics['memory_total_bytes'] > 0 and 0 <= metrics['memory_percent'] <= 100
        assert 0 <= metrics['cpu_percent'] <= 100
    assert client.post(f'/api/projects/{p["id"]}/telemetry/sample', json={}).status_code == 429


def test_telemetry_retention_export_and_restart(client, fast_host):
    p = project(client)
    client.patch(f'/api/projects/{p["id"]}/settings', json=settings_body(p, telemetry_retention_count=10, telemetry_retention_hours=1))
    ids = [sample(client, p, evaluate=False)['sample']['id'] for _ in range(13)]
    history = client.get(f'/api/projects/{p["id"]}/telemetry').json()
    assert history['total'] == 10
    assert {row['id'] for row in history['items']} == set(ids[-10:])
    store = client.app.state.service.store
    assert len(store.list('telemetry', p['id'])) == 10
    with store.transaction() as db:
        db.execute('UPDATE records SET created_at=? WHERE id=?',
                   ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), ids[-1]))
    assert client.get(f'/api/projects/{p["id"]}/telemetry').json()['total'] == 9
    sample(client, p, evaluate=False)
    assert len(store.list('telemetry', p['id'])) == 10
    assert ids[-1] not in {row['id'] for row in store.list('telemetry', p['id'])}
    exported = client.get(f'/api/projects/{p["id"]}/telemetry/export')
    assert len(list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))) == 10
    # A second Service/Store instance reads durable history, without resampling.
    from ard.service import Service
    service = Service(store.root)
    try:
        assert len(service.store.list('telemetry', p['id'])) == 10
        assert service.store.verify_audit()['valid']
    finally:
        service.close()


def test_alert_acknowledge_resolve_rearm_and_recovery(client, fast_host):
    p = project(client)
    r = rule(client, p)
    first = sample(client, p)
    aid = first['evaluation'][0]['alert_id']
    path = f'/api/projects/{p["id"]}/alerts/{aid}/actions'
    ack = client.post(path, json={'action': 'acknowledge', 'expected_revision': 1, 'comment': '正在处理'})
    assert ack.status_code == 200 and ack.json()['status'] == 'ACKNOWLEDGED'
    assert client.post(path, json={'action': 'resolve', 'expected_revision': 1}).status_code == 409
    resolved = client.post(path, json={'action': 'resolve', 'expected_revision': 2, 'comment': '人工完成'})
    assert resolved.status_code == 200 and resolved.json()['status'] == 'RESOLVED'
    assert [event['action'] for event in resolved.json()['events']] == ['open', 'acknowledge', 'resolve']
    assert client.post(path, json={'action': 'resolve', 'expected_revision': 3}).status_code == 409
    repeat = client.post(f'/api/projects/{p["id"]}/alerts/evaluate', json={'sample_id': first['sample']['id']})
    assert repeat.json()['evaluation'][0]['outcome'] == 'already_evaluated_or_older'
    assert sample(client, p)['evaluation'][0]['outcome'] == 'resolved_waiting_for_recovery'
    fast_host['cpu'] = 20
    assert sample(client, p)['evaluation'][0]['outcome'] == 'normal'
    fast_host['cpu'] = 85
    second = sample(client, p)
    assert second['evaluation'][0]['outcome'] == 'opened'
    assert second['evaluation'][0]['alert_id'] != aid
    fast_host['cpu'] = 10
    assert sample(client, p)['evaluation'][0]['outcome'] == 'recovered'
    alerts = client.get(f'/api/projects/{p["id"]}/alerts').json()['items']
    assert len(alerts) == 2 and all(a['status'] == 'RESOLVED' for a in alerts)
    assert alerts[0]['events'][-1]['action'] == 'recovered'
    assert all(event['actor'] == 'local' for a in alerts for event in a['events'])
    assert len(client.app.state.service.store.list('alert_rule_state', p['id'])) == 1


def test_alert_rule_update_revision_limits_and_missing_metric(client, fast_host, monkeypatch):
    p = project(client)
    r = rule(client, p)
    sample(client, p)
    body = {'name': 'CPU 停用', 'metric': 'cpu_percent', 'threshold': 90, 'enabled': False, 'expected_revision': r['revision']}
    changed = client.patch(f'/api/projects/{p["id"]}/alert-rules/{r["id"]}', json=body)
    assert changed.status_code == 200 and changed.json()['revision'] == 2
    assert client.get(f'/api/projects/{p["id"]}/alerts').json()['items'][0]['events'][-1]['action'] == 'rule_changed'
    assert client.patch(f'/api/projects/{p["id"]}/alert-rules/{r["id"]}', json=body).status_code == 409
    assert sample(client, p)['evaluation'] == []
    assert client.post(f'/api/projects/{p["id"]}/alert-rules', json={'name': 'bad', 'metric': 'cpu_percent', 'threshold': 101}).status_code == 422
    monkeypatch.setattr(operations, 'MAX_RULES', 2)
    rule(client, p, name='另一规则')
    assert client.post(f'/api/projects/{p["id"]}/alert-rules', json={'name': '第三条', 'metric': 'cpu_percent', 'threshold': 10}).status_code == 409
    fast_host['cpu'] = None
    unavailable = sample(client, p)
    assert unavailable['evaluation'][0]['outcome'] == 'metric_unavailable'


def test_cross_project_references_are_rejected(client, fast_host):
    p, other = project(client), project(client, '第二项目')
    r = rule(client, other)
    result = sample(client, other)
    assert client.post(f'/api/projects/{p["id"]}/alerts/evaluate', json={'sample_id': result['sample']['id']}).status_code == 400
    body = {'name': '错误规则', 'metric': 'cpu_percent', 'threshold': 50, 'expected_revision': r['revision']}
    assert client.patch(f'/api/projects/{p["id"]}/alert-rules/{r["id"]}', json=body).status_code == 400
    aid = result['evaluation'][0]['alert_id']
    assert client.post(f'/api/projects/{p["id"]}/alerts/{aid}/actions', json={'action': 'resolve', 'expected_revision': 1}).status_code == 400
    assert client.get(f'/api/projects/{other["id"]}/alerts').json()['items'][0]['status'] == 'OPEN'


@pytest.fixture
def backup_archive(client, tmp_path):
    p = project(client)
    rows = [{'x': i, 'label': int(i >= 20)} for i in range(40)]
    asset = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': '恢复数据', 'rows': rows}).json()
    approval = client.post(f'/api/assets/{asset["id"]}/transfer', json={}).json()
    assert client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 200
    job = client.post(f'/api/assets/{asset["id"]}/train', json={'target': 'label', 'features': ['x']}).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get('/api/jobs/' + job['id']).json()
        if job['status'] in ('SUCCEEDED', 'FAILED'):
            break
        time.sleep(.01)
    assert job['status'] == 'SUCCEEDED', job
    model = client.get('/api/models/' + job['result']['model_id']).json()
    document = client.post(f'/api/projects/{p["id"]}/documents', json={'name': '恢复文档', 'text': 'This document preserves original evidence. 第二段支持恢复验证。'}).json()
    store = client.app.state.service.store
    private = store.root / 'private' / 'integrations'
    private.mkdir(parents=True)
    (private / 'connector.json').write_text('{"token":"secret-do-not-export"}')
    (store.root / 'identities.json').write_text('{"password":"secret-do-not-export"}')
    orphan = store.blob(b'unreferenced-object-not-exported')
    response = client.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'})
    assert response.status_code == 200, response.text
    path = tmp_path / 'backup.zip'
    path.write_bytes(response.content)
    assert hashlib.sha256(response.content).hexdigest() == response.headers['x-backup-sha256']
    return {'path': path, 'project': p, 'dataset': asset, 'rows': rows, 'model': model,
            'document': document, 'store': store, 'orphan': orphan}


def test_backup_real_restore_script_rows_model_documents_and_audit(backup_archive, tmp_path):
    b = backup_archive
    destination = tmp_path / 'restored'
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'restore_backup.py'
    process = subprocess.run([sys.executable, str(script), str(b['path']), str(destination)], text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result['audit']['valid'] and result['objects'] >= 3
    with zipfile.ZipFile(b['path']) as archive:
        assert 'objects/' + b['orphan'] not in archive.namelist()
        assert all(name in ('manifest.json', 'platform.sqlite3') or name.startswith('objects/') for name in archive.namelist())
        assert b'secret-do-not-export' not in b['path'].read_bytes()
    app = create_app(destination)
    operations.install(app)
    with TestClient(app) as restored:
        assert restored.get(f'/api/assets/{b["dataset"]["id"]}/rows').json()['rows'] == b['rows']
        assert restored.get(f'/api/models/{b["model"]["id"]}/artifact').json() == json.loads(b['store'].read_blob(b['model']['sha256']))
        dep = restored.post(f'/api/models/{b["model"]["id"]}/deploy', json={'expires_minutes': 5}).json()
        assert restored.post(f'/api/deployments/{dep["id"]}/predict', json={'rows': [{'x': 0}, {'x': 39}]}).json()['predictions'] == [0, 1]
        doc = restored.get(f'/api/projects/{b["project"]["id"]}/documents').json()[0]
        assert doc['id'] == b['document']['id']
        assert restored.app.state.service.store.read_blob(doc['sha256']) == b['store'].read_blob(b['document']['sha256'])
        search = restored.post(f'/api/projects/{b["project"]["id"]}/search', json={'query': 'document preserves', 'mode': 'keyword'}).json()
        assert search['matches']
        assert restored.get('/api/audit/verify').json()['valid']
    assert not (destination / 'private').exists()


def test_backup_rejects_active_jobs_and_corrupt_content(client):
    p = project(client)
    assert client.post('/api/operations/backup', json={}).status_code == 422
    store = client.app.state.service.store
    for state in ('RUNNING', 'QUEUED', 'WAITING_APPROVAL', 'PAUSED'):
        job = store.create('job', p['id'], {'status': state}, 'test')
        assert client.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'}).status_code == 409
        store.update(job['id'], {'status': 'CANCELLED'}, 'test')
    data = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': 'bad blob', 'rows': [{'x': 1}]}).json()
    (store.objects / data['sha256']).write_bytes(b'corrupted')
    response = client.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'})
    assert response.status_code == 400 and 'integrity' in response.text


def _rewrite_archive(source, destination, change):
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    change(entries)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)


@pytest.mark.parametrize('attack', ['traversal', 'absolute', 'backslash', 'symlink', 'duplicate', 'bomb', 'hash', 'extra', 'missing_object'])
def test_restore_rejects_archive_attacks(backup_archive, tmp_path, attack):
    source = backup_archive['path']
    corrupt = tmp_path / ('bad-' + attack + '.zip')
    def modify(entries):
        if attack == 'hash':
            key = next(k for k in entries if k.startswith('objects/'))
            entries[key] = b'X' + entries[key][1:]
        elif attack == 'missing_object':
            key = 'objects/' + backup_archive['dataset']['sha256']
            del entries[key]
            manifest = json.loads(entries['manifest.json'])
            manifest['files'] = [e for e in manifest['files'] if e['path'] != key]
            manifest['object_count'] -= 1
            entries['manifest.json'] = json.dumps(manifest).encode()
        elif attack in ('traversal', 'absolute', 'backslash', 'extra'):
            key = {'traversal': '../escaped', 'absolute': '/escaped', 'backslash': 'objects\\escaped', 'extra': 'private/secret.json'}[attack]
            entries[key] = b'bad'
    _rewrite_archive(source, corrupt, modify)
    if attack in ('symlink', 'duplicate', 'bomb'):
        with zipfile.ZipFile(corrupt, 'a') as archive:
            if attack == 'symlink':
                entry = zipfile.ZipInfo('objects/' + 'a' * 64)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, b'/outside')
            elif attack == 'duplicate':
                with pytest.warns(UserWarning, match='Duplicate'):
                    archive.writestr('manifest.json', b'{}')
            else:
                archive.writestr('objects/' + 'b' * 64, b'0' * 1000000, compress_type=zipfile.ZIP_DEFLATED)
    destination = tmp_path / ('target-' + attack)
    with pytest.raises(ValueError):
        restore_backup(corrupt, destination)
    assert not destination.exists()
    assert not (tmp_path / 'escaped').exists()
    assert not list(tmp_path.glob('.ard-restore-*'))


def test_restore_validates_audit_even_when_manifest_hash_is_recomputed(backup_archive, tmp_path):
    corrupt = tmp_path / 'tampered-audit.zip'
    def modify(entries):
        database = tmp_path / 'tampered.sqlite3'
        database.write_bytes(entries['platform.sqlite3'])
        with sqlite3.connect(database) as db:
            db.execute("UPDATE audit SET actor='tampered' WHERE seq=1")
        entries['platform.sqlite3'] = database.read_bytes()
        manifest = json.loads(entries['manifest.json'])
        for entry in manifest['files']:
            if entry['path'] == 'platform.sqlite3':
                entry['size_bytes'] = len(entries['platform.sqlite3'])
                entry['sha256'] = hashlib.sha256(entries['platform.sqlite3']).hexdigest()
        entries['manifest.json'] = json.dumps(manifest).encode()
    _rewrite_archive(backup_archive['path'], corrupt, modify)
    with pytest.raises(ValueError, match='audit hash chain'):
        restore_backup(corrupt, tmp_path / 'tampered-restored')


def test_restore_never_overwrites_existing_data_or_symlinks(backup_archive, tmp_path):
    existing = tmp_path / 'live'
    existing.mkdir()
    (existing / 'important.txt').write_text('preserve')
    with pytest.raises(ValueError, match='NEW empty directory'):
        restore_backup(backup_archive['path'], existing)
    assert (existing / 'important.txt').read_text() == 'preserve'
    symlink = tmp_path / 'redirect'
    symlink.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        restore_backup(backup_archive['path'], symlink)
    empty = tmp_path / 'empty-target'
    empty.mkdir()
    assert restore_backup(backup_archive['path'], empty)['audit']['valid']
    assert (empty / 'platform.sqlite3').is_file()


def test_backup_missing_reference_and_symlink_content_rejected(client, tmp_path):
    p = project(client)
    asset = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': 'referenced', 'rows': [{'x': 1}]}).json()
    path = client.app.state.service.store.objects / asset['sha256']
    original = path.read_bytes()
    path.unlink()
    assert client.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'}).status_code == 400
    outside = tmp_path / 'outside-object'
    outside.write_bytes(original)
    path.symlink_to(outside)
    assert client.post('/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'}).status_code == 400
