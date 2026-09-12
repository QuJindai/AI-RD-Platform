"""Workflow interchange, immutable versions and cooperative execution controls."""
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field

from ard.features.common import Input, Svc, User, install_once
from ard.store import encode
from ard.workflows import NODE_TYPES, PARAM_KEYS, validate


router = APIRouter(prefix='/api', tags=['workflow-features'])


class Definition(Input):
    name: str = Field(min_length=1, max_length=150, pattern=r'.*\S.*')
    nodes: list[dict] = Field(min_length=1, max_length=40)
    edges: list[dict] = Field(default_factory=list, max_length=100)


class Package(Input):
    format: Literal['ard-workflow-v1']
    workflow: Definition
    parent_id: str | None = Field(default=None, max_length=80)


class Revision(Input):
    expected_revision: int = Field(ge=1)


def _check(s, pid, body, user):
    """Check the graph and fixed references while the caller holds a transaction."""
    from ard.features.data import require_active
    order, incoming = validate(body.nodes, body.edges)
    for node in body.nodes:
        for key, kind in (('asset_id', 'dataset'), ('model_id', 'model')):
            identifier = node.get('params', {}).get(key)
            if identifier:
                record = s.record(identifier, kind, user)
                if record['project_id'] != pid:
                    raise ValueError('工作流不能引用其他项目的资产')
                if kind == 'dataset':
                    require_active(s, record)
    return order, incoming


def save_workflow(s, pid, body, user, parent=None):
    """Save one immutable version; legacy, import and version routes share this gate.

    ``body`` supplies ``name``, ``nodes`` and ``edges`` (Definition or the legacy
    API's WorkflowInput). ``parent`` is the explicitly selected parent version.
    """
    user.allow_write()
    with s.store.transaction():
        s.project(pid, user)
        _check(s, pid, body, user)
        if parent and s.record(parent, 'workflow', user)['project_id'] != pid:
            raise ValueError('工作流版本不能跨项目')
        payload = {key: getattr(body, key) for key in ('name', 'nodes', 'edges')}
        return s.store.create('workflow', pid, {**payload, 'parent_id': parent, 'creator': user.user}, user.user)


@router.get('/workflow-node-types')
def node_types(user: User):
    return {'node_types': list(NODE_TYPES), 'params': {k: sorted(v) for k, v in PARAM_KEYS.items()},
            'limits': {'nodes': 40, 'edges': 100, 'iteration_items': 100, 'checkpoint_bytes': 16 * 1024 * 1024}}


@router.post('/projects/{pid}/workflows/validate')
def validate_definition(pid: str, body: Definition, s: Svc, user: User):
    with s.store.transaction():
        s.project(pid, user)
        order, incoming = _check(s, pid, body, user)
    return {'valid': True, 'order': order, 'incoming': incoming}


@router.post('/projects/{pid}/workflows/import', status_code=201)
def import_workflow(pid: str, body: Package, s: Svc, user: User):
    return save_workflow(s, pid, body.workflow, user, body.parent_id)


@router.get('/workflows/{workflow_id}/export')
def export_workflow(workflow_id: str, s: Svc, user: User):
    flow = s.record(workflow_id, 'workflow', user)
    return Response(encode({'format': 'ard-workflow-v1', 'workflow': {k: flow[k] for k in ('name', 'nodes', 'edges')}}),
                    media_type='application/json', headers={'Content-Disposition': f'attachment; filename="workflow-{workflow_id}.json"'})


@router.get('/workflows/{workflow_id}/versions')
def workflow_versions(workflow_id: str, s: Svc, user: User):
    selected = s.record(workflow_id, 'workflow', user)
    records = s.list('workflow', selected['project_id'], user)
    mapping = {r['id']: r for r in records}
    def root(record):
        seen = set()
        while record.get('parent_id') in mapping and record['id'] not in seen:
            seen.add(record['id'])
            record = mapping[record['parent_id']]
        return record['id']
    root_id = root(selected)
    return [r for r in reversed(records) if root(r) == root_id]


@router.post('/workflows/{workflow_id}/versions', status_code=201)
def save_workflow_version(workflow_id: str, body: Definition, s: Svc, user: User):
    with s.store.transaction():
        parent = s.record(workflow_id, 'workflow', user)
        return save_workflow(s, parent['project_id'], body, user, workflow_id)


@router.post('/jobs/{job_id}/pause')
def pause(job_id: str, body: Revision, s: Svc, user: User):
    user.allow_write()
    s.record(job_id, 'job', user)
    return s.jobs.pause(job_id, user.user, body.expected_revision)


@router.post('/jobs/{job_id}/resume', status_code=202)
def resume(job_id: str, body: Revision, s: Svc, user: User):
    user.allow_write()
    job = s.record(job_id, 'job', user)
    project = s.project(job['project_id'], user)
    return s.jobs.resume(job_id, user.user, body.expected_revision, project['max_jobs'])


@router.post('/jobs/{job_id}/retry', status_code=202)
def retry(job_id: str, body: Revision, s: Svc, user: User):
    user.allow_write()
    job = s.record(job_id, 'job', user)
    project = s.project(job['project_id'], user)
    return s.jobs.resume(job_id, user.user, body.expected_revision, project['max_jobs'], retry=True)


def install(app):
    install_once(app, router, 'workflows')
