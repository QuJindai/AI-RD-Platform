import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from ard.api import create_app
    with TestClient(create_app(tmp_path)) as c:
        yield c


def project(c, name="工程验证"):
    r = c.post('/api/projects', json={'name': name})
    assert r.status_code == 201, r.text
    return r.json()


def dataset(c, p, rows=None):
    rows = rows or [{'x': i, 'y': i * 3 + 1, 'label': int(i >= 30)} for i in range(60)]
    r = c.post(f'/api/projects/{p["id"]}/datasets', json={'name': '合成检测记录', 'rows': rows})
    assert r.status_code == 201, r.text
    return r.json()


def wait_job(c, job_id, terminal=('SUCCEEDED', 'FAILED', 'CANCELLED', 'WAITING_APPROVAL')):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = c.get('/api/jobs/' + job_id).json()
        if job['status'] in terminal:
            return job
        time.sleep(.03)
    pytest.fail('job did not reach a terminal state')


def approve(c, asset):
    a = c.post(f'/api/assets/{asset["id"]}/transfer', json={}).json()
    r = c.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': a['revision']})
    assert r.status_code == 200, r.text


def test_health_empty_state_and_project(client):
    assert client.get('/health').json()['version'] == '0.1.0'
    p = project(client)
    assert client.get('/api/projects').json()[0]['id'] == p['id']
    assert client.get('/api/overview').json()['counts']['datasets'] == 0


def test_dataset_versions_export_and_audit(client):
    p = project(client)
    d = dataset(client, p, [{'x': ' A '}, {'x': ' A '}])
    r = client.post(f'/api/assets/{d["id"]}/transform', json={'operations': [{'type': 'strip'}, {'type': 'drop_duplicates'}]})
    assert r.status_code == 201, r.text
    child = r.json()
    assert child['parents'] == [d['id']]
    assert child['row_count'] == 1
    assert client.get(f'/api/assets/{d["id"]}/rows').json()['rows'] == [{'x': ' A '}, {'x': ' A '}]
    out = client.get(f'/api/assets/{child["id"]}/export')
    assert out.json() == [{'x': 'A'}]
    assert hashlib.sha256(out.content).hexdigest() == child['sha256']
    assert client.get('/api/audit/verify').json()['valid'] is True


def test_split_merge_disjoint_and_project_boundary(client):
    p, p2 = project(client), project(client, '另一项目')
    d, d2 = dataset(client, p), dataset(client, p2)
    split = client.post(f'/api/assets/{d["id"]}/split', json={'ratio': .7, 'seed': 3}).json()
    assert sum(x['row_count'] for x in split) == 60
    ids = [x['id'] for x in split]
    r = client.post(f'/api/projects/{p["id"]}/merge', json={'asset_ids': ids, 'name': '重组'})
    assert r.status_code == 201 and r.json()['row_count'] == 60
    r = client.post(f'/api/projects/{p["id"]}/merge', json={'asset_ids': [d['id'], d2['id']], 'name': '错误合并'})
    assert r.status_code == 400


def test_real_training_deployment_prediction(client):
    p = project(client)
    d = dataset(client, p)
    request = {'target': 'label', 'features': ['x'], 'task': 'classification'}
    assert client.post(f'/api/assets/{d["id"]}/train', json=request).status_code == 409
    approve(client, d)
    r = client.post(f'/api/assets/{d["id"]}/train', json=request)
    assert r.status_code == 202, r.text
    j = wait_job(client, r.json()['id'])
    assert j['status'] == 'SUCCEEDED', j
    model = client.get('/api/models/' + j['result']['model_id']).json()
    assert model['metrics']['accuracy'] >= .8
    dep = client.post(f'/api/models/{model["id"]}/deploy', json={'expires_minutes': 60}).json()
    r = client.post(f'/api/deployments/{dep["id"]}/predict', json={'rows': [{'x': 1}, {'x': 59}]})
    assert r.status_code == 200, r.text
    assert r.json()['predictions'] == [0, 1]
    artifact = client.get(f'/api/models/{model["id"]}/artifact').json()
    json.dumps(artifact, allow_nan=False)


def test_approval_stale_revision_rejected(client):
    d = dataset(client, project(client))
    a = client.post(f'/api/assets/{d["id"]}/transfer', json={}).json()
    body = {'decision': 'approve', 'expected_revision': a['revision']}
    assert client.post(f'/api/approvals/{a["id"]}/decide', json=body).status_code == 200
    assert client.post(f'/api/approvals/{a["id"]}/decide', json=body).status_code == 409


def test_bad_payloads_do_not_create_assets(client):
    p = project(client)
    assert client.post(f'/api/projects/{p["id"]}/datasets', json={'name': 'empty', 'rows': []}).status_code == 400
    assert client.get('/api/assets/../../etc/passwd').status_code in (400, 404)
    assert client.post(f'/api/projects/{p["id"]}/import', files={'file': ('bad.csv', b'\xff\x00', 'text/csv')}).status_code == 400


def test_restart_keeps_assets(tmp_path):
    from ard.api import create_app
    with TestClient(create_app(tmp_path)) as c:
        d = dataset(c, project(c))
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/assets/' + d['id']).json()['sha256'] == d['sha256']
        assert c.get('/api/audit/verify').json()['valid']


def test_identity_project_scope_and_reviewer(tmp_path):
    from ard.api import create_app
    ids = {'developer-token-1234': {'user': 'dev', 'role': 'developer', 'projects': ['*']},
           'reviewer-token-12345': {'user': 'review', 'role': 'reviewer', 'projects': ['*']},
           'outsider-token-12345': {'user': 'outsider', 'role': 'developer', 'projects': []}}
    with TestClient(create_app(tmp_path, identities=ids)) as c:
        assert c.get('/api/projects').status_code == 401
        c.headers['Authorization'] = 'Bearer developer-token-1234'
        d = dataset(c, project(c))
        a = c.post(f'/api/assets/{d["id"]}/transfer', json={}).json()
        assert c.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 403
        c.headers['Authorization'] = 'Bearer outsider-token-12345'
        assert c.get('/api/assets/' + d['id']).status_code == 403
        assert c.get('/api/projects').json() == []
        c.headers['Authorization'] = 'Bearer reviewer-token-12345'
        assert c.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 200


def test_store_conflict_and_audit_tamper(tmp_path):
    from ard.store import Store
    store = Store(tmp_path)
    p = store.create('project', None, {'name': 'x'}, 'a')
    store.update(p['id'], {'name': 'y'}, 'a', expected_revision=1)
    with pytest.raises(ValueError, match='revision'):
        store.update(p['id'], {'name': 'z'}, 'a', expected_revision=1)
    assert store.verify_audit()['valid']
    with store.connect() as db:
        db.execute("UPDATE audit SET actor='tampered' WHERE seq=1")
    assert not store.verify_audit()['valid']


def test_storage_quota_and_immutable_data(client):
    p = client.post('/api/projects', json={'name': '限额', 'quota_bytes': 1024}).json()
    r = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': '大文件', 'rows': [{'text': 'x' * 2000}]})
    assert r.status_code == 400
    assert client.get(f'/api/projects/{p["id"]}/datasets').json() == []
    d = dataset(client, p, [{'x': 1}])
    with pytest.raises(ValueError, match='immutable'):
        client.app.state.service.store.update(d['id'], {'name': 'mutate'}, 'local', 1)


def test_local_mode_rejects_other_hosts_and_origins(client):
    assert client.get('/api/projects', headers={'Host': 'evil.example'}).status_code == 400
    assert client.post('/api/projects', json={'name': 'forged'}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_interrupted_jobs_reconciled_on_start(tmp_path):
    from ard.store import Store
    from ard.api import create_app
    store = Store(tmp_path)
    p = store.create('project', None, {'name': 'recovery', 'quota_bytes': 4096, 'max_jobs': 1}, 'local')
    j = store.create('job', p['id'], {'status': 'RUNNING', 'logs': [], 'creator': 'local'}, 'local')
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/jobs/' + j['id']).json()['status'] == 'INTERRUPTED'


def test_expired_deployment_fails_closed(client):
    from ard.store import now
    p = project(client)
    dep = client.app.state.service.store.create('deployment', p['id'], {'name': 'expired', 'model_id': 'none',
        'status': 'ACTIVE', 'expires_at': '2000-01-01T00:00:00+00:00'}, 'local')
    assert client.post(f'/api/deployments/{dep["id"]}/predict', json={'rows': [{'x': 1}]}).status_code == 410
