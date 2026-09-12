"""Regression evidence for independently reproduced review findings."""
import tracemalloc

import pytest

from test_platform import client, project, dataset, wait_job
from test_workflows import flow


def test_overlapping_chunk_limit_rejects_before_memory_amplification():
    from ard.knowledge import chunk_text
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match='5000'):
            chunk_text('x' * 30000, 'fixed', 1001, 1000)
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 15_000_000
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize('patch', [
    {'edges': [{'source': [], 'target': 'out'}]},
    {'edges': [{'source': 'in', 'target': {}}]},
    {'nodes': [{'id': 'in', 'type': []}, {'id': 'out', 'type': 'output'}]},
])
def test_malformed_graph_returns_client_error_without_writes(client, patch):
    p = project(client)
    body = {'name': 'invalid', 'nodes': [{'id': 'in', 'type': 'input'}, {'id': 'out', 'type': 'output'}],
            'edges': [{'source': 'in', 'target': 'out'}]}
    response = client.post(f'/api/projects/{p["id"]}/workflows', json={**body, **patch})
    assert response.status_code == 400, response.text
    assert client.get(f'/api/projects/{p["id"]}/workflows').json() == []


def test_transfer_failure_rolls_back_clone_and_audit_then_retry_succeeds(client, monkeypatch):
    source = project(client)
    target = client.post('/api/projects', json={'name': 'small target', 'quota_bytes': 1024}).json()
    asset = dataset(client, source, [{'text': 'x' * 800}])
    approval = client.post(f'/api/assets/{asset["id"]}/transfer', json={'target_project_id': target['id']}).json()
    store = client.app.state.service.store
    before = store.verify_audit()
    original = store.update

    def interrupted(entity_id, *args, **kwargs):
        if entity_id == approval['id']:
            raise RuntimeError('injected interruption before approval commit')
        return original(entity_id, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(store, 'update', interrupted)
        with pytest.raises(RuntimeError, match='injected interruption'):
            client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1})
    # Reopen SQLite to verify persisted state, rather than an in-memory cache.
    from ard.store import Store
    reopened = Store(store.root)
    assert reopened.list('dataset', target['id']) == []
    assert reopened.get(approval['id'])['status'] == 'PENDING'
    assert reopened.verify_audit() == before
    r = client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1})
    assert r.status_code == 200, r.text
    copies = reopened.list('dataset', target['id'])
    assert len(copies) == 1
    assert copies[0]['id'] == r.json()['approved_asset_id']
    assert copies[0]['sha256'] == asset['sha256']


def test_failed_workflow_decision_keeps_resumable_pending_state(client, monkeypatch):
    p = project(client)
    wf = flow(client, p['id'], True)
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={}).json()
    job = wait_job(client, j['id'])
    approval = client.get(f'/api/projects/{p["id"]}/approvals').json()[0]
    store = client.app.state.service.store
    original = store.update

    def interrupted(entity_id, *args, **kwargs):
        if entity_id == approval['id']:
            raise RuntimeError('injected decision interruption')
        return original(entity_id, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(store, 'update', interrupted)
        with pytest.raises(RuntimeError, match='injected decision'):
            client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1})
    assert store.get(job['id'])['status'] == 'WAITING_APPROVAL'
    assert store.get(job['id'])['state']['approved_nodes'] == []
    assert store.get(approval['id'])['status'] == 'PENDING'
    assert client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 200
    assert wait_job(client, job['id'])['status'] == 'SUCCEEDED'


def test_fast_approval_is_not_overwritten_when_worker_yields(client, monkeypatch):
    import threading
    from ard.jobs import Waiting
    p = project(client)
    wf = flow(client, p['id'], True)
    jobs = client.app.state.service.jobs
    original = jobs.runner
    waiting, release = threading.Event(), threading.Event()

    def held_runner(job_id):
        try:
            return original(job_id)
        except Waiting:
            waiting.set()
            assert release.wait(5)
            raise

    monkeypatch.setattr(jobs, 'runner', held_runner)
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={}).json()
    try:
        assert waiting.wait(5)
        approval = client.get(f'/api/projects/{p["id"]}/approvals').json()[0]
        assert client.post(f'/api/approvals/{approval["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 200
    finally:
        release.set()
    assert wait_job(client, j['id'])['status'] == 'SUCCEEDED'
