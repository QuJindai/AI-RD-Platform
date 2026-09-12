"""Application operations shared by the HTTP API and workflow runner."""
from datetime import datetime, timedelta, timezone
import json

from ard.engines.data import profile, transform, split_rows, parse_table, parse_archive
from ard.engines.models import train_model, predict_model
from ard.jobs import Jobs
from ard.store import Store, encode, now


class Service:
    def __init__(self, root):
        self.store = Store(root)
        self.jobs = Jobs(self.store, self.run_job)

    def record(self, entity_id, kind, identity):
        record = self.store.get(entity_id, kind)
        identity.allow_project(record['id'] if kind == 'project' else record['project_id'])
        return record

    def project(self, pid, identity):
        return self.record(pid, 'project', identity)

    def list(self, kind, pid, identity):
        self.project(pid, identity)
        return self.store.list(kind, pid)

    def create_project(self, name, description, quota_bytes, max_jobs, identity):
        identity.allow_write()
        if '*' not in identity.projects:
            raise ValueError('创建项目需要全局项目权限')
        return self.store.create('project', None, {'name': name, 'description': description,
               'quota_bytes': quota_bytes, 'max_jobs': max_jobs, 'creator': identity.user}, identity.user)

    def _quota(self, pid, extra):
        project = self.store.get(pid, 'project')
        used = sum(r.get('size_bytes', 0) for r in self.store.list(project_id=pid))
        if used + extra > project['quota_bytes']:
            raise ValueError('项目存储配额不足')

    def add_dataset(self, pid, name, rows, tags, identity, parents=None):
        identity.allow_write()
        self.project(pid, identity)
        if not rows:
            raise ValueError('数据集不能为空')
        stats = profile(rows)
        raw = encode(rows)
        if len(raw) > 100 * 1024 * 1024:
            raise ValueError('数据集超过100MiB')
        parents = parents or []
        with self.store.transaction():
            from ard.features.data import require_active
            for parent in parents:
                source = require_active(self, self.record(parent, 'dataset', identity))
                if source['project_id'] != pid:
                    raise ValueError('不能跨项目混合数据版本')
            self._quota(pid, len(raw))
            digest = self.store.blob(raw)
            return self.store.create('dataset', pid, {'name': name, 'tags': tags, 'parents': parents,
                     'row_count': len(rows), 'columns': list(dict.fromkeys(k for row in rows for k in row)),
                     'stats': stats, 'sha256': digest, 'size_bytes': len(raw), 'creator': identity.user,
                     'format': 'json', 'sealed': True}, identity.user)

    def rows(self, asset_id):
        return json.loads(self.store.read_blob(self.store.get(asset_id, 'dataset')['sha256']))

    def import_data(self, pid, name, filename, raw, identity):
        rows = parse_archive(raw) if filename.lower().endswith('.zip') else parse_table(filename, raw)
        return self.add_dataset(pid, name or filename, rows, [], identity)

    def transform_data(self, asset_id, operations, name, identity):
        asset = self.record(asset_id, 'dataset', identity)
        rows = transform(self.rows(asset_id), operations)
        return self.add_dataset(asset['project_id'], name or asset['name'] + ' · 清洗', rows, asset['tags'], identity, [asset_id])

    def split_data(self, asset_id, ratio, seed, identity):
        identity.allow_write()
        asset = self.record(asset_id, 'dataset', identity)
        parts = split_rows(self.rows(asset_id), ratio, seed)
        for rows in parts:
            if not rows:
                raise ValueError('拆分两侧都必须包含样本')
            profile(rows)
        with self.store.transaction():
            self._quota(asset['project_id'], sum(len(encode(x)) for x in parts))
            return [self.add_dataset(asset['project_id'], asset['name'] + suffix, rows, asset['tags'], identity, [asset_id])
                    for rows, suffix in zip(parts, (' · A', ' · B'))]

    def merge_data(self, pid, asset_ids, name, identity):
        self.project(pid, identity)
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError('合并来源不能重复')
        rows = []
        for asset_id in asset_ids:
            asset = self.record(asset_id, 'dataset', identity)
            if asset['project_id'] != pid:
                raise ValueError('不能跨项目混合数据版本')
            rows.extend(self.rows(asset_id))
        return self.add_dataset(pid, name, rows, [], identity, asset_ids)

    def transfer(self, asset_id, target_pid, identity):
        identity.allow_write()
        from ard.features.data import require_active
        with self.store.transaction():
            asset = require_active(self, self.record(asset_id, 'dataset', identity))
            target_pid = target_pid or asset['project_id']
            self.project(target_pid, identity)
            return self.store.create('approval', asset['project_id'], {'approval_type': 'dataset',
                  'asset_id': asset_id, 'target_project_id': target_pid, 'status': 'PENDING',
                  'creator': identity.user, 'name': asset['name'] + ' · 转入模型库'}, identity.user)

    def decide(self, approval_id, decision, revision, comment, identity):
        approval = self.record(approval_id, 'approval', identity)
        identity.allow_approve(approval['creator'])
        with self.store.transaction():
            current = self.store.get(approval_id)
            if current['revision'] != revision or current['status'] != 'PENDING':
                raise ValueError('revision conflict; approval already processed')
            changes = {'status': 'APPROVED' if decision == 'approve' else 'REJECTED',
                       'reviewer': identity.user, 'comment': comment, 'decided_at': now()}
            if approval['approval_type'] == 'dataset' and decision == 'approve':
                pid = approval['target_project_id']
                self.project(pid, identity)
                if pid != approval['project_id']:
                    source = self.store.get(approval['asset_id'])
                    # Reviewed transfer is a new immutable version in the receiving project.
                    raw = self.store.read_blob(source['sha256'])
                    self._quota(pid, len(raw))
                    clone = {k: source[k] for k in ('name','tags','row_count','columns','stats','sha256','size_bytes','format','sealed')}
                    clone.update(parents=[], transferred_from=source['id'], creator=identity.user)
                    copied = self.store.create('dataset', pid, clone, identity.user)
                    changes['approved_asset_id'] = copied['id']
                else:
                    changes['approved_asset_id'] = approval['asset_id']
            if approval['approval_type'] == 'workflow':
                job = self.store.get(approval['job_id'], 'job')
                if job['status'] != 'WAITING_APPROVAL':
                    raise ValueError('任务当前不在等待审批状态')
                if decision == 'approve':
                    state = dict(job.get('state', {}))
                    state['approved_nodes'] = state.get('approved_nodes', []) + [approval['node_id']]
                    self.store.update(job['id'], {'state': state, 'status': 'QUEUED'}, identity.user)
                else:
                    self.store.update(job['id'], {'status': 'FAILED', 'error': '人工审核驳回', 'finished_at': now()}, identity.user)
            result = self.store.update(approval_id, changes, identity.user, revision)
        if approval['approval_type'] == 'workflow' and decision == 'approve':
            self.jobs.enqueue(approval['job_id'])
        return result

    def approved(self, asset_id):
        return any(a['status'] == 'APPROVED' and a.get('approved_asset_id') == asset_id for a in self.store.list('approval'))

    def train(self, asset_id, config, identity):
        identity.allow_write()
        from ard.features.data import require_active
        with self.store.transaction():
            asset = require_active(self, self.record(asset_id, 'dataset', identity))
            if not self.approved(asset_id):
                raise ValueError('数据版本尚未获批转库')
            project = self.project(asset['project_id'], identity)
            return self.jobs.submit(asset['project_id'], {'kind': 'train', 'asset_id': asset_id, **config}, identity.user, project['max_jobs'])

    def run_job(self, job_id):
        job = self.store.get(job_id, 'job')
        payload = job['payload']
        if payload['kind'] == 'workflow':
            from ard.workflows import execute
            return execute(self, job_id)
        self.jobs.progress(job_id, 10, '读取已审批数据版本，划分训练集与测试集')
        result = train_model(self.rows(payload['asset_id']), target=payload['target'], task=payload.get('task', 'classification'),
                             features=payload.get('features'), test_fraction=payload.get('test_fraction', .25), seed=payload.get('seed', 42))
        self.jobs.progress(job_id, 90, '评估完成，封存模型参数与指标')
        with self.store.lock:
            self.jobs.check(job_id)
            model = self.save_model(job['project_id'], payload['asset_id'], payload['target'], payload['task'], result, job['creator'])
        return {'model_id': model['id'], 'metrics': model['metrics']}

    def save_model(self, pid, asset_id, target, task, result, actor):
        raw = encode(result['artifact'])
        self._quota(pid, len(raw))
        digest = self.store.blob(raw)
        source = self.store.get(asset_id, 'dataset')
        return self.store.create('model', pid, {'name': source['name'] + ' · ' + target, 'dataset_id': asset_id,
             'task': task, 'target': target, 'features': result['features'], 'metrics': result['metrics'],
             'train_rows': result['train_rows'], 'test_rows': result['test_rows'], 'sha256': digest,
             'size_bytes': len(raw), 'creator': actor, 'engine': 'scikit-learn', 'artifact_format': 'json'}, actor)

    def predict(self, model_id, rows):
        model = self.store.get(model_id, 'model')
        artifact = json.loads(self.store.read_blob(model['sha256']))
        return predict_model(artifact, rows)

    def deploy(self, model_id, minutes, identity):
        identity.allow_write()
        model = self.record(model_id, 'model', identity)
        return self.store.create('deployment', model['project_id'], {'model_id': model_id, 'name': model['name'],
             'status': 'ACTIVE', 'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
             'requests': 0, 'failures': 0, 'total_latency_ms': 0, 'creator': identity.user}, identity.user)

    def close(self):
        self.jobs.close()
