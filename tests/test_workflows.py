import json
import pytest

from test_platform import client, project, wait_job, dataset


def test_chunk_offsets_and_strategies():
    from ard.knowledge import chunk_text
    text = '# 标题\n' + '制造检测数据需要完整性核验。' * 20 + '\n\n# 第二节\n北斗定位需要稳定的信号。'
    for strategy in ('fixed', 'paragraph', 'heading', 'sentence', 'delimiter'):
        chunks = chunk_text(text, strategy, 100, 15, '\n\n')
        assert chunks
        assert all(text[c['start']:c['end']] == c['text'] and len(c['text']) <= 100 for c in chunks)
    with pytest.raises(ValueError):
        chunk_text('x', 'fixed', 50, 50)


def test_knowledge_search_sources_and_isolation(client):
    p, other = project(client), project(client, '其他')
    d = client.post(f'/api/projects/{p["id"]}/documents', json={'name': '北斗工艺说明', 'text': '北斗定位的产线检测需要校验天线连接和卫星信号质量。'}).json()
    r = client.post(f'/api/projects/{p["id"]}/search', json={'query': '北斗定位天线', 'top_k': 3})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result['matches'][0]['document_id'] == d['id']
    assert '天线连接' in result['matches'][0]['text']
    assert result['latency_ms'] >= 0
    assert client.post(f'/api/projects/{other["id"]}/search', json={'query': '北斗定位'}).json()['matches'] == []
    assert client.post(f'/api/projects/{p["id"]}/search', json={'query': 'zzzzzz'}).json()['matches'] == []


def flow(client, pid, human=False):
    nodes = [{'id': 'in', 'type': 'input', 'params': {}},
             {'id': 'search', 'type': 'retrieve', 'params': {'query': '北斗定位'}}]
    if human:
        nodes.append({'id': 'review', 'type': 'human', 'params': {'message': '请复核检索证据'}})
    nodes.append({'id': 'out', 'type': 'output', 'params': {}})
    edges = [{'source': a['id'], 'target': b['id']} for a, b in zip(nodes, nodes[1:])]
    r = client.post(f'/api/projects/{pid}/workflows', json={'name': '知识证据复核', 'nodes': nodes, 'edges': edges})
    assert r.status_code == 201, r.text
    return r.json()


def test_workflow_human_resume_persists(client):
    p = project(client)
    client.post(f'/api/projects/{p["id"]}/documents', json={'name': '知识', 'text': '北斗定位天线接头需要安装牢固。'})
    wf = flow(client, p['id'], True)
    r = client.post(f'/api/workflows/{wf["id"]}/run', json={'input': {}})
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()['id'])
    assert job['status'] == 'WAITING_APPROVAL', job
    a = client.get(f'/api/projects/{p["id"]}/approvals').json()[0]
    assert a['status'] == 'PENDING'
    assert client.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': a['revision']}).status_code == 200
    job = wait_job(client, job['id'])
    assert job['status'] == 'SUCCEEDED', job
    assert job['result']['outputs']['out']['matches'][0]['name'] == '知识'
    assert len([a for a in client.get(f'/api/projects/{p["id"]}/approvals').json() if a['approval_type'] == 'workflow']) == 1


def test_cancel_waiting_workflow_blocks_later_approval(client):
    p = project(client)
    wf = flow(client, p['id'], True)
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={}).json()
    job = wait_job(client, j['id'])
    assert client.post(f'/api/jobs/{job["id"]}/cancel', json={'expected_revision': job['revision']}).status_code == 200
    a = client.get(f'/api/projects/{p["id"]}/approvals').json()[0]
    assert client.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': a['revision']}).status_code == 409
    assert client.get('/api/jobs/' + job['id']).json()['status'] == 'CANCELLED'


def test_workflow_rejects_invalid_graphs(client):
    p = project(client)
    base = {'name': 'invalid', 'nodes': [{'id': 'a', 'type': 'input'}, {'id': 'b', 'type': 'output'}], 'edges': [{'source': 'a', 'target': 'b'}]}
    for patch in [
        {'edges': [{'source': 'a', 'target': 'missing'}]},
        {'edges': [{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'a'}]},
        {'nodes': [{'id': 'a', 'type': 'input'}, {'id': 'a', 'type': 'output'}]},
        {'nodes': [{'id': 'a', 'type': 'shell', 'params': {'command': 'echo hi'}}, {'id': 'b', 'type': 'output'}]},
        {'edges': []},
    ]:
        assert client.post(f'/api/projects/{p["id"]}/workflows', json={**base, **patch}).status_code == 400


def test_workflow_data_operations_and_cross_project_asset(client):
    p = project(client)
    nodes = [{'id': 'in', 'type': 'input'}, {'id': 'clean', 'type': 'clean', 'params': {'operations': [{'type': 'drop_duplicates'}]}}, {'id': 'out', 'type': 'output'}]
    wf = client.post(f'/api/projects/{p["id"]}/workflows', json={'name': '清洗流', 'nodes': nodes, 'edges': [{'source': 'in', 'target': 'clean'}, {'source': 'clean', 'target': 'out'}]}).json()
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={'input': {'rows': [{'x': 1}, {'x': 1}, {'x': 2}]}}).json()
    done = wait_job(client, j['id'])
    assert done['status'] == 'SUCCEEDED', done
    assert done['result']['outputs']['out']['rows'] == [{'x': 1}, {'x': 2}]
    foreign = dataset(client, project(client, 'other'))
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={'input': {'asset_id': foreign['id']}}).json()
    assert wait_job(client, j['id'])['status'] == 'FAILED'


def test_unconfigured_connector_is_honest(client, monkeypatch):
    monkeypatch.delenv('ARD_OLLAMA_URL', raising=False)
    assert client.get('/api/connectors').json()['ollama']['configured'] is False
    assert client.post('/api/connectors/ollama/probe').status_code == 400


def test_invalid_node_options_rejected_before_execution(client):
    p = project(client)
    for params in ({'query': 'x', 'mode': 'fake'}, {'query': 'x', 'top_k': -1}, {'query': 'x', 'surprise': True}):
        nodes = [{'id': 'a', 'type': 'input'}, {'id': 'b', 'type': 'retrieve', 'params': params}, {'id': 'c', 'type': 'output'}]
        r = client.post(f'/api/projects/{p["id"]}/workflows', json={'name': 'bad', 'nodes': nodes,
                        'edges': [{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'c'}]})
        assert r.status_code == 400, r.text


def test_waiting_job_survives_restart_and_can_resume(tmp_path):
    from ard.api import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(tmp_path)) as c:
        p = project(c)
        wf = flow(c, p['id'], True)
        j = c.post(f'/api/workflows/{wf["id"]}/run', json={}).json()
        assert wait_job(c, j['id'])['status'] == 'WAITING_APPROVAL'
    with TestClient(create_app(tmp_path)) as c:
        a = c.get(f'/api/projects/{p["id"]}/approvals').json()[0]
        r = c.post(f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': a['revision']})
        assert r.status_code == 200, r.text
        assert wait_job(c, j['id'])['status'] == 'SUCCEEDED'


def test_active_job_quota_released_after_cancel(client):
    p = client.post('/api/projects', json={'name': 'quota', 'max_jobs': 1}).json()
    wf = flow(client, p['id'], True)
    j = client.post(f'/api/workflows/{wf["id"]}/run', json={}).json()
    job = wait_job(client, j['id'])
    assert client.post(f'/api/workflows/{wf["id"]}/run', json={}).status_code == 400
    assert client.post(f'/api/jobs/{job["id"]}/cancel', json={'expected_revision': job['revision']}).status_code == 200
    assert client.post(f'/api/workflows/{wf["id"]}/run', json={}).status_code == 202
