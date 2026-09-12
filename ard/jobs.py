"""Bounded background execution with persisted state and cooperative cancellation."""
from concurrent.futures import ThreadPoolExecutor
import threading

from ard.store import now


TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}
ACTIVE = {'QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'PAUSED'}


class Cancelled(Exception):
    pass


class Paused(Exception):
    pass


class Waiting(Exception):
    def __init__(self, result):
        self.result = result


class Jobs:
    def __init__(self, store, runner, workers=2):
        self.store, self.runner = store, runner
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='ard-job')
        self.guards = {}
        self.lock = threading.RLock()
        for job in store.list('job'):
            if job['status'] in ('QUEUED', 'RUNNING'):
                if job.get('pause_requested') and job.get('payload', {}).get('kind') == 'workflow':
                    store.update(job['id'], {'status': 'PAUSED', 'paused_at': now()}, 'system')
                else:
                    store.update(job['id'], {'status': 'INTERRUPTED', 'error': '服务重启中断了此任务；工作流可从检查点重试', 'finished_at': now()}, 'system')
        for kind in ('skill_run', 'agent_run'):
            for run in store.list(kind):
                if run['status'] == 'RUNNING':
                    store.update(run['id'], {'status': 'INTERRUPTED', 'error': '服务重启中断调用，请检查已有结果后重新提交', 'finished_at': now()}, 'system')

    def submit(self, pid, payload, actor, max_active=4):
        def quota(db):
            import json
            # Resolve the current project limit in the same transaction as insertion.
            current_project = db.execute("SELECT data FROM records WHERE id=? AND kind='project'", (pid,)).fetchone()
            if current_project is None:
                raise KeyError('project not found')
            limit = json.loads(current_project[0]).get('max_jobs', max_active)
            jobs = db.execute("SELECT data FROM records WHERE kind='job' AND project_id=?", (pid,))
            active = sum(json.loads(r[0])['status'] in ACTIVE for r in jobs)
            if active >= limit:
                raise ValueError('项目活动任务配额已用完')
        j = self.store.create('job', pid, {'status': 'QUEUED', 'payload': payload, 'creator': actor,
                              'progress': 0, 'logs': [], 'result': None, 'error': None,
                              'cancel_requested': False, 'pause_requested': False, 'retry_count': 0}, actor, check=quota)
        self.enqueue(j['id'])
        return j

    def enqueue(self, job_id):
        with self.lock:
            guard = self.guards.setdefault(job_id, threading.RLock())
        self.executor.submit(self._run, job_id, guard)

    def check(self, job_id, allow_pause=True):
        job = self.store.get(job_id, 'job')
        if job.get('cancel_requested') or job['status'] == 'CANCELLED':
            raise Cancelled()
        if allow_pause and (job.get('pause_requested') or job['status'] == 'PAUSED'):
            raise Paused()

    def progress(self, job_id, value, message):
        with self.store.lock:
            self.check(job_id)
            job = self.store.get(job_id)
            self.store.update(job_id, {'progress': value, 'logs': (job['logs'] + [{'at': now(), 'message': message}])[-200:]}, 'worker')

    def _run(self, job_id, guard):
        with guard:
            try:
                with self.store.lock:
                    job = self.store.get(job_id)
                    if job['status'] != 'QUEUED':
                        return
                    self.check(job_id)
                    self.store.update(job_id, {'status': 'RUNNING', 'started_at': now()}, 'worker')
                result = self.runner(job_id)
                with self.store.lock:
                    self.check(job_id)
                    self.store.update(job_id, {'status': 'SUCCEEDED', 'result': result, 'progress': 100, 'finished_at': now()}, 'worker')
            except Waiting as w:
                with self.store.lock:
                    if self.store.get(job_id)['status'] == 'RUNNING':
                        self.store.update(job_id, {'status': 'WAITING_APPROVAL', 'result': w.result}, 'worker')
            except Cancelled:
                self.store.update(job_id, {'status': 'CANCELLED', 'finished_at': now()}, 'worker')
            except Paused:
                with self.store.lock:
                    if self.store.get(job_id)['status'] not in TERMINAL:
                        self.store.update(job_id, {'status': 'PAUSED', 'paused_at': now()}, 'worker')
            except Exception as exc:
                with self.store.lock:
                    if self.store.get(job_id)['status'] != 'CANCELLED':
                        safe = str(exc)[:500] if isinstance(exc, (ValueError, KeyError)) else '执行失败，请检查输入或服务配置'
                        self.store.update(job_id, {'status': 'FAILED', 'error': safe, 'finished_at': now()}, 'worker')

    def cancel(self, job_id, actor, revision):
        with self.store.lock:
            job = self.store.get(job_id, 'job')
            if job['status'] in TERMINAL:
                raise ValueError('terminal job cannot be cancelled')
            return self.store.update(job_id, {'cancel_requested': True, 'status': 'CANCELLED', 'finished_at': now()}, actor, revision)

    def pause(self, job_id, actor, revision):
        """A running CPU/network call reaches PAUSED only at its next checkpoint."""
        with self.store.transaction():
            job = self.store.get(job_id, 'job')
            if job.get('payload', {}).get('kind') != 'workflow':
                raise ValueError('暂停目前只支持有检查点的工作流')
            if job['status'] not in ('QUEUED', 'RUNNING') or job.get('pause_requested'):
                raise ValueError('此任务当前不能请求暂停')
            changes = {'pause_requested': True}
            if job['status'] == 'QUEUED':
                changes.update(status='PAUSED', paused_at=now())
            return self.store.update(job_id, changes, actor, revision)

    def resume(self, job_id, actor, revision, max_active=4, retry=False):
        with self.store.transaction():
            job = self.store.get(job_id, 'job')
            if job.get('payload', {}).get('kind') != 'workflow':
                raise ValueError('恢复目前只支持工作流')
            allowed = ('FAILED', 'INTERRUPTED') if retry else ('PAUSED',)
            if job['status'] not in allowed:
                raise ValueError('任务状态不允许重试' if retry else '任务尚未暂停')
            approvals = [a for a in self.store.list('approval', job['project_id']) if a.get('job_id') == job_id]
            if any(a['status'] in ('PENDING', 'REJECTED') for a in approvals):
                raise ValueError('存在待审批或已驳回节点，不能通过重试绕过审批')
            active = sum(j['id'] != job_id and j['status'] in ACTIVE for j in self.store.list('job', job['project_id']))
            if active >= self.store.get(job['project_id'], 'project').get('max_jobs', max_active):
                raise ValueError('项目活动任务配额已用完')
            changes = {'status': 'QUEUED', 'pause_requested': False, 'error': None, 'result': None, 'finished_at': None,
                       'retry_count': job.get('retry_count', 0) + int(retry),
                       'logs': (job.get('logs', []) + [{'at': now(), 'message': '从已提交检查点重试' if retry else '从已提交检查点继续'}])[-200:]}
            result = self.store.update(job_id, changes, actor, revision)
        self.enqueue(job_id)
        return result

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
