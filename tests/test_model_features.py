"""CPU feature tests and synthetic HTTP protocol fixtures (not live model services)."""
import asyncio
import json
import math
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import numpy as np
import pytest
from sklearn.model_selection import train_test_split

from ard.api import create_app
from ard import connectors
from ard.features.models import install


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    install(app)
    count = len(app.routes)
    install(app)
    assert len(app.routes) == count
    with TestClient(app) as client:
        yield client


def package(name='合成回归模型', coefficient=2.):
    return {'format': 'ard.model-package', 'version': 1, 'name': name, 'target': 'y', 'artifact': {
        'format': 'ard.linear-model', 'version': 1, 'task': 'regression', 'features': ['x'],
        'preprocessing': {'imputer': {'strategy': 'median', 'statistics': [0.]},
                          'scaler': {'mean': [0.], 'scale': [1.]}},
        'estimator': {'type': 'linear_regression', 'coefficients': [[coefficient]], 'intercepts': [1.]}}}


def project(client, name='合成模型测试'):
    response = client.post('/api/projects', json={'name': name})
    assert response.status_code == 201, response.text
    return response.json()


def dataset(client, pid, rows=None, name='合成评估数据'):
    response = client.post(f'/api/projects/{pid}/datasets', json={
        'name': name, 'rows': rows if rows is not None else [{'x': index, 'y': 2 * index + 1} for index in range(24)]})
    assert response.status_code == 201, response.text
    return response.json()


def import_package(client, pid, value=None, **extra):
    response = client.post(f'/api/projects/{pid}/models/import', json={'package': value or package(), **extra})
    assert response.status_code == 201, response.text
    return response.json()


def wait_job(client, job_id):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            assert job['status'] == 'SUCCEEDED', job
            return job
        time.sleep(.01)
    pytest.fail('CPU training job did not complete')


def approve(client, data):
    approval = client.post(f'/api/assets/{data["id"]}/transfer', json={}).json()
    response = client.post(f'/api/approvals/{approval["id"]}/decide', json={
        'decision': 'approve', 'expected_revision': approval['revision']})
    assert response.status_code == 200, response.text


def test_import_export_predictions_versions_and_persistence(client, tmp_path):
    pid = project(client)['id']
    model = import_package(client, pid)
    assert model['metrics'] == {} and model['dataset_id'] is None
    export = client.get(f'/api/models/{model["id"]}/package')
    assert export.json() == package()
    assert export.headers['content-disposition'].startswith('attachment;')
    deployment = client.post(f'/api/models/{model["id"]}/deploy', json={}).json()
    result = client.post(f'/api/deployments/{deployment["id"]}/predict', json={'rows': [{'x': 3}, {'x': None}]}).json()
    assert result['predictions'] == [7., 1.]
    child = client.post(f'/api/models/{model["id"]}/versions', json={'name': '合成模型新版本'}).json()
    assert child['id'] != model['id'] and child['sha256'] == model['sha256']
    assert child['parent_id'] == model['id'] and child['version_number'] == 2
    sibling = import_package(client, pid, package(coefficient=3.), parent_model_id=model['id'])
    assert sibling['parent_id'] == model['id']
    assert len(client.get(f'/api/models/{child["id"]}/versions').json()) == 3
    assert client.get(f'/api/models/{model["id"]}').json() == model
    with pytest.raises(ValueError, match='immutable'):
        client.app.state.service.store.update(model['id'], {'name': 'bad'}, 'test')
    from ard.store import Store
    reopened = Store(tmp_path)
    assert reopened.get(child['id'], 'model')['parent_id'] == model['id']
    assert json.loads(reopened.read_blob(model['sha256'])) == package()['artifact']


def test_export_preserves_long_legacy_generated_model_names(client):
    pid = project(client)['id']
    name = '合成' * 150 + ' · 目标列'
    model = import_package(client, pid, package(name=name))
    response = client.get(f'/api/models/{model["id"]}/package')
    assert response.status_code == 200 and response.json()['name'] == name


@pytest.mark.parametrize('change', [
    lambda p: p.update(format='pickle'),
    lambda p: p.update(version=True),
    lambda p: p.update(target='x'),
    lambda p: p['artifact'].update(version=True),
    lambda p: p['artifact'].update(features=['x', 'x']),
    lambda p: p['artifact'].update(features=['']),
    lambda p: p['artifact'].update(preprocessing=[]),
    lambda p: p['artifact']['preprocessing']['imputer'].update(strategy='mean'),
    lambda p: p['artifact']['preprocessing']['scaler'].update(scale=[0.]),
    lambda p: p['artifact']['preprocessing']['scaler'].update(mean=[1., 2.]),
    lambda p: p['artifact']['estimator'].update(coefficients=[[1., 2.]]),
    lambda p: p['artifact']['estimator'].update(coefficients=[[]]),
    lambda p: p['artifact']['estimator'].update(coefficients=[['2.0']]),
    lambda p: p['artifact']['estimator'].update(intercepts=[True]),
    lambda p: p['artifact']['estimator'].update(intercepts=[]),
    lambda p: p['artifact']['estimator'].update(type='logistic_regression'),
    lambda p: p['artifact']['estimator'].update(__reduce__='untrusted code'),
    lambda p: p.update(metrics={'accuracy': 1.}),
])
def test_import_rejects_invalid_schema_before_persistence(client, change):
    pid = project(client)['id']
    value = package()
    change(value)
    response = client.post(f'/api/projects/{pid}/models/import', json={'package': value})
    assert response.status_code in (400, 422), response.text
    assert client.get(f'/api/projects/{pid}/models').json() == []
    assert list(client.app.state.service.store.objects.iterdir()) == []


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_import_rejects_raw_nonfinite_json(client, value):
    pid = project(client)['id']
    body = package()
    body['artifact']['estimator']['coefficients'] = [[value]]
    response = client.post(f'/api/projects/{pid}/models/import', content=json.dumps({'package': body}),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code in (400, 422)
    assert client.get(f'/api/projects/{pid}/models').json() == []


@pytest.mark.parametrize('classes', [[0, 0], [0, 'one'], [None, 1], [False, 1], [[], []]])
def test_import_rejects_ambiguous_class_labels(client, classes):
    pid = project(client)['id']
    value = package()
    value['artifact'].update(task='classification')
    value['artifact']['estimator'].update(type='logistic_regression', classes=classes)
    response = client.post(f'/api/projects/{pid}/models/import', json={'package': value})
    assert response.status_code == 422


def test_real_reevaluation_metrics_report_and_target_exclusion(client, monkeypatch):
    pid = project(client)['id']
    value = package(name='<script>alert("model")</script>')
    model = import_package(client, pid, value)
    data = dataset(client, pid, [{'x': 0, 'y': 2}, {'x': 1, 'y': 3}, {'x': 2, 'y': 7}], name='<img src=x onerror=bad>')
    import ard.features.models as module
    real_predict = module.predict_model
    called = []
    def feature_only(artifact, rows):
        assert all(set(row) == {'x'} for row in rows)
        called.append(rows)
        return real_predict(artifact, rows)
    monkeypatch.setattr(module, 'predict_model', feature_only)
    response = client.post(f'/api/models/{model["id"]}/evaluate', json={'dataset_id': data['id'], 'name': '<script>report</script>'})
    assert response.status_code == 201, response.text
    evaluation = response.json()
    assert called
    assert evaluation['metrics']['mse'] == pytest.approx(5 / 3)
    assert evaluation['metrics']['mae'] == pytest.approx(1)
    assert evaluation['row_count'] == 3 and evaluation['independence'] == 'unknown_training_provenance'
    results = client.get(f'/api/model-evaluations/{evaluation["id"]}/rows').json()
    assert [item['prediction'] for item in results['rows']] == [1., 3., 5.]
    report = client.get(f'/api/model-evaluations/{evaluation["id"]}/report')
    assert report.status_code == 200 and '<script>' not in report.text and '<img ' not in report.text
    assert '&lt;script&gt;report&lt;/script&gt;' in report.text and '&lt;img' in report.text
    assert report.headers['content-disposition'].endswith('.html"')
    assert "default-src 'none'" in report.headers['content-security-policy']
    assert client.get(f'/api/models/{model["id"]}').json()['metrics'] == {}


def test_classification_evaluation_confusion_labels_and_escaping(client):
    pid = project(client)['id']
    value = package()
    value['artifact']['task'] = 'classification'
    value['artifact']['estimator'].update(type='logistic_regression', classes=['<b>低</b>', '<script>高</script>'], intercepts=[0.])
    model = import_package(client, pid, value)
    data = dataset(client, pid, [{'x': -1, 'y': '<b>低</b>'}, {'x': 1, 'y': '<script>高</script>'}, {'x': 2, 'y': '<b>低</b>'}])
    evaluation = client.post(f'/api/models/{model["id"]}/evaluate', json={'dataset_id': data['id']}).json()
    assert evaluation['metrics']['accuracy'] == pytest.approx(2 / 3)
    assert evaluation['metrics']['confusion_matrix'] == [[1, 1], [0, 1]]
    assert evaluation['metrics']['f1'] == pytest.approx(2 / 3)
    report = client.get(f'/api/model-evaluations/{evaluation["id"]}/report').text
    assert '<script>高' not in report and '&lt;script&gt;高&lt;/script&gt;' in report


def test_baseline_reuses_exact_dataset_config_and_evaluates_only_recorded_holdout(client):
    pid = project(client)['id']
    data = dataset(client, pid)
    cfg = {'target': 'y', 'task': 'regression', 'features': ['x'], 'test_fraction': .25, 'seed': 9}
    response = client.post(f'/api/projects/{pid}/model-baselines', json={'name': '合成基线', 'dataset_id': data['id'], 'config': cfg})
    assert response.status_code == 201, response.text
    baseline = response.json()
    assert client.post(f'/api/model-baselines/{baseline["id"]}/run').status_code == 409
    approve(client, data)
    job = client.post(f'/api/model-baselines/{baseline["id"]}/run').json()
    assert {key: job['payload'][key] for key in cfg} == cfg
    assert job['payload']['asset_id'] == data['id']
    job = wait_job(client, job['id'])
    model_id = job['result']['model_id']
    # A pending unrelated job must not break model-to-training-job lookup.
    client.app.state.service.store.create('job', pid, {'result': None, 'payload': {'kind': 'synthetic_fixture'}, 'status': 'PAUSED'}, 'test')
    evaluation = client.post(f'/api/models/{model_id}/evaluate', json={'dataset_id': data['id']}).json()
    assert evaluation['scope'] == 'original_holdout' and evaluation['row_count'] == 6
    expected = sorted(train_test_split(list(range(24)), test_size=.25, random_state=9)[1])
    rows = client.get(f'/api/model-evaluations/{evaluation["id"]}/rows').json()['rows']
    assert [row['source_row'] for row in rows] == expected
    assert evaluation['metrics']['mse'] < 1e-20
    imported = import_package(client, pid)
    comparison = client.post(f'/api/projects/{pid}/model-comparisons', json={
        'dataset_id': data['id'], 'model_ids': [model_id, imported['id']]}).json()
    assert comparison['row_count'] == 6
    for entry in comparison['entries']:
        common = client.get(f'/api/model-evaluations/{entry["evaluation_id"]}/rows').json()['rows']
        assert [row['source_row'] for row in common] == expected
    copied_rows = [{'x': str(index), 'y': 2 * index + 1} for index in range(24)]
    copied_rows.extend([{'x': 100, 'y': 201}, {'x': 101, 'y': 203}])
    copied = dataset(client, pid, copied_rows)
    copied_evaluation = client.post(f'/api/models/{model_id}/evaluate', json={'dataset_id': copied['id']}).json()
    assert copied_evaluation['row_count'] == 8 and copied_evaluation['overlap_rows_excluded'] == 18
    clone = client.post(f'/api/models/{model_id}/versions', json={'name': '已训练模型版本'}).json()
    assert clone['training_config'] == cfg
    child = client.post(f'/api/projects/{pid}/model-baselines', json={
        'name': '合成基线二版', 'dataset_id': data['id'], 'config': {**cfg, 'seed': 7}, 'parent_id': baseline['id']}).json()
    assert child['parent_id'] == baseline['id'] and child['version_number'] == 2
    assert client.get(f'/api/model-baselines/{baseline["id"]}').json() == baseline
    assert len(client.get(f'/api/model-baselines/{baseline["id"]}/runs').json()) == 1
    with pytest.raises(ValueError, match='immutable'):
        client.app.state.service.store.update(baseline['id'], {'config': {}}, 'test')


def test_comparison_uses_same_rows_and_is_atomic_when_one_model_fails(client):
    pid = project(client)['id']
    data = dataset(client, pid)
    first = import_package(client, pid)
    second = import_package(client, pid, package(coefficient=3.))
    response = client.post(f'/api/projects/{pid}/model-comparisons', json={
        'dataset_id': data['id'], 'model_ids': [first['id'], second['id']]})
    assert response.status_code == 201, response.text
    comparison = response.json()
    assert comparison['row_count'] == 24
    assert comparison['entries'][0]['metrics']['mse'] == 0
    assert comparison['entries'][1]['metrics']['mse'] > 100
    ids = [entry['evaluation_id'] for entry in comparison['entries']]
    row_sets = [client.get(f'/api/model-evaluations/{eid}/rows').json()['rows'] for eid in ids]
    assert [row['source_row'] for row in row_sets[0]] == [row['source_row'] for row in row_sets[1]]
    broken = package()
    broken['target'] = 'missing'
    broken_model = import_package(client, pid, broken)
    failed = client.post(f'/api/projects/{pid}/model-comparisons', json={
        'dataset_id': data['id'], 'model_ids': [first['id'], broken_model['id']]})
    assert failed.status_code == 400
    assert len(client.get(f'/api/projects/{pid}/model-evaluations').json()) == 2
    assert len(client.get(f'/api/projects/{pid}/model-comparisons').json()) == 1


def test_deployment_statistics_are_measured_and_expiry_is_effective(client):
    pid = project(client)['id']
    model = import_package(client, pid)
    deployment = client.post(f'/api/models/{model["id"]}/deploy', json={}).json()
    initial = client.get(f'/api/projects/{pid}/deployment-statistics').json()
    assert initial['requests'] == 0 and initial['mean_latency_ms'] is None
    assert client.post(f'/api/deployments/{deployment["id"]}/predict', json={'rows': [{'x': 2}]}).status_code == 200
    assert client.post(f'/api/deployments/{deployment["id"]}/predict', json={'rows': [{'x': 'invalid'}]}).status_code == 400
    measured = client.get(f'/api/projects/{pid}/deployment-statistics').json()
    assert measured['requests'] == 2 and measured['failures'] == 1 and measured['failure_rate'] == .5
    assert measured['mean_latency_ms'] > 0
    client.app.state.service.store.update(deployment['id'], {'expires_at': '2000-01-01T00:00:00+00:00'}, 'test')
    expired = client.get(f'/api/projects/{pid}/deployment-statistics').json()
    assert expired['active'] == 0 and expired['entries'][0]['status'] == 'EXPIRED'
    assert expired['requests'] == 2


def test_model_permissions_cross_project_denial_and_read_only_reports(tmp_path):
    identities = {
        'admin-token-abcdefghijkl': {'user': 'admin', 'role': 'admin', 'projects': ['*']},
        'audit-token-abcdefghijkl': {'user': 'auditor', 'role': 'auditor', 'projects': ['*']},
        'dev-token-abcdefghijklmn': {'user': 'developer', 'role': 'developer', 'projects': []},
    }
    app = create_app(tmp_path, identities=identities)
    install(app)
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer admin-token-abcdefghijkl'
        first, second = project(client), project(client)
        model = import_package(client, first['id'])
        data = dataset(client, first['id'])
        other_data = dataset(client, second['id'])
        assert client.post(f'/api/models/{model["id"]}/evaluate', json={'dataset_id': other_data['id']}).status_code == 400
        assert client.post(f'/api/projects/{second["id"]}/models/import', json={
            'package': package(), 'parent_model_id': model['id']}).status_code == 400
        evaluation = client.post(f'/api/models/{model["id"]}/evaluate', json={'dataset_id': data['id']}).json()
        client.headers['Authorization'] = 'Bearer audit-token-abcdefghijkl'
        assert client.get(f'/api/model-evaluations/{evaluation["id"]}/report').status_code == 200
        for path, body in [(f'/api/projects/{first["id"]}/models/import', {'package': package()}),
                           (f'/api/models/{model["id"]}/versions', {'name': 'version'}),
                           (f'/api/models/{model["id"]}/evaluate', {'dataset_id': data['id']}),
                           ('/api/model-services/chat', {'prompt': 'fixture'}),
                           ('/api/model-services/embeddings', {'texts': ['fixture']}),
                           ('/api/model-services/probe', {})]:
            assert client.post(path, json=body).status_code == 403, path
        identities['dev-token-abcdefghijklmn']['projects'] = [second['id']]
        client.headers['Authorization'] = 'Bearer dev-token-abcdefghijklmn'
        assert client.get(f'/api/models/{model["id"]}/package').status_code == 403
        assert client.get(f'/api/model-evaluations/{evaluation["id"]}/report').status_code == 403
        assert client.get(f'/api/projects/{first["id"]}/model-baselines').status_code == 403
        assert client.get(f'/api/projects/{first["id"]}/deployment-statistics').status_code == 403


def mock_protocol(monkeypatch, provider, handler):
    """In-process synthetic protocol fixture. No actual third-party server is contacted."""
    monkeypatch.setenv('ARD_MODEL_PROVIDER', provider)
    prefix = 'ARD_OPENAI_' if provider == 'openai' else 'ARD_'
    monkeypatch.setenv('ARD_OPENAI_URL' if provider == 'openai' else 'ARD_OLLAMA_URL',
                       'https://synthetic.invalid/v1' if provider == 'openai' else 'https://synthetic.invalid')
    monkeypatch.setenv(prefix + 'CHAT_MODEL', 'synthetic-chat')
    monkeypatch.setenv(prefix + 'EMBED_MODEL', 'synthetic-embedding')
    original = httpx.AsyncClient
    seen_options = []
    def client(**options):
        seen_options.append(options)
        return original(transport=httpx.MockTransport(handler), **options)
    monkeypatch.setattr(connectors.httpx, 'AsyncClient', client)
    return seen_options


@pytest.mark.parametrize('provider', ['ollama', 'openai'])
def test_synthetic_protocol_chat_model_list_embeddings_and_secret_masking(monkeypatch, provider):
    calls = []
    secret = 'synthetic-secret-not-live'
    monkeypatch.setenv('ARD_OPENAI_API_KEY', secret)
    def handler(request):
        calls.append(request)
        if request.url.path.endswith(('/models', '/tags')):
            assert request.method == 'GET'
            return httpx.Response(200, json={'data': [{'id': 'synthetic-chat'}]} if provider == 'openai' else {'models': [{'name': 'synthetic-chat'}]})
        payload = json.loads(request.content)
        if request.url.path.endswith(('/chat/completions', '/chat')):
            assert payload['messages'] == [{'role': 'user', 'content': '合成测试问题'}]
            assert payload['stream'] is False
            return httpx.Response(200, json={'choices': [{'message': {'content': '合成测试回答'}}], 'usage': {'completion_tokens': 7}}
                                  if provider == 'openai' else {'message': {'content': '合成测试回答'}, 'eval_count': 7})
        assert payload['input'] == ['甲', '乙']
        return httpx.Response(200, json={'data': [{'index': 1, 'embedding': [3., 4.]}, {'index': 0, 'embedding': [1., 2.]}]}
                              if provider == 'openai' else {'embeddings': [[1., 2.], [3., 4.]]})
    options = mock_protocol(monkeypatch, provider, handler)
    public = connectors.configuration()
    identity = connectors.embedding_identity()
    assert identity == {'provider': provider, 'model': 'synthetic-embedding', 'endpoint_sha256': public['endpoint_sha256']}
    assert len(identity['endpoint_sha256']) == 64
    assert secret not in json.dumps(public) and 'synthetic.invalid' not in json.dumps(public)
    assert connectors.model_probe()['models'] == ['synthetic-chat']
    assert connectors.chat('合成测试问题') == {'answer': '合成测试回答', 'model': 'synthetic-chat', 'eval_count': 7}
    np.testing.assert_array_equal(connectors.embeddings(['甲', '乙']), [[1., 2.], [3., 4.]])
    assert all(option['follow_redirects'] is False and option['trust_env'] is False for option in options)
    assert all(request.headers.get('Accept-Encoding') == 'identity' for request in calls)
    if provider == 'openai':
        assert all(request.headers['authorization'] == 'Bearer ' + secret for request in calls)
    assert connectors.capabilities()['ollama']['live_verified'] is False


@pytest.mark.parametrize('body', [[], None, 'bad', {'message': None}, {'message': []}, {'message': {'content': 9}},
                                   {'message': {'content': 'text'}, 'eval_count': -1}])
def test_synthetic_ollama_protocol_rejects_malformed_chat(monkeypatch, body):
    mock_protocol(monkeypatch, 'ollama', lambda request: httpx.Response(200, content=json.dumps(body)))
    with pytest.raises(ValueError):
        connectors.chat('fixture')


@pytest.mark.parametrize('body', [
    {'data': [{'index': 0, 'embedding': [1.]}, {'index': 0, 'embedding': [2.]}]},
    {'data': [{'index': False, 'embedding': [1.]}, {'index': 1, 'embedding': [2.]}]},
    {'data': [{'index': 0, 'embedding': [1.]}, {'index': 2, 'embedding': [2.]}]},
    {'data': [{'index': 0, 'embedding': [1.]}]},
    {'data': [{'index': 0, 'embedding': [1.]}, {'index': 1, 'embedding': [2., 3.]}]},
    {'data': [{'index': 0, 'embedding': [True]}, {'index': 1, 'embedding': [2.]}]},
    {'data': [{'index': 0, 'embedding': ['1']}, {'index': 1, 'embedding': [2.]}]},
    {'data': [{'index': 0, 'embedding': []}, {'index': 1, 'embedding': []}]},
    {'data': [None, None]},
])
def test_synthetic_openai_protocol_rejects_embedding_shape_count_index_and_types(monkeypatch, body):
    mock_protocol(monkeypatch, 'openai', lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError):
        connectors.embeddings(['first', 'second'])


@pytest.mark.parametrize('raw', [b'{bad json', b'{"embeddings":[[NaN]]}', b'{"embeddings":[[1e9999]]}',
                                  b'{"embeddings":[[Infinity]]}', b'{"embeddings":[[true]]}'])
def test_synthetic_protocol_rejects_malformed_nonfinite_responses(monkeypatch, raw):
    mock_protocol(monkeypatch, 'ollama', lambda request: httpx.Response(200, content=raw))
    with pytest.raises(ValueError):
        connectors.embeddings(['fixture'])


def test_synthetic_protocol_bounded_size_redirect_timeout_and_total_deadline(monkeypatch):
    modes = ['oversize', 'redirect', 'timeout', 'deadline']
    clock = [0.]
    def handler(request):
        if modes[0] == 'oversize':
            return httpx.Response(200, content=b' ' * (8 * 1024 * 1024 + 1))
        if modes[0] == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://never-follow.invalid'})
        if modes[0] == 'timeout':
            raise httpx.ReadTimeout('synthetic protocol timeout')
        clock[0] = 2.
        return httpx.Response(200, json={'models': []})
    mock_protocol(monkeypatch, 'ollama', handler)
    monkeypatch.setenv('ARD_MODEL_TIMEOUT_SECONDS', '1')
    monkeypatch.setattr(connectors, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    for message in ('8MiB', 'HTTP 302', '超时', '总时限'):
        with pytest.raises(ValueError, match=message):
            connectors.ollama_probe()
        modes.pop(0)


def test_synthetic_protocol_enforces_cross_batch_dimensions(monkeypatch):
    counter = [0]
    def handler(request):
        counter[0] += 1
        count = len(json.loads(request.content)['input'])
        return httpx.Response(200, json={'embeddings': [[1.] * counter[0] for _ in range(count)]})
    mock_protocol(monkeypatch, 'ollama', handler)
    with pytest.raises(ValueError, match='维度不一致'):
        connectors.embeddings(['fixture'] * 33)


@pytest.mark.parametrize('url', ['file:///tmp/data', 'https://user:secret@host/v1', 'https://host/v1?key=secret',
                                'https://host/v1#fragment', 'https://host:99999/v1'])
def test_model_endpoint_configuration_rejects_untrusted_shapes(monkeypatch, url):
    monkeypatch.setenv('ARD_MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('ARD_OPENAI_URL', url)
    monkeypatch.setenv('ARD_OPENAI_EMBED_MODEL', 'synthetic')
    with pytest.raises(ValueError):
        connectors.embedding_identity()


def test_synthetic_protocol_total_deadline_cancels_a_pending_response(monkeypatch):
    async def delayed_handler(request):
        await asyncio.sleep(3)
        return httpx.Response(200, json={'models': []})
    mock_protocol(monkeypatch, 'ollama', delayed_handler)
    monkeypatch.setenv('ARD_MODEL_TIMEOUT_SECONDS', '1')
    start = time.monotonic()
    with pytest.raises(ValueError, match='总时限'):
        connectors.ollama_probe()
    assert time.monotonic() - start < 2.5


def test_sync_connector_signature_also_works_inside_existing_event_loop(monkeypatch):
    mock_protocol(monkeypatch, 'ollama', lambda request: httpx.Response(200, json={'models': []}))
    async def caller():
        return connectors.ollama_probe()
    assert asyncio.run(caller())['status'] == 'connected'


@pytest.mark.parametrize('body', [
    {'choices': []}, {'choices': None}, {'choices': [None]}, {'choices': [{'message': None}]},
    {'choices': [{'message': {'content': 'fixture'}}], 'usage': []},
    {'choices': [{'message': {'content': 'fixture'}}], 'usage': {'completion_tokens': True}},
])
def test_synthetic_openai_protocol_rejects_malformed_chat(monkeypatch, body):
    mock_protocol(monkeypatch, 'openai', lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError):
        connectors.chat('fixture')


@pytest.mark.parametrize('provider,body', [
    ('ollama', {'models': [None]}), ('ollama', {'models': [{'name': ''}]}),
    ('openai', {'data': [{'id': 1}]}), ('openai', {'data': None}),
])
def test_synthetic_protocol_rejects_malformed_model_list(monkeypatch, provider, body):
    mock_protocol(monkeypatch, provider, lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError):
        connectors.model_probe()


def test_model_services_api_masks_configuration_and_disallows_client_endpoints(client, monkeypatch):
    secret = 'synthetic-never-live-secret'
    monkeypatch.setenv('ARD_OPENAI_API_KEY', secret)
    mock_protocol(monkeypatch, 'openai', lambda request: httpx.Response(200, json={'data': []}))
    response = client.get('/api/model-services/configuration')
    assert response.status_code == 200 and response.json()['api_key_configured'] is True
    assert secret not in response.text and 'synthetic.invalid' not in response.text
    assert secret not in client.get('/api/connectors').text
    assert client.post('/api/model-services/configuration', json={'endpoint': 'https://untrusted.invalid'}).status_code == 405
    assert client.post('/api/model-services/chat', json={'prompt': 'fixture', 'endpoint': 'https://untrusted.invalid'}).status_code == 422


def test_quota_failure_does_not_publish_model_or_comparison_records(client):
    pid = project(client)['id']
    data = dataset(client, pid)
    model = import_package(client, pid)
    second = import_package(client, pid, package(coefficient=3.))
    store = client.app.state.service.store
    used = sum(record.get('size_bytes', 0) for record in store.list(project_id=pid))
    store.update(pid, {'quota_bytes': used}, 'test')
    assert client.post(f'/api/projects/{pid}/models/import', json={'package': package()}).status_code == 400
    assert client.post(f'/api/projects/{pid}/model-comparisons', json={
        'dataset_id': data['id'], 'model_ids': [model['id'], second['id']]}).status_code == 400
    assert len(client.get(f'/api/projects/{pid}/models').json()) == 2
    assert client.get(f'/api/projects/{pid}/model-evaluations').json() == []
    assert client.get(f'/api/projects/{pid}/model-comparisons').json() == []


def test_evaluation_rejects_unbounded_new_class_labels_before_persistence(client):
    pid = project(client)['id']
    value = package()
    value['artifact']['task'] = 'classification'
    value['artifact']['estimator'].update(type='logistic_regression', classes=['a', 'b'])
    model = import_package(client, pid, value)
    data = dataset(client, pid, [{'x': index, 'y': str(index)} for index in range(257)])
    response = client.post(f'/api/models/{model["id"]}/evaluate', json={'dataset_id': data['id']})
    assert response.status_code == 400 and '256' in response.json()['detail']
    assert client.get(f'/api/projects/{pid}/model-evaluations').json() == []
