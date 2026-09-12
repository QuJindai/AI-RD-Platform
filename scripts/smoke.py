"""Real HTTP acceptance. Creates an explicitly synthetic, uniquely named project."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import httpx


def run(base_url):
    token, reviewer_token = os.environ.get('ARD_TOKEN', ''), os.environ.get('ARD_REVIEW_TOKEN', '')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    checks = []
    started = time.time()
    with httpx.Client(base_url=base_url.rstrip('/'), headers=headers, timeout=30, trust_env=False) as c:
        def call(method, path, **kwargs):
            r = c.request(method, path, **kwargs)
            if r.status_code >= 400:
                raise RuntimeError(f'{method} {path}: HTTP {r.status_code}: {r.text[:500]}')
            return r.json()

        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            checks.append({'name': name, 'status': 'PASS'})

        def wait(jid):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                j = call('GET', '/api/jobs/' + jid)
                if j['status'] in ('SUCCEEDED', 'FAILED', 'WAITING_APPROVAL', 'CANCELLED'):
                    return j
                time.sleep(.1)
            raise TimeoutError('job deadline exceeded')

        def decide(a):
            kw = {'headers': {'Authorization': 'Bearer ' + reviewer_token}} if reviewer_token else {}
            return call('POST', f'/api/approvals/{a["id"]}/decide', json={'decision': 'approve', 'expected_revision': a['revision']}, **kw)

        health = call('GET', '/health')
        check('HTTP health', health['version'] == '0.1.0')
        p = call('POST', '/api/projects', json={'name': '合成验收-' + uuid.uuid4().hex[:8]})
        pid = p['id']
        records = [f'{i},{2*i+3},{int(i>=30)}' for i in range(60)]
        csv = ('x,y,label\n' + '\n'.join(records + records[-3:]) + '\n').encode()
        d = call('POST', f'/api/projects/{pid}/import', files={'file': ('synthetic.csv', csv, 'text/csv')})
        check('CSV upload', d['row_count'] == 63)
        d2 = call('POST', f'/api/assets/{d["id"]}/transform', json={'operations': [{'type': 'drop_duplicates'}]})
        check('Immutable clean version', d2['row_count'] == 60 and d2['parents'] == [d['id']])
        exported = c.get(f'/api/assets/{d2["id"]}/export')
        check('Export SHA-256', hashlib.sha256(exported.content).hexdigest() == d2['sha256'])
        parts = call('POST', f'/api/assets/{d2["id"]}/split', json={'ratio': .7, 'seed': 42})
        merged = call('POST', f'/api/projects/{pid}/merge', json={'asset_ids': [x['id'] for x in parts], 'name': '合并验收'})
        check('Split and merge', merged['row_count'] == 60)
        a = call('POST', f'/api/assets/{d2["id"]}/transfer', json={})
        check('Transfer approval', decide(a)['status'] == 'APPROVED')
        j = call('POST', f'/api/assets/{d2["id"]}/train', json={'target': 'label', 'task': 'classification', 'features': ['x', 'y']})
        done = wait(j['id'])
        check('Real CPU training', done['status'] == 'SUCCEEDED')
        model = call('GET', '/api/models/' + done['result']['model_id'])
        check('Held-out evaluation', model['metrics']['accuracy'] >= .8 and model['train_rows'] + model['test_rows'] == 60)
        dep = call('POST', f'/api/models/{model["id"]}/deploy', json={'expires_minutes': 30})
        predictions = call('POST', f'/api/deployments/{dep["id"]}/predict', json={'rows': [{'x': 1, 'y': 5}, {'x': 59, 'y': 121}]})
        check('Deployed predictions', [str(x) for x in predictions['predictions']] == ['0', '1'])
        doc = call('POST', f'/api/projects/{pid}/documents', json={'name': '合成北斗检测说明', 'text': '北斗定位检测需要核对天线连接、信号质量及设备标定状态。本文为工程验收合成样例。'})
        search = call('POST', f'/api/projects/{pid}/search', json={'query': '北斗定位天线'})
        check('Knowledge source retrieval', search['matches'][0]['document_id'] == doc['id'])
        nodes = [{'id': 'input', 'type': 'input'}, {'id': 'search', 'type': 'retrieve', 'params': {'query': '北斗定位'}},
                 {'id': 'review', 'type': 'human', 'params': {'message': '核对检索证据'}}, {'id': 'output', 'type': 'output'}]
        wf = call('POST', f'/api/projects/{pid}/workflows', json={'name': '合成知识审核流', 'nodes': nodes,
                       'edges': [{'source': x['id'], 'target': y['id']} for x, y in zip(nodes, nodes[1:])]})
        j = call('POST', f'/api/workflows/{wf["id"]}/run', json={})
        waiting = wait(j['id'])
        check('Human checkpoint', waiting['status'] == 'WAITING_APPROVAL')
        a = next(a for a in call('GET', f'/api/projects/{pid}/approvals') if a.get('job_id') == j['id'])
        decide(a)
        check('Workflow resume', wait(j['id'])['status'] == 'SUCCEEDED')
        check('Audit chain', call('GET', '/api/audit/verify')['valid'])
        return {'status': 'PASS', 'version': health['version'], 'checks': checks,
                'project_id': pid, 'duration_seconds': round(time.time() - started, 3),
                'data_classification': 'synthetic only', 'transport': 'real HTTP'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', required=True)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    report = run(args.base_url)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
