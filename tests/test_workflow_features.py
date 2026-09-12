"""Functional evidence for real workflow transforms, checkpoints and granted skills."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from ard.api import create_app
from ard.features import skills, workflows
from test_platform import project, dataset, wait_job, approve


def app_at(path, identities=None):
    app = create_app(path, identities=identities)
    workflows.install(app); workflows.install(app)
    skills.install(app); skills.install(app)
    return app


@pytest.fixture
def feature_client(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        yield client


def make_flow(c, pid, middle, edges=None):
    nodes = [{'id': 'in', 'type': 'input'}, *middle, {'id': 'out', 'type': 'output'}]
    if edges is None:
        edges = [{'source': a['id'], 'target': b['id']} for a, b in zip(nodes, nodes[1:])]
    result = c.post(f'/api/projects/{pid}/workflows', json={'name': '可复核的合成流程', 'nodes': nodes, 'edges': edges})
    assert result.status_code == 201, result.text
    return result.json()


def start(c, flow, input=None):
    response = c.post(f'/api/workflows/{flow["id"]}/run', json={'input': input or {}})
    assert response.status_code == 202, response.text
    return response.json()


def skill(c, pid, operation, defaults=None, parent_id=None):
    response = c.post(f'/api/projects/{pid}/skills', json={'name': operation, 'operation': operation,
        'defaults': defaults or {}, 'parent_id': parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def call(c, record, arguments):
    response = c.post(f'/api/skills/{record["id"]}/invoke', json={'arguments': arguments})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'SUCCEEDED'
    return response.json()['result']


def test_install_once_and_workflow_export_version_roundtrip(feature_client):
    c = feature_client
    route_count = len(c.app.routes)
    workflows.install(c.app); skills.install(c.app)
    assert len(c.app.routes) == route_count
    assert {'workflows', 'skills'} <= c.app.state.features
    assert len(c.get('/api/workflow-node-types').json()['node_types']) == 16
    p = project(c)
    flow = make_flow(c, p['id'], [])
    exported = c.get(f'/api/workflows/{flow["id"]}/export').json()
    copied = c.post(f'/api/projects/{p["id"]}/workflows/import', json=exported)
    assert copied.status_code == 201, copied.text
    assert copied.json()['nodes'] == flow['nodes']
    version = c.post(f'/api/workflows/{flow["id"]}/versions', json=exported['workflow'])
    assert version.status_code == 201 and version.json()['parent_id'] == flow['id']
    history = c.get(f'/api/workflows/{flow["id"]}/versions').json()
    assert {r['id'] for r in history} == {flow['id'], version.json()['id']}
    assert c.get(f'/api/workflows/{flow["id"]}/export').json() == exported


def test_branch_variables_iteration_and_output_dataset(feature_client):
    c = feature_client; p = project(c)
    middle = [
        {'id': 'vars', 'type': 'variable', 'params': {'values': {'batch': {'$path': 'batch'}, 'prefix': '样本'}}},
        {'id': 'gate', 'type': 'condition', 'params': {'path': 'enabled', 'op': 'eq', 'value': True}},
        {'id': 'loop', 'type': 'iterate', 'params': {'max_items': 2, 'operations': [{'type': 'strip'}],
            'template': '{{variables.prefix}} {{item.name}} / {{variables.batch}}', 'target': 'summary'}},
        {'id': 'no', 'type': 'template', 'params': {'template': '未启用 {{variables.batch}}'}},
        {'id': 'save', 'type': 'dataset_output', 'params': {'name': '实际迭代结果'}}]
    edges = [{'source': 'in', 'target': 'vars'}, {'source': 'vars', 'target': 'gate'},
        {'source': 'gate', 'target': 'loop', 'when': True}, {'source': 'gate', 'target': 'no', 'when': False},
        {'source': 'loop', 'target': 'save'}, {'source': 'save', 'target': 'out'}, {'source': 'no', 'target': 'out'}]
    flow = make_flow(c, p['id'], middle, edges)
    run = wait_job(c, start(c, flow, {'enabled': True, 'batch': 'B01', 'rows': [{'name': ' A '}, {'name': ' B '} ]})['id'])
    assert run['status'] == 'SUCCEEDED', run
    output = run['result']['outputs']['out']
    assert output['rows'] == [{'name': 'A', 'summary': '样本 A / B01'}, {'name': 'B', 'summary': '样本 B / B01'}]
    assert output['dataset']['parents'] == []
    assert c.get(f'/api/assets/{output["asset_id"]}/rows').json()['rows'] == output['rows']
    assert run['state']['branches'] == {'gate': True}
    assert run['result']['skipped_nodes'] == ['no']
    other = wait_job(c, start(c, flow, {'enabled': False, 'batch': 'B02'})['id'])
    assert other['result']['outputs']['out']['text'] == '未启用 B02'
    assert set(other['result']['skipped_nodes']) == {'loop', 'save'}
    assert len(c.get(f'/api/projects/{p["id"]}/datasets').json()) == 1


def test_iteration_limits_and_expression_rejection(feature_client):
    c = feature_client; p = project(c)
    flow = make_flow(c, p['id'], [{'id': 'loop', 'type': 'iterate', 'params': {'max_items': 1, 'template': '{{item.x}}'}}])
    done = wait_job(c, start(c, flow, {'rows': [{'x': 1}, {'x': 2}]})['id'])
    assert done['status'] == 'FAILED' and 'max_items' in done['error']
    for node in [
        {'id': 'bad', 'type': 'iterate', 'params': {'max_items': 101, 'template': '{{item.x}}'}},
        {'id': 'bad', 'type': 'iterate', 'params': {'max_items': True, 'template': '{{item.x}}'}},
        {'id': 'bad', 'type': 'template', 'params': {'template': '{{__import__("os").system("id")}}'}},
        {'id': 'bad', 'type': 'variable', 'params': {'values': {'x': {'$path': '__class__.__mro__'}}}},
        {'id': 'bad', 'type': 'iterate', 'params': {'operations': [{'type': 'shell', 'command': 'id'}]}},
    ]:
        nodes = [{'id': 'in', 'type': 'input'}, node, {'id': 'out', 'type': 'output'}]
        r = c.post(f'/api/projects/{p["id"]}/workflows', json={'name': '拒绝表达式', 'nodes': nodes,
            'edges': [{'source': 'in', 'target': 'bad'}, {'source': 'bad', 'target': 'out'}]})
        assert r.status_code == 400, r.text


def test_pause_is_cooperative_persists_and_resume_finishes(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    from ard import workflows as engine
    original = engine.transform
    def slow(rows, operations):
        entered.set()
        assert release.wait(5)
        return original(rows, operations)
    with TestClient(app_at(tmp_path)) as c:
        p = project(c)
        flow = make_flow(c, p['id'], [{'id': 'clean', 'type': 'clean', 'params': {'operations': [{'type': 'drop_duplicates'}]}}])
        with monkeypatch.context() as patch:
            patch.setattr(engine, 'transform', slow)
            job = start(c, flow, {'rows': [{'x': 1}, {'x': 1}]})
            try:
                assert entered.wait(5)
                current = c.get(f'/api/jobs/{job["id"]}').json()
                requested = c.post(f'/api/jobs/{job["id"]}/pause', json={'expected_revision': current['revision']})
                assert requested.status_code == 200, requested.text
                assert requested.json()['status'] == 'RUNNING'
                assert requested.json()['pause_requested'] is True
            finally:
                release.set()
            paused = wait_job(c, job['id'], terminal=('PAUSED', 'FAILED'))
            assert paused['status'] == 'PAUSED'
            assert 'in' in paused['state']['outputs'] and 'clean' not in paused['state']['outputs']
    with TestClient(app_at(tmp_path)) as c:
        current = c.get(f'/api/jobs/{job["id"]}').json()
        assert current['status'] == 'PAUSED'
        resumed = c.post(f'/api/jobs/{job["id"]}/resume', json={'expected_revision': current['revision']})
        assert resumed.status_code == 202, resumed.text
        done = wait_job(c, job['id'])
        assert done['status'] == 'SUCCEEDED' and done['result']['outputs']['out']['rows'] == [{'x': 1}]


def test_retry_preserves_approval_and_side_effect_checkpoint(feature_client, monkeypatch):
    c = feature_client; p = project(c)
    flow = make_flow(c, p['id'], [{'id': 'review', 'type': 'human'},
        {'id': 'save', 'type': 'dataset_output', 'params': {'name': '一次封存'}},
        {'id': 'answer', 'type': 'llm', 'params': {'prompt': '概述输入'}}])
    import ard.connectors
    def unavailable(prompt):
        raise ValueError('测试服务暂不可用')
    monkeypatch.setattr(ard.connectors, 'chat', unavailable)
    job = start(c, flow, {'rows': [{'x': 1}]})
    waiting = wait_job(c, job['id'])
    review = c.get(f'/api/projects/{p["id"]}/approvals').json()[0]
    assert waiting['status'] == 'WAITING_APPROVAL'
    assert c.post(f'/api/approvals/{review["id"]}/decide', json={'decision': 'approve', 'expected_revision': 1}).status_code == 200
    failed = wait_job(c, job['id'])
    assert failed['status'] == 'FAILED'
    saved = failed['state']['outputs']['save']['asset_id']
    monkeypatch.setattr(ard.connectors, 'chat', lambda prompt: {'answer': '测试协议返回', 'model': 'fixture'})
    retried = c.post(f'/api/jobs/{job["id"]}/retry', json={'expected_revision': failed['revision']})
    assert retried.status_code == 202, retried.text
    assert c.post(f'/api/jobs/{job["id"]}/retry', json={'expected_revision': failed['revision']}).status_code in (400, 409)
    done = wait_job(c, job['id'])
    assert done['status'] == 'SUCCEEDED' and done['retry_count'] == 1
    assert done['state']['outputs']['save']['asset_id'] == saved
    assert len(c.get(f'/api/projects/{p["id"]}/datasets').json()) == 1
    assert len(c.get(f'/api/projects/{p["id"]}/approvals').json()) == 1


def test_effect_checkpoint_failure_rolls_back_then_retry_creates_once(feature_client, monkeypatch):
    c = feature_client; p = project(c)
    flow = make_flow(c, p['id'], [{'id': 'save', 'type': 'dataset_output', 'params': {'name': '原子封存'}}])
    store = c.app.state.service.store
    original = store.update
    def fault(identifier, changes, *args, **kwargs):
        if 'save' in changes.get('state', {}).get('outputs', {}):
            raise RuntimeError('checkpoint injection')
        return original(identifier, changes, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'update', fault)
        job = start(c, flow, {'rows': [{'x': 1}]})
        failed = wait_job(c, job['id'])
        assert failed['status'] == 'FAILED'
    assert c.get(f'/api/projects/{p["id"]}/datasets').json() == []
    assert c.post(f'/api/jobs/{job["id"]}/retry', json={'expected_revision': failed['revision']}).status_code == 202
    assert wait_job(c, job['id'])['status'] == 'SUCCEEDED'
    assert len(c.get(f'/api/projects/{p["id"]}/datasets').json()) == 1
    assert store.verify_audit()['valid']


def test_cancel_and_reject_cannot_be_bypassed_by_retry(feature_client):
    c = feature_client; p = project(c)
    flow = make_flow(c, p['id'], [{'id': 'review', 'type': 'human'}])
    for mode in ('cancel', 'reject'):
        job = wait_job(c, start(c, flow)['id'])
        if mode == 'cancel':
            assert c.post(f'/api/jobs/{job["id"]}/cancel', json={'expected_revision': job['revision']}).status_code == 200
        else:
            review = next(a for a in c.get(f'/api/projects/{p["id"]}/approvals').json() if a['job_id'] == job['id'])
            assert c.post(f'/api/approvals/{review["id"]}/decide', json={'decision': 'reject', 'expected_revision': review['revision']}).status_code == 200
        current = c.get(f'/api/jobs/{job["id"]}').json()
        assert c.post(f'/api/jobs/{job["id"]}/retry', json={'expected_revision': current['revision']}).status_code == 400


def test_eight_real_builtin_operations_and_history(feature_client):
    c = feature_client; p = project(c)
    catalogue = c.get('/api/skills/builtins').json()
    assert len(catalogue) == len({r['operation'] for r in catalogue}) == 8
    data = dataset(c, p, [{'group': 'A', 'x': 1}, {'group': 'A', 'x': 1}, {'group': 'B', 'x': 4}])
    assert call(c, skill(c, p['id'], 'data.profile'), {'asset_id': data['id']})['row_count'] == 3
    cleaned = call(c, skill(c, p['id'], 'data.transform'), {'asset_id': data['id'], 'operations': [{'type': 'drop_duplicates'}]})
    assert cleaned['dataset']['row_count'] == 2 and cleaned['dataset']['parents'] == [data['id']]
    split = call(c, skill(c, p['id'], 'data.split'), {'asset_id': data['id'], 'ratio': .5})
    assert sum(x['row_count'] for x in split['datasets']) == 3
    aggregated = call(c, skill(c, p['id'], 'data.aggregate'), {'asset_id': data['id'], 'group_by': 'group', 'value_column': 'x'})
    assert aggregated['groups'] == [{'group': 'A', 'count': 2, 'numeric_count': 2, 'sum': 2., 'mean': 1.}, {'group': 'B', 'count': 1, 'numeric_count': 1, 'sum': 4., 'mean': 4.}]
    text = '合成传感器检测需要记录数值并进行质量复核。' * 10
    doc = c.post(f'/api/projects/{p["id"]}/documents', json={'name': '合成规范', 'text': text}).json()
    found = call(c, skill(c, p['id'], 'knowledge.search'), {'query': '传感器质量'})
    assert found['matches'][0]['document_id'] == doc['id']
    chunks = call(c, skill(c, p['id'], 'knowledge.chunk'), {'document_id': doc['id'], 'chunk_size': 100, 'overlap': 10})
    assert all(text[x['start']:x['end']] == x['text'] for x in chunks['chunks'])
    training = dataset(c, p); approve(c, training)
    job = c.post(f'/api/assets/{training["id"]}/train', json={'target': 'label', 'features': ['x'], 'task': 'classification'}).json()
    model = wait_job(c, job['id'])['result']['model_id']
    assert call(c, skill(c, p['id'], 'model.predict'), {'model_id': model, 'rows': [{'x': 0}, {'x': 59}]})['predictions'] == [0, 1]
    assert call(c, skill(c, p['id'], 'text.template'), {'template': '样本 {{id}}', 'values': {'id': 'S01'}})['text'] == '样本 S01'
    history = c.get(f'/api/projects/{p["id"]}/skill-runs').json()
    assert len(history) == 8 and all(r['status'] == 'SUCCEEDED' for r in history)


def test_skill_publication_permissions_versioning_and_cross_project(tmp_path):
    identities = {
        'developer-token-1234': {'user': 'dev', 'role': 'developer', 'projects': ['*']},
        'developer-token-5678': {'user': 'dev2', 'role': 'developer', 'projects': ['*']},
        'reviewer-token-12345': {'user': 'review', 'role': 'reviewer', 'projects': ['*']},
        'outsider-token-12345': {'user': 'outsider', 'role': 'developer', 'projects': []},
    }
    with TestClient(app_at(tmp_path, identities)) as c:
        c.headers['Authorization'] = 'Bearer developer-token-1234'
        p = project(c); first = skill(c, p['id'], 'text.template', {'template': '{{id}}'})
        package = c.get(f'/api/skills/{first["id"]}/export').json()
        assert c.post(f'/api/projects/{p["id"]}/skills/import', json=package).status_code == 201
        version = skill(c, p['id'], 'text.template', {'template': '版本2 {{id}}'}, first['id'])
        assert version['version'] == 2
        review = c.post(f'/api/skills/{first["id"]}/publication').json()
        body = {'decision': 'approve', 'expected_revision': 1}
        assert c.post(f'/api/skill-reviews/{review["id"]}/decide', json=body).status_code == 403
        c.headers['Authorization'] = 'Bearer developer-token-5678'
        assert c.post(f'/api/skills/{first["id"]}/invoke', json={'arguments': {'values': {'id': 'x'}}}).status_code == 403
        c.headers['Authorization'] = 'Bearer reviewer-token-12345'
        assert c.post(f'/api/skill-reviews/{review["id"]}/decide', json=body).status_code == 200
        assert c.post(f'/api/skill-reviews/{review["id"]}/decide', json=body).status_code == 409
        c.headers['Authorization'] = 'Bearer developer-token-5678'
        assert call(c, first, {'values': {'id': 'x'}})['text'] == 'x'
        assert c.post(f'/api/skills/{version["id"]}/invoke', json={'arguments': {'values': {'id': 'x'}}}).status_code == 403
        foreign = dataset(c, project(c, '另一合成项目'))
        profile_skill = skill(c, p['id'], 'data.profile')
        assert c.post(f'/api/skills/{profile_skill["id"]}/invoke', json={'arguments': {'asset_id': foreign['id']}}).status_code == 400
        c.headers['Authorization'] = 'Bearer outsider-token-12345'
        assert c.get(f'/api/skills/{first["id"]}/export').status_code == 403


def test_malicious_skill_packages_and_bounded_agent(feature_client, monkeypatch):
    c = feature_client; p = project(c)
    for body in [
        {'name': '脚本', 'operation': 'shell', 'defaults': {'command': 'id'}},
        {'name': '端点', 'operation': 'text.template', 'defaults': {'url': 'http://example.invalid'}},
        {'name': '未知清洗', 'operation': 'data.transform', 'defaults': {'operations': [{'type': 'shell', 'command': 'id'}]}},
    ]:
        assert c.post(f'/api/projects/{p["id"]}/skills', json=body).status_code == 400
    selected = skill(c, p['id'], 'text.template', {'template': '实际 {{value}}'})
    import ard.connectors
    for env in ('ARD_CHAT_MODEL', 'ARD_OLLAMA_URL', 'ARD_OPENAI_URL', 'ARD_OPENAI_MODEL'):
        monkeypatch.delenv(env, raising=False)
    request = {'goal': '合成文本', 'skill_ids': [selected['id']], 'max_steps': 1, 'input': {'value': '输出'}}
    missing = c.post(f'/api/projects/{p["id"]}/agents/run', json=request)
    assert missing.status_code == 400
    assert c.get(f'/api/projects/{p["id"]}/agent-runs').json()[0]['status'] == 'FAILED'
    for plan in [
        {'steps': [{'skill_id': 'not-granted', 'arguments': {}}]},
        {'steps': [{'skill_id': selected['id'], 'arguments': {'values': {'value': 'x'}}}] * 2},
        {'steps': [{'skill_id': selected['id'], 'arguments': {'values': {'value': 'x'}, 'command': 'id'}}]},
    ]:
        monkeypatch.setattr(ard.connectors, 'chat', lambda prompt, plan=plan: {'answer': json.dumps(plan), 'model': 'fixture'})
        assert c.post(f'/api/projects/{p["id"]}/agents/run', json=request).status_code == 400
    assert c.get(f'/api/projects/{p["id"]}/skill-runs').json() == []
    monkeypatch.setattr(ard.connectors, 'chat', lambda prompt: {'answer': json.dumps({'steps': [{'skill_id': selected['id'], 'arguments': {'values': {'value': '输出'}}}]}), 'model': 'fixture'})
    success = c.post(f'/api/projects/{p["id"]}/agents/run', json=request)
    assert success.status_code == 200 and success.json()['status'] == 'SUCCEEDED'
    runs = c.get(f'/api/projects/{p["id"]}/skill-runs').json()
    assert len(runs) == 1 and runs[0]['result']['text'] == '实际 输出'


def test_template_rejects_amplification_before_allocating_large_result():
    import tracemalloc
    from ard.workflow_values import render_template
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match='1MiB'):
            render_template('{{payload}}' * 1000, {'payload': 'x' * 200000})
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 12_000_000
    finally:
        tracemalloc.stop()


def test_paused_job_counts_against_current_project_quota(feature_client, monkeypatch):
    c = feature_client
    p = c.post('/api/projects', json={'name': '协作暂停配额', 'max_jobs': 1}).json()
    flow = make_flow(c, p['id'], [])
    service = c.app.state.service
    monkeypatch.setattr(service.jobs, 'enqueue', lambda identifier: None)
    payload = {'kind': 'workflow', 'workflow_id': flow['id'], 'input': {}}
    job = service.jobs.submit(p['id'], payload, 'local', max_active=20)
    paused = service.jobs.pause(job['id'], 'local', job['revision'])
    assert paused['status'] == 'PAUSED'
    with pytest.raises(ValueError, match='配额'):
        service.jobs.submit(p['id'], payload, 'local', max_active=20)
    assert service.store.get(job['id'])['status'] == 'PAUSED'


def test_cancel_running_cpu_workflow_does_not_register_later_output(feature_client, monkeypatch):
    c = feature_client; p = project(c)
    flow = make_flow(c, p['id'], [{'id': 'clean', 'type': 'clean', 'params': {'operations': [{'type': 'strip'}]}},
        {'id': 'save', 'type': 'dataset_output', 'params': {'name': '不能创建'}}])
    from ard import workflows as engine
    entered, release = threading.Event(), threading.Event()
    original = engine.transform
    def slow(rows, operations):
        entered.set()
        assert release.wait(5)
        return original(rows, operations)
    monkeypatch.setattr(engine, 'transform', slow)
    job = start(c, flow, {'rows': [{'x': ' A '}]})
    try:
        assert entered.wait(5)
        current = c.get(f'/api/jobs/{job["id"]}').json()
        assert c.post(f'/api/jobs/{job["id"]}/cancel', json={'expected_revision': current['revision']}).status_code == 200
    finally:
        release.set()
    c.app.state.service.jobs.close()
    assert c.get(f'/api/jobs/{job["id"]}').json()['status'] == 'CANCELLED'
    assert c.get(f'/api/projects/{p["id"]}/datasets').json() == []


def test_skill_versions_are_immutable_and_restart_reconciles_calls(feature_client, tmp_path):
    c = feature_client; p = project(c)
    definition = skill(c, p['id'], 'text.template')
    with pytest.raises(ValueError, match='immutable'):
        c.app.state.service.store.update(definition['id'], {'operation': 'shell'}, 'local')
    for kind in ('skill_run', 'agent_run'):
        c.app.state.service.store.create(kind, p['id'], {'status': 'RUNNING'}, 'local')
    from ard.service import Service
    reopened = Service(tmp_path)
    try:
        for kind in ('skill_run', 'agent_run'):
            assert reopened.store.list(kind, p['id'])[0]['status'] == 'INTERRUPTED'
    finally:
        reopened.close()


def _fixed_definition(kind, identifier):
    params = {'asset_id': identifier, 'target': 'label'} if kind == 'train' else {'model_id': identifier}
    return {'name': '固定引用安全边界', 'nodes': [{'id': 'in', 'type': 'input'},
        {'id': 'fixed', 'type': kind, 'params': params}, {'id': 'out', 'type': 'output'}],
        'edges': [{'source': 'in', 'target': 'fixed'}, {'source': 'fixed', 'target': 'out'}]}


def _definition_requests(c, pid, parent_id, definition):
    return [
        c.post(f'/api/projects/{pid}/workflows', json=definition),
        c.post(f'/api/projects/{pid}/workflows/import', json={'format': 'ard-workflow-v1', 'workflow': definition}),
        c.post(f'/api/workflows/{parent_id}/versions', json=definition),
        c.post(f'/api/projects/{pid}/workflows/validate', json=definition),
    ]


def test_all_workflow_save_routes_reject_archived_fixed_dataset(feature_client):
    c = feature_client; p = project(c)
    parent = make_flow(c, p['id'], [])
    asset = dataset(c, p)
    archived = c.post(f'/api/assets/{asset["id"]}/archive', json={
        'archived': True, 'confirm': asset['id'], 'expected_revision': 0, 'reason': '合成归档验证'})
    assert archived.status_code == 201, archived.text
    store = c.app.state.service.store
    before = store.verify_audit()
    for response in _definition_requests(c, p['id'], parent['id'], _fixed_definition('train', asset['id'])):
        assert response.status_code == 409, response.text
        assert '归档' in response.text
    assert [r['id'] for r in c.get(f'/api/projects/{p["id"]}/workflows').json()] == [parent['id']]
    assert store.verify_audit() == before


def test_all_workflow_save_routes_reject_cross_project_fixed_assets(feature_client):
    c = feature_client; target = project(c); other = project(c, '其他项目固定引用')
    parent = make_flow(c, target['id'], [])
    foreign = dataset(c, other)
    # This minimal immutable model record exercises reference identity; it is never executed.
    model = c.app.state.service.store.create('model', other['id'], {'name': '合成外部模型记录'}, 'local')
    for kind, identifier in [('train', foreign['id']), ('predict', model['id'])]:
        for response in _definition_requests(c, target['id'], parent['id'], _fixed_definition(kind, identifier)):
            assert response.status_code == 400, response.text
            assert '其他项目' in response.text
    for response in _definition_requests(c, target['id'], parent['id'], _fixed_definition('predict', 'missing-model')):
        assert response.status_code == 404, response.text
    assert [r['id'] for r in c.get(f'/api/projects/{target["id"]}/workflows').json()] == [parent['id']]


def test_workflow_create_and_import_reject_foreign_parent_without_writes(feature_client):
    c = feature_client; p = project(c); other = project(c, '其他工作流版本')
    parent = make_flow(c, other['id'], [])
    definition = {key: parent[key] for key in ('name', 'nodes', 'edges')}
    before = c.app.state.service.store.verify_audit()
    results = [c.post(f'/api/projects/{p["id"]}/workflows', json={**definition, 'parent_id': parent['id']}),
        c.post(f'/api/projects/{p["id"]}/workflows/import', json={
            'format': 'ard-workflow-v1', 'workflow': definition, 'parent_id': parent['id']})]
    assert all(r.status_code == 400 for r in results), [r.text for r in results]
    assert c.get(f'/api/projects/{p["id"]}/workflows').json() == []
    assert c.app.state.service.store.verify_audit() == before


def test_workflow_save_serializes_active_check_with_concurrent_archive(feature_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from ard.features import data as data_features
    c = feature_client; p = project(c); asset = dataset(c, p)
    definition = _fixed_definition('train', asset['id'])
    checked, release, archive_started = threading.Event(), threading.Event(), threading.Event()
    original = data_features.require_active
    def held_check(service, record):
        result = original(service, record)
        if record['id'] == asset['id']:
            checked.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(data_features, 'require_active', held_check)
    def archive_request():
        archive_started.set()
        return c.post(f'/api/assets/{asset["id"]}/archive', json={
            'archived': True, 'confirm': asset['id'], 'expected_revision': 0})
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(c.post, f'/api/projects/{p["id"]}/workflows/import',
            json={'format': 'ard-workflow-v1', 'workflow': definition})
        try:
            assert checked.wait(5)
            archival = pool.submit(archive_request)
            assert archive_started.wait(5)
            # The real archival write must wait for the validated workflow to commit.
            with pytest.raises(TimeoutError):
                archival.result(timeout=.2)
        finally:
            release.set()
        saved, archived = saving.result(timeout=5), archival.result(timeout=5)
    assert saved.status_code == 201, saved.text
    assert archived.status_code == 409, archived.text
    assert c.get(f'/api/assets/{asset["id"]}/references').json()['archive']['archived'] is False
    assert len(c.get(f'/api/projects/{p["id"]}/workflows').json()) == 1
