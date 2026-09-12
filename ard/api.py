"""HTTP API and same-origin web console."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import time
from typing import Annotated, Literal

from fastapi import FastAPI, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ard import __version__
from ard.security import Identity, Security
from ard.service import Service


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r'.*\S.*')
    description: str = Field(default='', max_length=2000)
    quota_bytes: int = Field(default=104857600, ge=1024, le=10737418240)
    max_jobs: int = Field(default=4, ge=1, le=20)


class DatasetInput(Input):
    name: str = Field(min_length=1, max_length=150)
    rows: list[dict] = Field(max_length=50000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class TransformInput(Input):
    operations: list[dict] = Field(min_length=1, max_length=30)
    name: str | None = Field(default=None, max_length=150)


class SplitInput(Input):
    ratio: float = Field(default=.8, gt=0, lt=1)
    seed: int = 42


class MergeInput(Input):
    asset_ids: list[str] = Field(min_length=2, max_length=20)
    name: str = Field(min_length=1, max_length=150)


class TransferInput(Input):
    target_project_id: str | None = None


class DecisionInput(Input):
    decision: Literal['approve', 'reject']
    expected_revision: int = Field(ge=1)
    comment: str = Field(default='', max_length=2000)


class TrainInput(Input):
    target: str = Field(min_length=1, max_length=200)
    task: Literal['classification', 'regression'] = 'classification'
    features: list[str] | None = None
    test_fraction: float = Field(default=.25, ge=.1, le=.5)
    seed: int = 42


class PredictInput(Input):
    rows: list[dict] = Field(min_length=1, max_length=10000)


class DeployInput(Input):
    expires_minutes: int = Field(default=60, ge=1, le=10080)


class RevisionInput(Input):
    expected_revision: int = Field(ge=1)


class DocumentInput(Input):
    name: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=2000000)
    strategy: Literal['fixed', 'paragraph', 'heading', 'sentence', 'delimiter'] = 'paragraph'
    chunk_size: int = Field(default=600, ge=50, le=4000)
    overlap: int = Field(default=60, ge=0, le=1000)
    delimiter: str = Field(default='\n\n', min_length=1, max_length=30)


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0, ge=0, le=1)
    mode: Literal['keyword', 'semantic', 'hybrid'] = 'keyword'


class WorkflowInput(Input):
    name: str = Field(min_length=1, max_length=150)
    nodes: list[dict] = Field(min_length=1, max_length=40)
    edges: list[dict] = Field(default_factory=list, max_length=100)
    parent_id: str | None = None


class RunInput(Input):
    input: dict = Field(default_factory=dict)


class BodyLimit:
    def __init__(self, app, limit=22 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        total = 0

        async def limited():
            nonlocal total
            message = await receive()
            total += len(message.get('body', b''))
            if total > self.limit:
                raise HTTPException(413, '请求超过22MiB限制')
            return message
        await self.app(scope, limited, send)


def create_app(data_dir=None, identities=None):
    if identities is None:
        identities = json.loads(os.environ.get('ARD_IDENTITIES', '{}'))
    security = Security(identities, os.environ.get('ARD_PUBLIC_ORIGIN'))
    root = data_dir or os.environ.get('ARD_DATA_DIR', 'runtime')
    instance_id = os.environ.get('ARD_INSTANCE_ID')

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = Service(root)
        yield
        app.state.service.close()

    app = FastAPI(title='AI-RD-Platform', version=__version__, lifespan=lifespan)
    app.state.security = security
    allowed = ['localhost', '127.0.0.1', '[::1]', 'testserver']
    if security.public_host:
        allowed.append(security.public_host)
    allowed += [x.strip() for x in os.environ.get('ARD_ALLOWED_HOSTS', '').split(',') if x.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)
    app.add_middleware(BodyLimit)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': '对象不存在'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        message = str(exc)
        conflict = any(w in message for w in ('revision conflict', '尚未获批', 'terminal job', '等待审批状态'))
        return JSONResponse({'detail': message}, status_code=409 if conflict else 400)

    def identity(request: Request):
        return security.resolve(request)

    def service(request: Request):
        return request.app.state.service

    User = Annotated[Identity, Depends(identity)]
    Svc = Annotated[Service, Depends(service)]

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'AI-RD-Platform', 'version': __version__}

    @app.get('/api/me')
    def me(user: User):
        return {'user': user.user, 'role': user.role, 'mode': 'local' if user.local else 'token', 'projects': user.projects}

    @app.get('/api/runtime/identity')
    def runtime_identity(user: User):
        if user.local or user.role != 'admin' or '*' not in user.projects:
            raise HTTPException(403, '需要已认证的全局管理员')
        return {'service': 'AI-RD-Platform', 'pid': os.getpid(), 'instance_id': instance_id}

    @app.get('/api/projects')
    def projects(s: Svc, user: User):
        return [p for p in s.store.list('project') if '*' in user.projects or p['id'] in user.projects]

    @app.post('/api/projects', status_code=201)
    def add_project(body: ProjectInput, s: Svc, user: User):
        return s.create_project(**body.model_dump(), identity=user)

    @app.get('/api/overview')
    def overview(s: Svc, user: User):
        ps = projects(s, user)
        ids = {p['id'] for p in ps}
        records = [r for r in s.store.list() if r['project_id'] in ids]
        counts = {label: sum(r['kind'] == kind for r in records) for label, kind in
                  [('datasets', 'dataset'), ('models', 'model'), ('documents', 'document'), ('workflows', 'workflow')]}
        jobs = [r for r in records if r['kind'] == 'job']
        counts.update(projects=len(ps), jobs=len(jobs))
        return {'counts': counts, 'jobs': jobs[:8], 'resources': {
            'cpu_count': os.cpu_count(), 'worker_slots': 2, 'running_jobs': sum(j['status'] == 'RUNNING' for j in jobs),
            'queued_jobs': sum(j['status'] == 'QUEUED' for j in jobs),
            'stored_bytes': sum(r.get('size_bytes', 0) for r in records),
            'gpu': {'status': 'not_connected', 'devices': []}},
            'engine': 'scikit-learn CPU', 'version': __version__}

    @app.get('/api/projects/{pid}/datasets')
    def datasets(pid: str, s: Svc, user: User):
        from ard.features.data import is_archived
        return [asset for asset in s.list('dataset', pid, user) if not is_archived(s, asset)]

    @app.post('/api/projects/{pid}/datasets', status_code=201)
    def add_dataset(pid: str, body: DatasetInput, s: Svc, user: User):
        return s.add_dataset(pid, **body.model_dump(), identity=user)

    @app.post('/api/projects/{pid}/import', status_code=201)
    async def import_data(pid: str, s: Svc, user: User, file: UploadFile = File(...)):
        user.allow_write(); s.project(pid, user)
        raw = await file.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise HTTPException(413, '文件超过20MiB限制')
        return s.import_data(pid, None, file.filename or 'upload.csv', raw, user)

    @app.get('/api/assets/{asset_id}')
    def get_asset(asset_id: str, s: Svc, user: User):
        return s.record(asset_id, 'dataset', user)

    @app.get('/api/assets/{asset_id}/rows')
    def asset_rows(asset_id: str, s: Svc, user: User, limit: int = Query(default=100, ge=1, le=50000), offset: int = Query(default=0, ge=0)):
        d = s.record(asset_id, 'dataset', user)
        return {'rows': s.rows(asset_id)[offset:offset + limit], 'total': d['row_count']}

    @app.get('/api/assets/{asset_id}/export')
    def export(asset_id: str, s: Svc, user: User):
        d = s.record(asset_id, 'dataset', user)
        return Response(s.store.read_blob(d['sha256']), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="dataset-{asset_id}.json"'})

    @app.post('/api/assets/{asset_id}/transform', status_code=201)
    def clean(asset_id: str, body: TransformInput, s: Svc, user: User):
        return s.transform_data(asset_id, body.operations, body.name, user)

    @app.post('/api/assets/{asset_id}/split', status_code=201)
    def split(asset_id: str, body: SplitInput, s: Svc, user: User):
        return s.split_data(asset_id, body.ratio, body.seed, user)

    @app.post('/api/projects/{pid}/merge', status_code=201)
    def merge(pid: str, body: MergeInput, s: Svc, user: User):
        return s.merge_data(pid, body.asset_ids, body.name, user)

    @app.post('/api/assets/{asset_id}/transfer', status_code=201)
    def transfer(asset_id: str, body: TransferInput, s: Svc, user: User):
        return s.transfer(asset_id, body.target_project_id, user)

    @app.get('/api/projects/{pid}/approvals')
    def approvals(pid: str, s: Svc, user: User):
        return s.list('approval', pid, user)

    @app.post('/api/approvals/{approval_id}/decide')
    def decide(approval_id: str, body: DecisionInput, s: Svc, user: User):
        return s.decide(approval_id, body.decision, body.expected_revision, body.comment, user)

    @app.post('/api/assets/{asset_id}/train', status_code=202)
    def train(asset_id: str, body: TrainInput, s: Svc, user: User):
        return s.train(asset_id, body.model_dump(), user)

    @app.get('/api/projects/{pid}/jobs')
    def jobs(pid: str, s: Svc, user: User):
        return s.list('job', pid, user)

    @app.get('/api/jobs/{job_id}')
    def job(job_id: str, s: Svc, user: User):
        return s.record(job_id, 'job', user)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str, body: RevisionInput, s: Svc, user: User):
        user.allow_write(); s.record(job_id, 'job', user)
        return s.jobs.cancel(job_id, user.user, body.expected_revision)

    @app.get('/api/projects/{pid}/models')
    def models(pid: str, s: Svc, user: User):
        return s.list('model', pid, user)

    @app.get('/api/models/{model_id}')
    def model(model_id: str, s: Svc, user: User):
        return s.record(model_id, 'model', user)

    @app.get('/api/models/{model_id}/artifact')
    def artifact(model_id: str, s: Svc, user: User):
        m = s.record(model_id, 'model', user)
        return Response(s.store.read_blob(m['sha256']), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="model-{model_id}.json"'})

    @app.post('/api/models/{model_id}/deploy', status_code=201)
    def deploy(model_id: str, body: DeployInput, s: Svc, user: User):
        return s.deploy(model_id, body.expires_minutes, user)

    @app.get('/api/projects/{pid}/deployments')
    def deployments(pid: str, s: Svc, user: User):
        return s.list('deployment', pid, user)

    @app.post('/api/deployments/{deployment_id}/predict')
    def predict(deployment_id: str, body: PredictInput, s: Svc, user: User):
        dep = s.record(deployment_id, 'deployment', user)
        if dep['status'] != 'ACTIVE' or datetime.fromisoformat(dep['expires_at']) <= datetime.now(timezone.utc):
            raise HTTPException(410, '模型服务已停用或超过运行时限')
        start, failed = time.perf_counter(), False
        try:
            return {'predictions': s.predict(dep['model_id'], body.rows), 'model_id': dep['model_id']}
        except Exception:
            failed = True
            raise
        finally:
            with s.store.lock:
                current = s.store.get(deployment_id)
                s.store.update(deployment_id, {'requests': current['requests'] + 1,
                    'failures': current['failures'] + int(failed),
                    'total_latency_ms': current['total_latency_ms'] + (time.perf_counter() - start) * 1000}, user.user)

    @app.post('/api/deployments/{deployment_id}/stop')
    def stop(deployment_id: str, body: RevisionInput, s: Svc, user: User):
        user.allow_write(); s.record(deployment_id, 'deployment', user)
        return s.store.update(deployment_id, {'status': 'STOPPED'}, user.user, body.expected_revision)

    @app.get('/api/projects/{pid}/documents')
    def documents(pid: str, s: Svc, user: User):
        from ard.knowledge import active_documents
        s.project(pid, user)
        return active_documents(s, pid)

    @app.post('/api/projects/{pid}/documents', status_code=201)
    def document(pid: str, body: DocumentInput, s: Svc, user: User):
        from ard.knowledge import add_document
        user.allow_write(); s.project(pid, user)
        return add_document(s, pid, body.model_dump(), user.user)

    @app.post('/api/projects/{pid}/search')
    def search(pid: str, body: SearchInput, s: Svc, user: User):
        from ard.knowledge import search_documents
        s.project(pid, user)
        return search_documents(s, pid, **body.model_dump())

    @app.get('/api/projects/{pid}/workflows')
    def workflows(pid: str, s: Svc, user: User):
        return s.list('workflow', pid, user)

    @app.post('/api/projects/{pid}/workflows', status_code=201)
    def workflow(pid: str, body: WorkflowInput, s: Svc, user: User):
        from ard.features.workflows import save_workflow
        return save_workflow(s, pid, body, user, body.parent_id)

    @app.post('/api/workflows/{workflow_id}/run', status_code=202)
    def run(workflow_id: str, body: RunInput, s: Svc, user: User):
        from ard.store import encode
        user.allow_write(); flow = s.record(workflow_id, 'workflow', user)
        if len(encode(body.input)) > 2 * 1024 * 1024:
            raise ValueError('工作流输入超过2MiB，请引用已保存数据集')
        p = s.project(flow['project_id'], user)
        return s.jobs.submit(p['id'], {'kind': 'workflow', 'workflow_id': workflow_id, 'input': body.input}, user.user, p['max_jobs'])

    @app.get('/api/connectors')
    def connectors(user: User):
        from ard.connectors import capabilities
        return capabilities()

    @app.post('/api/connectors/ollama/probe')
    def probe(user: User):
        from ard.connectors import ollama_probe
        user.allow_write()
        return ollama_probe()

    @app.get('/api/audit')
    def audit(s: Svc, user: User, limit: int = Query(default=100, ge=1, le=1000)):
        return s.store.audits(None if '*' in user.projects else user.projects, limit)

    @app.get('/api/audit/verify')
    def verify_audit(s: Svc, user: User):
        if '*' not in user.projects:
            raise HTTPException(403, '全局审计验证需要全局项目权限')
        return s.store.verify_audit()

    from ard.features import install
    install(app)

    static = Path(__file__).parent / 'static'
    if static.is_dir():
        app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        if not (static / 'index.html').exists():
            return JSONResponse({'service': 'AI-RD-Platform', 'docs': '/docs'})
        return FileResponse(static / 'index.html', headers={'Cache-Control': 'no-store'})

    return app
