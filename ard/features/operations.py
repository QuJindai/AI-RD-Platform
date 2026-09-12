"""Project administration, explicit host telemetry and local alert handling."""
from datetime import datetime, timedelta, timezone
import csv
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import Field
from starlette.background import BackgroundTask

from ard.features.common import Input, Svc, User, admin, install_once
from ard.jobs import TERMINAL
from ard.store import now


router = APIRouter(tags=['operations'])
DEFAULT_RETENTION_COUNT = 120
DEFAULT_RETENTION_HOURS = 168
MIN_SAMPLE_INTERVAL_SECONDS = 1
MAX_RULES = 50
METRICS = ('cpu_percent', 'memory_percent', 'disk_percent',
           'project_storage_percent', 'active_jobs')


class SettingsInput(Input):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100, pattern=r'.*\S.*')
    description: str = Field(default='', max_length=2000)
    quota_bytes: int = Field(ge=1024, le=10737418240)
    max_jobs: int = Field(ge=1, le=20)
    telemetry_retention_count: int = Field(default=DEFAULT_RETENTION_COUNT, ge=10, le=2000)
    telemetry_retention_hours: int = Field(default=DEFAULT_RETENTION_HOURS, ge=1, le=8760)


class SampleInput(Input):
    evaluate_alerts: bool = True


class RuleInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r'.*\S.*')
    metric: Literal['cpu_percent', 'memory_percent', 'disk_percent', 'project_storage_percent', 'active_jobs']
    operator: Literal['gt', 'gte', 'lt', 'lte'] = 'gte'
    threshold: float = Field(ge=0, le=1000000)
    enabled: bool = True


class RuleUpdate(RuleInput):
    expected_revision: int = Field(ge=1)


class EvaluateInput(Input):
    sample_id: str = Field(min_length=1, max_length=64)


class AlertAction(Input):
    expected_revision: int = Field(ge=1)
    action: Literal['acknowledge', 'resolve']
    comment: str = Field(default='', max_length=1000)


class BackupInput(Input):
    confirm: Literal['BACKUP_ALL_PROJECTS']


def _usage(s, pid):
    records = s.store.list(project_id=pid)
    return {'stored_bytes': sum(r.get('size_bytes', 0) for r in records),
            'active_jobs': sum(r['kind'] == 'job' and r.get('status') not in TERMINAL for r in records)}


def _policy(project):
    return {'retention_count': project.get('telemetry_retention_count', DEFAULT_RETENTION_COUNT),
            'retention_hours': project.get('telemetry_retention_hours', DEFAULT_RETENTION_HOURS),
            'sampling': 'manual', 'minimum_interval_seconds': MIN_SAMPLE_INTERVAL_SECONDS,
            'cpu_window_seconds': .1,
            'cleanup': 'age hidden on read; persisted rows pruned on sampling or settings update'}


def _settings(s, project):
    return {'project': project, 'usage': _usage(s, project['id']), 'telemetry': _policy(project)}


def _prune(s, project, db):
    policy = _policy(project)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=policy['retention_hours'])).isoformat()
    rows = db.execute("SELECT id,created_at FROM records WHERE kind='telemetry' AND project_id=? "
                      'ORDER BY created_at DESC,id DESC', (project['id'],)).fetchall()
    removed = [r['id'] for i, r in enumerate(rows)
               if i >= policy['retention_count'] or r['created_at'] < cutoff]
    db.executemany('DELETE FROM records WHERE id=?', [(i,) for i in removed])
    return len(removed)


@router.get('/api/projects/{pid}/settings')
def settings(pid: str, s: Svc, user: User):
    with s.store.transaction():
        return _settings(s, s.project(pid, user))


@router.patch('/api/projects/{pid}/settings')
def update_settings(pid: str, body: SettingsInput, s: Svc, user: User):
    admin(user)
    with s.store.transaction() as db:
        project = s.project(pid, user)
        if project['revision'] != body.expected_revision:
            raise HTTPException(409, 'revision conflict; 请重新载入项目设置')
        used = _usage(s, pid)
        if body.quota_bytes < used['stored_bytes']:
            raise HTTPException(409, f"存储配额不能低于已使用的 {used['stored_bytes']} 字节")
        if body.max_jobs < used['active_jobs']:
            raise HTTPException(409, f"任务配额不能低于 {used['active_jobs']} 个活动任务")
        project = s.store.update(pid, body.model_dump(exclude={'expected_revision'}), user.user, body.expected_revision)
        removed = _prune(s, project, db)
        s.store._audit(db, user.user, 'settings:project', pid, pid,
                       {'revision': project['revision'], 'telemetry_pruned': removed})
        return _settings(s, project)


def _timestamp(value, name):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise HTTPException(422, f'{name} 必须是含时区的 ISO 8601 时间') from None
    if parsed.tzinfo is None:
        raise HTTPException(422, f'{name} 必须包含时区')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _bounds(since, until):
    since, until = _timestamp(since, 'since'), _timestamp(until, 'until')
    if since and until and since > until:
        raise HTTPException(422, '起始时间不能晚于结束时间')
    return since, until


def _audits(s, pid, actor, action, since, until, limit, offset):
    since, until = _bounds(since, until)
    where = ['(project_id=? OR (project_id IS NULL AND entity_id=?))']
    args = [pid, pid]
    for field, value in (('actor', actor), ('action', action)):
        if value is not None:
            where.append(field + '=?'); args.append(value)
    if since:
        where.append('at>=?'); args.append(since)
    if until:
        where.append('at<=?'); args.append(until)
    clause = ' AND '.join(where)
    with s.store.connect() as db:
        total = db.execute('SELECT count(*) FROM audit WHERE ' + clause, args).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT * FROM audit WHERE ' + clause +
                                          ' ORDER BY seq DESC LIMIT ? OFFSET ?', (*args, limit, offset))]
    for row in rows:
        row['detail'] = json.loads(row['detail'])
    return {'items': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/api/projects/{pid}/audit')
def audit_history(pid: str, s: Svc, user: User,
                  actor: str | None = Query(default=None, max_length=200),
                  action: str | None = Query(default=None, max_length=200),
                  since: str | None = Query(default=None, max_length=80),
                  until: str | None = Query(default=None, max_length=80),
                  limit: int = Query(default=200, ge=1, le=1000),
                  offset: int = Query(default=0, ge=0, le=10000000)):
    s.project(pid, user)
    return _audits(s, pid, actor, action, since, until, limit, offset)


def _csv(headers, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        values = []
        for key in headers:
            value = row.get(key)
            cell = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else str(value if value is not None else '')
            # Spreadsheet applications also interpret formulas after leading whitespace.
            if cell.startswith(('\t', '\r', '\n')) or cell.lstrip().startswith(('=', '+', '-', '@')):
                cell = "'" + cell
            values.append(cell)
        writer.writerow(values)
    return ('\ufeff' + output.getvalue()).encode('utf-8')


@router.get('/api/projects/{pid}/audit/export')
def audit_export(pid: str, s: Svc, user: User,
                 actor: str | None = Query(default=None, max_length=200),
                 action: str | None = Query(default=None, max_length=200),
                 since: str | None = Query(default=None, max_length=80),
                 until: str | None = Query(default=None, max_length=80),
                 limit: int = Query(default=10000, ge=1, le=10000),
                 offset: int = Query(default=0, ge=0, le=10000000)):
    project = s.project(pid, user)
    result = _audits(s, pid, actor, action, since, until, limit, offset)
    columns = ('seq', 'at', 'actor', 'action', 'entity_id', 'project_id', 'detail', 'previous_hash', 'hash')
    return Response(_csv(columns, result['items']), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="audit-{project["id"]}.csv"',
        'X-Total-Count': str(result['total']), 'X-Exported-Count': str(len(result['items']))})


def _cpu_ticks():
    fields = Path('/proc/stat').read_text().splitlines()[0].split()
    if fields[0] != 'cpu' or len(fields) < 5:
        raise ValueError('CPU counters unavailable')
    # guest/guest_nice are already counted in user/nice.
    ticks = [int(x) for x in fields[1:9]]
    return sum(ticks), ticks[3] + (ticks[4] if len(ticks) > 4 else 0)


def collect_host_metrics(root):
    """Read the host; unsupported counters remain null with a visible reason."""
    metrics = {'cpu_percent': None, 'memory_percent': None, 'disk_percent': None}
    unavailable = {}
    try:
        total0, idle0 = _cpu_ticks()
        time.sleep(.1)
        total1, idle1 = _cpu_ticks()
        if total1 <= total0:
            raise ValueError('CPU counters did not advance')
        metrics['cpu_percent'] = round(max(0, min(100, 100 * (1 - (idle1 - idle0) / (total1 - total0)))), 3)
    except (OSError, ValueError, IndexError):
        unavailable['cpu_percent'] = '当前宿主机没有可读取的 Linux /proc/stat 计数器'
    try:
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            memory[key] = int(value.strip().split()[0]) * 1024
        total, available = memory['MemTotal'], memory['MemAvailable']
        if total <= 0 or not 0 <= available <= total:
            raise ValueError('invalid memory counters')
        metrics.update(memory_percent=round(100 * (1 - available / total), 3),
                       memory_total_bytes=total, memory_available_bytes=available)
    except (OSError, ValueError, KeyError, IndexError):
        unavailable['memory_percent'] = '当前宿主机没有可读取的 Linux MemAvailable 计数器'
    try:
        disk = shutil.disk_usage(root)
        metrics.update(disk_percent=round(100 * disk.used / disk.total, 3),
                       disk_total_bytes=disk.total, disk_free_bytes=disk.free)
    except (OSError, ZeroDivisionError):
        unavailable['disk_percent'] = '无法读取数据目录所在文件系统的容量'
    metrics['cpu_count'] = os.cpu_count()
    return {'metrics': metrics, 'unavailable': unavailable,
            'source': 'host:/proc/stat,/proc/meminfo; filesystem:shutil.disk_usage',
            'scope': 'host counters, not project-isolated CPU/memory; no GPU collector',
            'cpu_window_seconds': .1}


def _recent_samples(s, project):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_policy(project)['retention_hours'])).isoformat()
    return sorted((r for r in s.store.list('telemetry', project['id']) if r['created_at'] >= cutoff),
                  key=lambda r: (r['created_at'], r['id']), reverse=True)[:_policy(project)['retention_count']]


def _change_alert(s, alert, action, actor, comment='', expected_revision=None):
    status = 'ACKNOWLEDGED' if action == 'acknowledge' else 'RESOLVED'
    event = {'at': now(), 'actor': actor, 'action': action, 'comment': comment}
    return s.store.update(alert['id'], {'status': status, 'events': alert['events'] + [event],
                                      'resolved_at': event['at'] if status == 'RESOLVED' else None},
                          actor, expected_revision if expected_revision is not None else alert['revision'])


def _evaluate(s, pid, sample, user):
    rules = s.list('alert_rule', pid, user)
    states = {r['rule_id']: r for r in s.list('alert_rule_state', pid, user)}
    alerts = s.list('alert', pid, user)
    output = []
    for rule in rules:
        if not rule['enabled']:
            continue
        state = states.get(rule['id'])
        if state and state['rule_revision'] == rule['revision'] and sample['created_at'] <= state['sampled_at']:
            output.append({'rule_id': rule['id'], 'outcome': 'already_evaluated_or_older'}); continue
        value = sample['metrics'].get(rule['metric'])
        if value is None:
            output.append({'rule_id': rule['id'], 'outcome': 'metric_unavailable'}); continue
        threshold = rule['threshold']
        condition = {'gt': value > threshold, 'gte': value >= threshold,
                     'lt': value < threshold, 'lte': value <= threshold}[rule['operator']]
        open_alerts = [a for a in alerts if a['rule_id'] == rule['id'] and a['status'] != 'RESOLVED']
        changed = not state or state['rule_revision'] != rule['revision']
        outcome, alert_id = 'normal', None
        if condition:
            outcome = 'firing'
            if not open_alerts and (changed or not state['condition']):
                opened = {'at': now(), 'actor': user.user, 'action': 'open', 'comment': '显式采样评估超过规则阈值'}
                alert = s.store.create('alert', pid, {'rule_id': rule['id'], 'rule_revision': rule['revision'],
                    'name': rule['name'], 'metric': rule['metric'], 'operator': rule['operator'], 'threshold': threshold,
                    'observed_value': value, 'sample_id': sample['id'], 'sampled_at': sample['created_at'],
                    'status': 'OPEN', 'events': [opened], 'creator': user.user}, user.user)
                alerts.append(alert); alert_id = alert['id']; outcome = 'opened'
            elif open_alerts:
                alert_id = open_alerts[0]['id']
            else:
                outcome = 'resolved_waiting_for_recovery'
        elif open_alerts:
            for alert in open_alerts:
                updated = _change_alert(s, alert, 'recovered', user.user, '显式采样评估确认指标已恢复')
                alerts[alerts.index(alert)] = updated
            outcome = 'recovered'
        data = {'rule_id': rule['id'], 'rule_revision': rule['revision'], 'sample_id': sample['id'],
                'sampled_at': sample['created_at'], 'condition': condition, 'alert_id': alert_id}
        if state:
            s.store.update(state['id'], data, user.user, state['revision'])
        else:
            s.store.create('alert_rule_state', pid, data, user.user)
        output.append({'rule_id': rule['id'], 'outcome': outcome, 'alert_id': alert_id})
    return output


@router.post('/api/projects/{pid}/telemetry/sample', status_code=201)
def sample_host(pid: str, body: SampleInput, s: Svc, user: User):
    user.allow_write(); s.project(pid, user)
    host = collect_host_metrics(s.store.root)
    with s.store.transaction() as db:
        project = s.project(pid, user)
        samples = _recent_samples(s, project)
        if samples and (datetime.now(timezone.utc) - datetime.fromisoformat(samples[0]['created_at'])).total_seconds() < MIN_SAMPLE_INTERVAL_SECONDS:
            raise HTTPException(429, '采样间隔至少为 1 秒；当前监控仅在显式请求时采样')
        usage = _usage(s, pid)
        host['metrics'].update(active_jobs=usage['active_jobs'], stored_bytes=usage['stored_bytes'],
                               project_storage_percent=round(100 * usage['stored_bytes'] / project['quota_bytes'], 3))
        sample = s.store.create('telemetry', pid, {**host, 'creator': user.user}, user.user)
        evaluation = _evaluate(s, pid, sample, user) if body.evaluate_alerts else []
        pruned = _prune(s, project, db)
        return {'sample': sample, 'evaluation': evaluation, 'pruned': pruned, 'policy': _policy(project)}


@router.get('/api/projects/{pid}/telemetry')
def telemetry(pid: str, s: Svc, user: User,
              since: str | None = Query(default=None, max_length=80),
              until: str | None = Query(default=None, max_length=80),
              limit: int = Query(default=2000, ge=1, le=2000)):
    project = s.project(pid, user)
    since, until = _bounds(since, until)
    rows = [r for r in _recent_samples(s, project)
            if (since is None or r['created_at'] >= since) and (until is None or r['created_at'] <= until)]
    return {'items': rows[:limit], 'total': len(rows), 'policy': _policy(project)}


@router.get('/api/projects/{pid}/telemetry/export')
def telemetry_export(pid: str, s: Svc, user: User,
                     since: str | None = Query(default=None, max_length=80),
                     until: str | None = Query(default=None, max_length=80)):
    project = s.project(pid, user)
    since, until = _bounds(since, until)
    rows = [{'id': r['id'], 'at': r['created_at'], 'creator': r['creator'], **r['metrics'],
             'unavailable': r['unavailable'], 'source': r['source']} for r in _recent_samples(s, project)
            if (since is None or r['created_at'] >= since) and (until is None or r['created_at'] <= until)]
    columns = ('id', 'at', 'creator', *METRICS, 'stored_bytes', 'memory_total_bytes',
               'memory_available_bytes', 'disk_total_bytes', 'disk_free_bytes', 'source', 'unavailable')
    return Response(_csv(columns, rows), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="telemetry-{project["id"]}.csv"'})


@router.get('/api/projects/{pid}/alert-rules')
def alert_rules(pid: str, s: Svc, user: User):
    return s.list('alert_rule', pid, user)


def _validate_rule(body):
    if body.metric.endswith('_percent') and body.threshold > 100:
        raise HTTPException(422, '百分比阈值必须位于 0–100')


@router.post('/api/projects/{pid}/alert-rules', status_code=201)
def create_rule(pid: str, body: RuleInput, s: Svc, user: User):
    admin(user); _validate_rule(body)
    with s.store.transaction():
        if len(s.list('alert_rule', pid, user)) >= MAX_RULES:
            raise HTTPException(409, '每个项目最多保存 50 条告警规则；请修改已有规则')
        return s.store.create('alert_rule', pid, {**body.model_dump(), 'creator': user.user}, user.user)


@router.patch('/api/projects/{pid}/alert-rules/{rule_id}')
def update_rule(pid: str, rule_id: str, body: RuleUpdate, s: Svc, user: User):
    admin(user); s.project(pid, user); _validate_rule(body)
    with s.store.transaction():
        rule = s.record(rule_id, 'alert_rule', user)
        if rule['project_id'] != pid:
            raise HTTPException(400, '告警规则不属于当前项目')
        updated = s.store.update(rule_id, body.model_dump(exclude={'expected_revision'}), user.user, body.expected_revision)
        for alert in s.list('alert', pid, user):
            if alert['rule_id'] == rule_id and alert['status'] != 'RESOLVED':
                _change_alert(s, alert, 'rule_changed', user.user, '规则已修改，旧规则告警结束')
        return updated


@router.post('/api/projects/{pid}/alerts/evaluate')
def evaluate_alerts(pid: str, body: EvaluateInput, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        project = s.project(pid, user)
        sample = s.record(body.sample_id, 'telemetry', user)
        if sample['project_id'] != pid:
            raise HTTPException(400, '采样记录不属于当前项目')
        if sample['id'] not in {r['id'] for r in _recent_samples(s, project)}:
            raise HTTPException(409, '采样记录已超过保留范围，请重新采样')
        return {'sample_id': sample['id'], 'evaluation': _evaluate(s, pid, sample, user)}


@router.get('/api/projects/{pid}/alerts')
def alerts(pid: str, s: Svc, user: User,
           status: Literal['OPEN', 'ACKNOWLEDGED', 'RESOLVED'] | None = None,
           limit: int = Query(default=200, ge=1, le=1000),
           offset: int = Query(default=0, ge=0, le=10000000)):
    items = [r for r in s.list('alert', pid, user) if status is None or r['status'] == status]
    return {'items': items[offset:offset + limit], 'total': len(items), 'limit': limit, 'offset': offset}


@router.post('/api/projects/{pid}/alerts/{alert_id}/actions')
def alert_action(pid: str, alert_id: str, body: AlertAction, s: Svc, user: User):
    user.allow_write(); s.project(pid, user)
    with s.store.transaction():
        alert = s.record(alert_id, 'alert', user)
        if alert['project_id'] != pid:
            raise HTTPException(400, '告警不属于当前项目')
        if alert['revision'] != body.expected_revision:
            raise HTTPException(409, 'revision conflict; 请重新载入告警')
        if alert['status'] == 'RESOLVED' or (body.action == 'acknowledge' and alert['status'] != 'OPEN'):
            raise HTTPException(409, '告警状态不支持此操作')
        return _change_alert(s, alert, body.action, user.user, body.comment, body.expected_revision)


@router.post('/api/operations/backup')
def backup(body: BackupInput, s: Svc, user: User):
    from ard.features.operation_backup import BackupBusy, create_backup
    admin(user)
    if '*' not in user.projects:
        raise HTTPException(403, '完整备份需要管理员及全局项目权限')
    # No caller-provided path and no runtime configuration/private directory traversal.
    descriptor, path = tempfile.mkstemp(prefix='ard-backup-', suffix='.zip')
    os.close(descriptor)
    try:
        result = create_backup(s.store, Path(path), user.user)
    except BackupBusy as exc:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(409, str(exc)) from None
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    return FileResponse(path, media_type='application/zip', filename='ai-rd-backup.zip',
                        headers={'X-Backup-Objects': str(result['objects']),
                                 'X-Backup-SHA256': result['sha256']},
                        background=BackgroundTask(Path(path).unlink, missing_ok=True))


def install(app):
    install_once(app, router, 'operations')
