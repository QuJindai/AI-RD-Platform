"""Real HTTP functional acceptance using synthetic data; no application imports.

ARD_TOKEN may name a global admin. ARD_REVIEW_TOKEN must be a distinct reviewer
to exercise independent annotation review/export. Local mode verifies the
self-review denial and explicitly reports the remaining annotation steps unrun.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit
import uuid
import zipfile

import httpx


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class Acceptance:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('--base-url must be an HTTP(S) service URL without credentials/query/fragment')
        self.base_url = base_url.rstrip('/')
        self.started = time.monotonic()
        self.review_token = os.environ.get('ARD_REVIEW_TOKEN', '')
        token = os.environ.get('ARD_TOKEN', '')
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        self.client = httpx.Client(base_url=self.base_url, headers=headers,
            timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False)
        self.report = {'status': 'RUNNING', 'transport': 'real HTTP', 'data_classification': 'synthetic only',
            'started_at': datetime.now(timezone.utc).isoformat(), 'base_url': self.base_url,
            'checks': [], 'http_calls': [], 'not_run': [], 'artifacts': {},
            'scope': 'CPU-capable local feature chain; no mocked application responses or external-service success claims'}

    def request(self, method, path, *, reviewer=False, expected=None, **kwargs):
        if reviewer and self.review_token:
            kwargs['headers'] = {**kwargs.get('headers', {}), 'Authorization': 'Bearer ' + self.review_token}
        begin = time.monotonic()
        response = self.client.request(method, path, **kwargs)
        self.report['http_calls'].append({'method': method, 'path': path, 'status_code': response.status_code,
            'elapsed_ms': round((time.monotonic() - begin) * 1000, 3),
            'response_bytes': len(response.content), 'response_sha256': sha256(response.content)})
        valid = response.status_code == expected if expected is not None else 200 <= response.status_code < 300
        if not valid:
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {response.text[:600]}')
        return response

    def call(self, method, path, **kwargs):
        return self.request(method, path, **kwargs).json()

    def check(self, name, condition, **evidence):
        if not condition:
            self.report['checks'].append({'name': name, 'status': 'FAIL', 'evidence': evidence})
            raise AssertionError(name)
        self.report['checks'].append({'name': name, 'status': 'PASS', **({'evidence': evidence} if evidence else {})})
        print('PASS ' + name, file=sys.stderr, flush=True)

    def wait(self, job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            job = self.call('GET', '/api/jobs/' + job_id, timeout=max(.1, remaining))
            if job['status'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED', 'WAITING_APPROVAL', 'PAUSED'}:
                return job
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        raise TimeoutError(f'Job {job_id} did not complete within 30 seconds')

    def successful_job(self, job, check_name):
        result = self.wait(job['id'])
        self.check(check_name, result['status'] == 'SUCCEEDED', job_id=job['id'],
                   actual_status=result['status'], error=result.get('error'))
        return result

    def decide(self, approval):
        return self.call('POST', f'/api/approvals/{approval["id"]}/decide', reviewer=True,
            json={'decision': 'approve', 'expected_revision': approval['revision'],
                  'comment': 'Synthetic real HTTP acceptance'})

    def dataset(self, rows, name):
        return self.call('POST', f'/api/projects/{self.pid}/datasets', json={'name': name, 'rows': rows})

    def rows(self, asset):
        return self.call('GET', f'/api/assets/{asset["id"]}/rows?limit=100')['rows']

    def data_chain(self):
        rows = [{'x': i, 'y': 2 * i + 3, 'label': int(i >= 30)} for i in range(60)]
        uploaded = self.dataset(rows + rows[-3:], '合成训练记录（含重复）')
        clean = self.call('POST', f'/api/assets/{uploaded["id"]}/transform',
            json={'operations': [{'type': 'drop_duplicates'}], 'name': '合成训练记录 · 去重'})
        self.check('Data immutable transform', uploaded['row_count'] == 63 and clean['row_count'] == 60
                   and clean['parents'] == [uploaded['id']], source_id=uploaded['id'], version_id=clean['id'])
        exported = self.request('GET', f'/api/assets/{clean["id"]}/export')
        self.check('Data export bytes and SHA-256', exported.json() == rows and sha256(exported.content) == clean['sha256'])
        metadata = self.call('POST', f'/api/assets/{clean["id"]}/metadata-version', json={
            'name': '合成训练记录 · 元数据版本', 'tags': ['synthetic', 'acceptance'],
            'description': 'Only synthetic data for HTTP functional verification.',
            'version_label': 'http-v1', 'expected_revision': clean['revision']})
        self.check('Data metadata creates immutable version', metadata['sha256'] == clean['sha256']
                   and metadata['parents'] == [clean['id']] and metadata['id'] != clean['id'])
        catalog = self.call('GET', f'/api/projects/{self.pid}/data-catalog?q=http-v1')
        self.check('Data searchable version catalogue', any(item['id'] == metadata['id'] for item in catalog))
        parts = self.call('POST', f'/api/assets/{clean["id"]}/partition', json={'column': 'label'})
        partition_rows = [self.rows(part) for part in parts]
        self.check('Data exact category partition', len(parts) == 2 and all(len(part) == 30 for part in partition_rows)
                   and {tuple(sorted({row['label'] for row in part})) for part in partition_rows} == {(0,), (1,)})
        distribution = self.call('POST', f'/api/projects/{self.pid}/data-distributions', json={
            'left_id': parts[0]['id'], 'right_id': parts[1]['id'], 'columns': ['label', 'x']})
        self.check('Data distribution comparison', distribution['columns'][0]['total_variation'] == 1.0
                   and distribution['left_rows'] == distribution['right_rows'] == 30)
        # Archive an unreferenced partition before creating downstream jobs/models.
        archived = self.call('POST', f'/api/assets/{parts[0]["id"]}/archive', json={
            'archived': True, 'confirm': parts[0]['id'], 'expected_revision': 0, 'reason': 'Synthetic reversible check'})
        active = self.call('GET', f'/api/projects/{self.pid}/data-catalog')
        self.check('Data archive removes catalogue entry and retains bytes', archived['archived']
                   and not any(item['id'] == parts[0]['id'] for item in active)
                   and sha256(self.request('GET', f'/api/assets/{parts[0]["id"]}/export').content) == parts[0]['sha256'])
        restored = self.call('POST', f'/api/assets/{parts[0]["id"]}/archive', json={
            'archived': False, 'confirm': parts[0]['id'], 'expected_revision': archived['event_version'],
            'reason': 'Synthetic restore'})
        self.check('Data archive restoration', not restored['archived'] and restored['event_version'] == 2)
        self.report['artifacts'].update(training_dataset_id=clean['id'], metadata_dataset_id=metadata['id'])
        return clean

    def annotation_chain(self):
        source = self.dataset([{'text': '合成记录合格'}, {'text': '合成记录需复核'}], '合成标注原文')
        annotators = self.call('GET', f'/api/projects/{self.pid}/annotation-annotators')
        self.check('Annotation available identity list', any(item['user'] == self.me['user'] for item in annotators))
        task = self.call('POST', f'/api/projects/{self.pid}/annotation-tasks', json={
            'dataset_id': source['id'], 'name': '合成独立标注', 'task_type': 'text_classification',
            'text_column': 'text', 'labels': ['pass', 'review'], 'annotators': [self.me['user']], 'row_indexes': [0, 1]})
        source_rows = self.call('GET', f'/api/annotation-tasks/{task["id"]}/rows')
        self.check('Annotation selected source rows', source_rows['total'] == 2
                   and source_rows['rows'][1]['row_index'] == 1)
        submitted = self.call('POST', f'/api/annotation-tasks/{task["id"]}/submit', json={
            'expected_revision': task['revision'], 'items': [{'row_index': 0, 'value': 'pass'}, {'row_index': 1, 'value': 'review'}]})
        self.check('Annotation immutable submission', submitted['status'] == 'SUBMITTED'
                   and submitted['submitted_count'] == 1, task_id=task['id'])
        body = {'expected_revision': submitted['revision'], 'decision': 'accept', 'comment': 'Synthetic reviewed labels'}
        self.request('POST', f'/api/annotation-tasks/{task["id"]}/review', json=body, expected=403)
        self.check('Annotation self-review is denied', True)
        self.report['artifacts']['annotation_task_id'] = task['id']
        if not self.review_token:
            self.report['not_run'].append({'name': 'Independent annotation review and labelled export',
                'reason': 'ARD_REVIEW_TOKEN is absent; the submitter may not review their own labels.'})
            return
        accepted = self.call('POST', f'/api/annotation-tasks/{task["id"]}/review', reviewer=True, json=body)
        self.check('Annotation independent review', accepted['status'] == 'ACCEPTED'
                   and accepted['review']['creator'] != self.me['user'])
        exported = self.call('POST', f'/api/annotation-tasks/{task["id"]}/export', json={
            'expected_revision': accepted['revision'], 'name': '合成已审核标签', 'label_column': 'reviewed_label'})
        labels = self.rows(exported['dataset'])
        self.check('Annotation reviewed immutable dataset export', exported['dataset']['parents'] == [source['id']]
                   and [(item['_source_row'], item['reviewed_label']) for item in labels] == [(0, 'pass'), (1, 'review')])
        self.report['artifacts']['labelled_dataset_id'] = exported['dataset']['id']

    def knowledge_chain(self):
        raw = '# 合成操作规范 😀\n\n压力超过阈值时必须停止测试并复核。\n\n本文仅为自动验收合成资料。'.encode()
        document = self.call('POST', f'/api/knowledge/projects/{self.pid}/import',
            files={'file': ('synthetic-procedure.txt', raw, 'text/plain')},
            data={'name': '合成操作规范', 'strategy': 'paragraph', 'chunk_size': '60', 'overlap': '10'})
        self.check('Knowledge actual TXT import and source SHA', document['source_sha256'] == sha256(raw))
        self.check('Knowledge original byte export', self.request('GET',
            f'/api/knowledge/documents/{document["id"]}/export?format=source').content == raw)
        detail = self.call('GET', f'/api/knowledge/documents/{document["id"]}')
        chunks = self.call('GET', f'/api/knowledge/documents/{document["id"]}/chunks')
        self.check('Knowledge exact Unicode source offsets', bool(chunks['chunks']) and all(
            detail['text'][chunk['start']:chunk['end']] == chunk['text'] for chunk in chunks['chunks']))
        preview = self.call('GET', f'/api/knowledge/documents/{document["id"]}/chunks/0?context=8')
        self.check('Knowledge source context preview', preview['preview']['selected'] == chunks['chunks'][0]['text'])
        edited_text = '压力超过阈值时必须停止测试、隔离样件并复核。\n\n本文仅为修订后的合成资料。'
        version = self.call('POST', f'/api/knowledge/documents/{document["id"]}/versions', json={
            'name': '合成操作规范 · 修订', 'text': edited_text, 'expected_revision': document['revision']})
        self.check('Knowledge edit creates immutable version', version['parent_id'] == document['id']
                   and version['version'] == 2 and self.call('GET', f'/api/knowledge/documents/{document["id"]}')['text'] == raw.decode())
        matches = self.call('POST', f'/api/projects/{self.pid}/search', json={'query': '压力阈值复核', 'mode': 'keyword'})['matches']
        self.check('Knowledge matched current-version citations', bool(matches) and all(
            match['document_id'] == version['id'] and edited_text[match['start']:match['end']] == match['text'] for match in matches))
        no_hit = self.call('POST', f'/api/knowledge/projects/{self.pid}/answer', json={'query': 'zzzzzzzzzzzzzz'})
        self.check('Knowledge no-hit suppresses generation', no_hit['answer_type'] == 'no_evidence'
                   and no_hit['answer'] is None and no_hit['model_called'] is False)
        archived = self.call('POST', f'/api/knowledge/documents/{version["id"]}/archive', json={
            'archived': True, 'confirmed': True, 'expected_revision': 0})
        no_documents = self.call('POST', f'/api/projects/{self.pid}/search', json={'query': '压力'})
        self.check('Knowledge archive excludes all historical versions from retrieval', archived['archived'] and no_documents['matches'] == [])
        restored = self.call('POST', f'/api/knowledge/documents/{version["id"]}/archive', json={
            'archived': False, 'confirmed': True, 'expected_revision': archived['status_revision']})
        self.check('Knowledge reversible archive', not restored['archived'] and restored['source_retained'])
        self.report['artifacts'].update(document_id=document['id'], document_version_id=version['id'])
        return document, version

    def model_chain(self, training):
        approval = self.call('POST', f'/api/assets/{training["id"]}/transfer', json={})
        self.check('Model training data approval', self.decide(approval)['status'] == 'APPROVED')
        config = {'target': 'label', 'task': 'classification', 'features': ['x', 'y'], 'seed': 42}
        job = self.call('POST', f'/api/assets/{training["id"]}/train', json=config)
        done = self.successful_job(job, 'Actual CPU model training')
        model = self.call('GET', '/api/models/' + done['result']['model_id'])
        self.check('Model held-out training metrics', model['metrics']['accuracy'] >= .8
                   and model['train_rows'] + model['test_rows'] == 60, model_id=model['id'], accuracy=model['metrics']['accuracy'])
        package = self.call('GET', f'/api/models/{model["id"]}/package')
        copied = self.call('POST', f'/api/projects/{self.pid}/models/import', json={'package': package})
        exported_copy = self.call('GET', f'/api/models/{copied["id"]}/package')
        self.check('Model validated JSON package roundtrip', exported_copy['artifact'] == package['artifact']
                   and copied['provenance'] == 'imported_unverified_training', imported_model_id=copied['id'])
        baseline = self.call('POST', f'/api/projects/{self.pid}/model-baselines', json={
            'name': '合成可复用训练基线', 'dataset_id': training['id'], 'config': config})
        baseline_run = self.successful_job(self.call('POST', f'/api/model-baselines/{baseline["id"]}/run'),
                                           'Model reusable baseline execution')
        runs = self.call('GET', f'/api/model-baselines/{baseline["id"]}/runs')
        self.check('Model baseline retains immutable data/config/run', baseline['dataset_sha256'] == training['sha256']
                   and any(item['id'] == baseline_run['id'] for item in runs))
        evaluation_rows = [{'x': i + .25, 'y': 2 * (i + .25) + 3, 'label': int(i + .25 >= 30)} for i in range(0, 60, 3)]
        evaluation_data = self.dataset(evaluation_rows, '合成独立特征评估集')
        evaluation = self.call('POST', f'/api/models/{model["id"]}/evaluate', json={
            'dataset_id': evaluation_data['id'], 'name': '合成真实重评'})
        predictions = self.call('GET', f'/api/model-evaluations/{evaluation["id"]}/rows')
        self.check('Model actual reevaluation on selected dataset', evaluation['row_count'] == 20
                   and predictions['total'] == len(predictions['rows']) == 20
                   and evaluation['metrics']['accuracy'] >= .8, evaluation_id=evaluation['id'])
        comparison = self.call('POST', f'/api/projects/{self.pid}/model-comparisons', json={
            'dataset_id': evaluation_data['id'], 'model_ids': [model['id'], copied['id']], 'name': '合成模型对比'})
        self.check('Model comparison uses common rows', comparison['row_count'] == 20
                   and len(comparison['entries']) == 2
                   and comparison['entries'][0]['metrics'] == comparison['entries'][1]['metrics'])
        html = self.request('GET', f'/api/model-evaluations/{evaluation["id"]}/report')
        self.check('Model downloadable HTML evidence report', 'text/html' in html.headers.get('content-type', '')
                   and '<!doctype html>' in html.text.lower() and evaluation['model_sha256'] in html.text
                   and 'Content-Disposition' in html.headers)
        deployment = self.call('POST', f'/api/models/{model["id"]}/deploy', json={'expires_minutes': 5})
        result = self.call('POST', f'/api/deployments/{deployment["id"]}/predict',
                           json={'rows': [{'x': 1, 'y': 5}, {'x': 59, 'y': 121}]})
        self.check('Model actual deployment predictions', [str(item) for item in result['predictions']] == ['0', '1'])
        statistics = self.call('GET', f'/api/projects/{self.pid}/deployment-statistics')
        entry = next(item for item in statistics['entries'] if item['deployment_id'] == deployment['id'])
        self.check('Model recorded deployment statistics', statistics['source'] == 'recorded_prediction_requests'
                   and entry['requests'] == 1 and entry['successes'] == 1 and entry['failures'] == 0
                   and entry['mean_latency_ms'] >= 0)
        # End only the deployment created by this script.
        deployments = self.call('GET', f'/api/projects/{self.pid}/deployments')
        current = next(item for item in deployments if item['id'] == deployment['id'])
        stopped = self.call('POST', f'/api/deployments/{deployment["id"]}/stop', json={'expected_revision': current['revision']})
        self.check('Model deployment stop', stopped['status'] == 'STOPPED')
        self.report['artifacts'].update(model_id=model['id'], imported_model_id=copied['id'], baseline_id=baseline['id'],
            evaluation_id=evaluation['id'], comparison_id=comparison['id'], deployment_id=deployment['id'],
            evaluation_report_sha256=sha256(html.content))
        return model, evaluation

    def workflow_chain(self):
        nodes = [
            {'id': 'in', 'type': 'input'},
            {'id': 'vars', 'type': 'variable', 'params': {'values': {'batch': {'$path': 'batch'}, 'prefix': '合成样本'}}},
            {'id': 'gate', 'type': 'condition', 'params': {'path': 'enabled', 'op': 'eq', 'value': True}},
            {'id': 'loop', 'type': 'iterate', 'params': {'max_items': 2, 'operations': [{'type': 'strip'}],
                'template': '{{variables.prefix}} {{item.name}} / {{variables.batch}}', 'target': 'summary'}},
            {'id': 'no', 'type': 'template', 'params': {'template': '未启用 {{variables.batch}}'}},
            {'id': 'save', 'type': 'dataset_output', 'params': {'name': '合成流程持久产物'}},
            {'id': 'out', 'type': 'output'}]
        edges = [{'source': 'in', 'target': 'vars'}, {'source': 'vars', 'target': 'gate'},
            {'source': 'gate', 'target': 'loop', 'when': True}, {'source': 'gate', 'target': 'no', 'when': False},
            {'source': 'loop', 'target': 'save'}, {'source': 'save', 'target': 'out'}, {'source': 'no', 'target': 'out'}]
        definition = {'name': '合成条件与迭代流程', 'nodes': nodes, 'edges': edges}
        validation = self.call('POST', f'/api/projects/{self.pid}/workflows/validate', json=definition)
        self.check('Workflow new node definition validation', validation['valid'])
        flow = self.call('POST', f'/api/projects/{self.pid}/workflows', json=definition)
        package = self.call('GET', f'/api/workflows/{flow["id"]}/export')
        imported = self.call('POST', f'/api/projects/{self.pid}/workflows/import', json=package)
        self.check('Workflow package roundtrip', imported['nodes'] == nodes and imported['edges'] == edges)
        run = self.successful_job(self.call('POST', f'/api/workflows/{flow["id"]}/run', json={'input': {
            'enabled': True, 'batch': 'B01', 'rows': [{'name': ' A '}, {'name': ' B '}]}}), 'Workflow actual branch/iteration execution')
        output = run['result']['outputs']['out']
        expected = [{'name': 'A', 'summary': '合成样本 A / B01'}, {'name': 'B', 'summary': '合成样本 B / B01'}]
        persisted = self.call('GET', f'/api/assets/{output["asset_id"]}/rows')['rows']
        self.check('Workflow immutable dataset output and skipped branch', output['rows'] == persisted == expected
                   and run['state']['branches'] == {'gate': True} and run['result']['skipped_nodes'] == ['no'])
        alternate = self.successful_job(self.call('POST', f'/api/workflows/{flow["id"]}/run', json={
            'input': {'enabled': False, 'batch': 'B02'}}), 'Workflow alternate branch execution')
        self.check('Workflow safe template and bounded branch outputs', alternate['result']['outputs']['out']['text'] == '未启用 B02'
                   and set(alternate['result']['skipped_nodes']) == {'loop', 'save'})
        self.report['artifacts'].update(workflow_id=flow['id'], workflow_job_id=run['id'], workflow_dataset_id=output['asset_id'])

    def skills_chain(self, training):
        builtins = self.call('GET', '/api/skills/builtins')
        expected = {'data.profile', 'data.transform', 'data.split', 'data.aggregate',
                    'knowledge.search', 'knowledge.chunk', 'model.predict', 'text.template'}
        self.check('Eight useful builtin skill definitions', {item['operation'] for item in builtins} == expected
                   and all(item['input_schema'] for item in builtins))
        skill = self.call('POST', f'/api/projects/{self.pid}/skills', json={
            'name': '合成数据概览技能', 'operation': 'data.profile', 'defaults': {'asset_id': training['id']}})
        run = self.call('POST', f'/api/skills/{skill["id"]}/invoke', json={'arguments': {}})
        history = self.call('GET', f'/api/projects/{self.pid}/skill-runs')
        self.check('Skill actual invocation and persistent run history', run['status'] == 'SUCCEEDED'
                   and run['result']['row_count'] == 60 and any(item['id'] == run['id'] for item in history))
        self.report['artifacts'].update(skill_id=skill['id'], skill_run_id=run['id'])

    def operations_chain(self):
        settings = self.call('GET', f'/api/projects/{self.pid}/settings')
        project = settings['project']
        changed = self.call('PATCH', f'/api/projects/{self.pid}/settings', json={
            'expected_revision': project['revision'], 'name': project['name'],
            'description': 'Synthetic real HTTP feature acceptance, settings updated.',
            'quota_bytes': 256 * 1024 * 1024, 'max_jobs': 4,
            'telemetry_retention_count': 20, 'telemetry_retention_hours': 24})
        self.check('Operations project settings and revision', changed['project']['revision'] == project['revision'] + 1
                   and changed['project']['telemetry_retention_count'] == 20)
        # The deliberately permissive test rule evaluates a real collected metric;
        # the script never uploads fabricated host telemetry.
        rule = self.call('POST', f'/api/projects/{self.pid}/alert-rules', json={
            'name': '合成验收规则 active_jobs >= 0', 'metric': 'active_jobs', 'operator': 'gte', 'threshold': 0})
        sample = self.call('POST', f'/api/projects/{self.pid}/telemetry/sample', json={'evaluate_alerts': True})
        history = self.call('GET', f'/api/projects/{self.pid}/telemetry')
        self.check('Operations actual manual telemetry sample', sample['policy']['sampling'] == 'manual'
                   and sample['sample']['metrics']['active_jobs'] == 0
                   and any(item['id'] == sample['sample']['id'] for item in history['items']))
        alerts = self.call('GET', f'/api/projects/{self.pid}/alerts')['items']
        alert = next(item for item in alerts if item['rule_id'] == rule['id'])
        self.check('Operations threshold alert from actual sample', alert['status'] == 'OPEN'
                   and alert['sample_id'] == sample['sample']['id'] and alert['observed_value'] == 0)
        ack = self.call('POST', f'/api/projects/{self.pid}/alerts/{alert["id"]}/actions', json={
            'expected_revision': alert['revision'], 'action': 'acknowledge', 'comment': 'Synthetic acknowledgement'})
        resolved = self.call('POST', f'/api/projects/{self.pid}/alerts/{alert["id"]}/actions', json={
            'expected_revision': ack['revision'], 'action': 'resolve', 'comment': 'Synthetic resolution'})
        self.check('Operations alert acknowledge/resolve history', ack['status'] == 'ACKNOWLEDGED'
                   and resolved['status'] == 'RESOLVED'
                   and [item['action'] for item in resolved['events']] == ['open', 'acknowledge', 'resolve'])
        telemetry_csv = self.request('GET', f'/api/projects/{self.pid}/telemetry/export')
        telemetry_rows = list(csv.DictReader(StringIO(telemetry_csv.content.decode('utf-8-sig'))))
        self.check('Operations telemetry CSV export', any(item['id'] == sample['sample']['id'] for item in telemetry_rows))
        audit = self.request('GET', f'/api/projects/{self.pid}/audit/export')
        entries = list(csv.DictReader(StringIO(audit.content.decode('utf-8-sig'))))
        # Project records have no parent project_id. Their own create/update
        # audits are correctly included by entity_id; CSV represents NULL as ''.
        project_metadata = [item for item in entries if item['project_id'] == '' and item['entity_id'] == self.pid]
        self.check('Operations actual scoped audit CSV', bool(entries) and all(
            item['project_id'] == self.pid or (item['project_id'] == '' and item['entity_id'] == self.pid)
            for item in entries)
                   and any(item['action'] == 'settings:project' for item in entries)
                   and len(entries) == int(audit.headers['X-Exported-Count']),
                   scoped_rows=len(entries), project_metadata_rows=len(project_metadata))
        self.check('Operations local audit hash chain', self.call('GET', '/api/audit/verify')['valid'])
        self.report['artifacts'].update(telemetry_id=sample['sample']['id'], alert_id=alert['id'],
            audit_csv_sha256=sha256(audit.content), telemetry_csv_sha256=sha256(telemetry_csv.content))

    def package_chain(self):
        buffer = BytesIO()
        source = b'# Synthetic dependency manifest only; nothing is installed.\nhttpx==0.28.1\n'
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('requirements.txt', source)
            archive.writestr('README.md', 'Synthetic HTTP acceptance package. No executable code.')
        raw = buffer.getvalue()
        package = self.call('POST', f'/api/projects/{self.pid}/packages',
            files={'file': ('synthetic-dependencies.zip', raw, 'application/zip')},
            data={'name': 'synthetic-dependencies', 'version': '1.0.0', 'category': 'dependency'})
        manifest = self.call('GET', f'/api/packages/{package["id"]}/manifest')
        self.check('Dependency package actual upload and manifest', package['sha256'] == sha256(raw)
                   and package['file_count'] == 2
                   and next(item for item in manifest['files'] if item['path'] == 'requirements.txt')['sha256'] == sha256(source))
        self.check('Dependency package byte-preserving download', self.request('GET', f'/api/packages/{package["id"]}/download').content == raw)
        requirements = self.request('GET', f'/api/packages/{package["id"]}/requirements')
        records = [json.loads(line) for line in requirements.text.splitlines() if line.strip()]
        self.check('Dependency requirements discovery export', any(item['requirement'] == 'httpx==0.28.1' for item in records))
        self.report['artifacts']['dependency_package_id'] = package['id']
        return package

    def backup_chain(self, required_objects):
        response = self.request('POST', '/api/operations/backup', json={'confirm': 'BACKUP_ALL_PROJECTS'})
        self.check('Whole-platform backup transport digest', sha256(response.content) == response.headers['X-Backup-SHA256'])
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read('manifest.json'))
            self.check('Backup ZIP manifest format and membership', manifest['format'] == 'ai-rd-platform-backup'
                       and manifest['version'] == 1 and set(names) == {'manifest.json', *(entry['path'] for entry in manifest['files'])}
                       and len(names) == len(set(names)))
            for entry in manifest['files']:
                raw = archive.read(entry['path'])
                if len(raw) != entry['size_bytes'] or sha256(raw) != entry['sha256']:
                    raise AssertionError('Backup manifest mismatch: ' + entry['path'])
            self.check('Backup every data member SHA-256 and length', True, checked_files=len(manifest['files']))
            object_names = {name.removeprefix('objects/') for name in names if name.startswith('objects/')}
            self.check('Backup includes actual cross-module source and artifact blobs', set(required_objects) <= object_names
                       and len(object_names) == manifest['object_count'], required_objects=len(required_objects), object_count=len(object_names))
            with sqlite3.connect(':memory:') as database:
                database.deserialize(archive.read('platform.sqlite3'))
                database.execute('PRAGMA trusted_schema=OFF')
                database.execute('PRAGMA query_only=ON')
                integrity = database.execute('PRAGMA integrity_check').fetchone()[0]
                count = database.execute('SELECT count(*) FROM records').fetchone()[0]
                current = database.execute('SELECT kind FROM records WHERE id=?', (self.pid,)).fetchone()
                saved = database.execute('SELECT kind FROM records WHERE id=?', (self.report['artifacts']['workflow_dataset_id'],)).fetchone()
            self.check('Backup SQLite integrity and persisted feature output', integrity == 'ok'
                       and count == manifest['record_count'] and current == ('project',) and saved == ('dataset',)
                       and manifest['audit']['valid'])
            self.report['artifacts']['backup'] = {'sha256': sha256(response.content), 'size_bytes': len(response.content),
                'record_count': manifest['record_count'], 'object_count': manifest['object_count'], 'audit': manifest['audit']}

    def execute(self):
        try:
            health = self.call('GET', '/health')
            self.check('HTTP service health', health['status'] == 'ok' and bool(health['version']))
            self.report['version'] = health['version']
            self.me = self.call('GET', '/api/me')
            self.check('Global administrator acceptance identity', self.me['role'] == 'admin' and '*' in self.me['projects'])
            if self.review_token:
                reviewer = self.call('GET', '/api/me', reviewer=True)
                self.check('Distinct independent reviewer identity', reviewer['user'] != self.me['user']
                           and reviewer['role'] in {'admin', 'reviewer'} and '*' in reviewer['projects'])
            project = self.call('POST', '/api/projects', json={'name': '合成功能验收-' + uuid.uuid4().hex[:8],
                'description': 'Synthetic data only. Created by scripts/functional_smoke.py.', 'quota_bytes': 128 * 1024 * 1024})
            self.pid = project['id']
            self.report['project_id'] = self.pid
            training = self.data_chain()
            self.annotation_chain()
            document, version = self.knowledge_chain()
            model, evaluation = self.model_chain(training)
            self.workflow_chain()
            self.skills_chain(training)
            self.operations_chain()
            package = self.package_chain()
            self.backup_chain({training['sha256'], document['sha256'], document['source_sha256'],
                version['sha256'], version['source_sha256'], model['sha256'], evaluation['sha256'],
                package['sha256'], package['manifest_sha256']})
            self.report['coverage_complete'] = not self.report['not_run']
            self.report['status'] = 'PASS' if self.report['coverage_complete'] else 'PARTIAL'
            self.report['not_exercised'] = ['External chat/embedding services and semantic model quality',
                'Docker lifecycle or third-party MCP service availability',
                'Browser rendering, GPU execution, OCR and hardware acceptance',
                'Configured database extraction (covered by separate source tests)']
        except Exception as exc:
            self.report['status'] = 'FAIL'
            self.report['coverage_complete'] = False
            self.report['error'] = {'type': type(exc).__name__, 'message': str(exc)[:1000]}
        finally:
            self.report['duration_seconds'] = round(time.monotonic() - self.started, 3)
            self.report['completed_at'] = datetime.now(timezone.utc).isoformat()
            self.client.close()
        return self.report


def run(base_url):
    return Acceptance(base_url).execute()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = run(args.base_url)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if report['status'] == 'PASS' else 2 if report['status'] == 'PARTIAL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
