"""Bounded background execution with persisted state and cooperative cancellation."""
from concurrent.futures import ThreadPoolExecutor
import threading

from ard.store import now


TERMINAL = {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}


class Cancelled(Exception):
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
                store.update(job['id'], {'status': 'INTERRUPTED', 'error': '服务重启中断了此任务，请重新提交', 'finished_at': now()}, 'system')

    def submit(self, pid, payload, actor, max_active=4):
        def quota(db):
            import json
            jobs = db.execute("SELECT data FROM records WHERE kind='job' AND project_id=?", (pid,))
            active = sum(json.loads(r[0])['status'] in ('QUEUED', 'RUNNING', 'WAITING_APPROVAL') for r in jobs)
            if active >= max_active:
                raise ValueError('项目活动任务配额已用完')
        j = self.store.create('job', pid, {'status': 'QUEUED', 'payload': payload, 'creator': actor,
                              'progress': 0, 'logs': [], 'result': None, 'error': None,
                              'cancel_requested': False}, actor, check=quota)
        self.enqueue(j['id'])
        return j

    def enqueue(self, job_id):
        with self.lock:
            guard = self.guards.setdefault(job_id, threading.RLock())
        self.executor.submit(self._run, job_id, guard)

    def check(self, job_id):
        job = self.store.get(job_id, 'job')
        if job['cancel_requested'] or job['status'] == 'CANCELLED':
            raise Cancelled()

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

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
