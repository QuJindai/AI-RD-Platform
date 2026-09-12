"""Authorized document lifecycle, parser, index and grounded-answer routes."""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from ard.features.common import Input, Svc, User, install_once
from ard import knowledge
from ard.knowledge_parsers import MAX_SOURCE_BYTES, parse_document
from ard.store import encode


router = APIRouter(prefix='/api/knowledge', tags=['knowledge'])


class EditDocument(Input):
    name: str = Field(min_length=1, max_length=150, pattern=r'.*\S.*')
    text: str = Field(min_length=1, max_length=2_000_000)
    expected_revision: int = Field(ge=1)
    strategy: Literal['fixed', 'paragraph', 'heading', 'sentence', 'delimiter'] = 'paragraph'
    chunk_size: int = Field(default=600, ge=50, le=4000)
    overlap: int = Field(default=60, ge=0, le=1000)
    delimiter: str = Field(default='\n\n', min_length=1, max_length=30)


class ArchiveDocument(Input):
    archived: bool
    confirmed: bool
    expected_revision: int = Field(ge=0)


class AnswerInput(Input):
    query: str = Field(min_length=1, max_length=2000, pattern=r'.*\S.*')
    mode: Literal['keyword', 'semantic', 'hybrid'] = 'keyword'
    top_k: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=.05, ge=0, le=1)


def _document(s, document_id, user):
    document = s.record(document_id, 'document', user)
    return next(item for item in knowledge.decorated_documents(s, document['project_id'])
                if item['id'] == document_id)


def _download(raw, name, media_type):
    return Response(raw, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="{name}"',
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@router.get('/projects/{pid}/documents')
def documents(pid: str, s: Svc, user: User, include_archived: bool = False,
              include_history: bool = False, q: str = Query(default='', max_length=200),
              offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=250)):
    s.project(pid, user)
    items = [item for item in knowledge.decorated_documents(s, pid)
             if (include_archived or not item['archived']) and (include_history or item['is_latest'])
             and q.casefold() in item['name'].casefold()]
    return {'documents': items[offset:offset + limit], 'total': len(items), 'offset': offset, 'limit': limit}


@router.post('/projects/{pid}/import', status_code=201)
async def import_document(pid: str, s: Svc, user: User, file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=150)] = None,
    strategy: Annotated[Literal['fixed', 'paragraph', 'heading', 'sentence', 'delimiter'], Form()] = 'paragraph',
    chunk_size: Annotated[int, Form(ge=50, le=4000)] = 600,
    overlap: Annotated[int, Form(ge=0, le=1000)] = 60,
    delimiter: Annotated[str, Form(min_length=1, max_length=30)] = '\n\n'):
    user.allow_write()
    s.project(pid, user)
    try:
        raw = await file.read(MAX_SOURCE_BYTES + 1)
        parsed = await run_in_threadpool(parse_document, file.filename or '', raw)
    finally:
        await file.close()
    title = (name or parsed['filename']).strip()
    if not title or len(title) > 150:
        raise ValueError('文档名称必须为1至150字符')
    config = {'name': title, 'text': parsed['text'], 'strategy': strategy,
              'chunk_size': chunk_size, 'overlap': overlap, 'delimiter': delimiter}
    return await run_in_threadpool(knowledge.add_document, s, pid, config, user.user, source_raw=raw,
        source={'filename': parsed['filename'], 'format': parsed['format']}, segments=parsed['segments'])


@router.get('/documents/{document_id}')
def detail(document_id: str, s: Svc, user: User):
    document = _document(s, document_id, user)
    data = knowledge.document_data(s, document)
    return {'document': document, 'text': data['text'], 'source': data.get('source', {}),
            'segments': data.get('segments', []), 'offset_unit': 'Unicode code points; end exclusive'}


@router.get('/documents/{document_id}/chunks')
def chunks(document_id: str, s: Svc, user: User, offset: int = Query(default=0, ge=0),
           limit: int = Query(default=20, ge=1, le=100)):
    document = s.record(document_id, 'document', user)
    data = knowledge.document_data(s, document)
    result = [{**chunk, 'locations': knowledge.source_locations(data.get('segments', []), chunk['start'], chunk['end'])}
              for chunk in data['chunks'][offset:offset + limit]]
    return {'document_id': document_id, 'source': data.get('source', {}), 'chunks': result,
            'total': len(data['chunks']), 'offset': offset, 'limit': limit,
            'offset_unit': 'Unicode code points; end exclusive'}


@router.get('/documents/{document_id}/chunks/{chunk_index}')
def chunk(document_id: str, chunk_index: int, s: Svc, user: User,
          context: int = Query(default=120, ge=0, le=1000)):
    document = s.record(document_id, 'document', user)
    data = knowledge.document_data(s, document)
    if not 0 <= chunk_index < len(data['chunks']):
        raise KeyError('chunk not found')
    value = data['chunks'][chunk_index]
    begin, end = max(0, value['start'] - context), min(len(data['text']), value['end'] + context)
    return {'document_id': document_id, 'source': data.get('source', {}), 'chunk': value,
            'locations': knowledge.source_locations(data.get('segments', []), value['start'], value['end']),
            'preview': {'start': begin, 'end': end, 'before': data['text'][begin:value['start']],
                        'selected': data['text'][value['start']:value['end']], 'after': data['text'][value['end']:end]},
            'offset_unit': 'Unicode code points; end exclusive'}


@router.get('/documents/{document_id}/export')
def export(document_id: str, s: Svc, user: User, format: Literal['source', 'text', 'chunks'] = 'chunks'):
    document = s.record(document_id, 'document', user)
    data = knowledge.document_data(s, document)
    if format == 'source' and document.get('source_sha256'):
        extension = data.get('source', {}).get('format', 'bin')
        return _download(s.store.read_blob(document['source_sha256']),
                         f'document-{document_id}.{extension}', 'application/octet-stream')
    if format in ('text', 'source'):
        return _download(data['text'].encode('utf-8'), f'document-{document_id}.txt', 'text/plain')
    return _download(encode({'document': document, **data,
        'offset_unit': 'Unicode code points; end exclusive'}), f'document-{document_id}-chunks.json', 'application/json')


@router.post('/documents/{document_id}/versions', status_code=201)
def edit(document_id: str, body: EditDocument, s: Svc, user: User):
    user.allow_write()
    document = s.record(document_id, 'document', user)
    if document['revision'] != body.expected_revision:
        raise ValueError('revision conflict; 文档版本已变化')
    return knowledge.add_document(s, document['project_id'], body.model_dump(exclude={'expected_revision'}), user.user,
        source={'filename': 'edited-document.txt', 'format': 'txt', 'derived_from_document_id': document_id,
                'derived_from_source_sha256': document.get('source_sha256', document['sha256'])}, parent=document)


@router.post('/documents/{document_id}/archive')
def archive(document_id: str, body: ArchiveDocument, s: Svc, user: User):
    user.allow_write()
    document = s.record(document_id, 'document', user)
    if not body.confirmed:
        raise ValueError('请明确确认文档归档或恢复操作')
    state = knowledge.archive_document(s, document, body.archived, body.expected_revision, user.user)
    children = [item['id'] for item in s.list('document', document['project_id'], user)
                if item.get('parent_id') == document_id]
    return {'document_id': document_id, 'archived': state['archived'], 'status_revision': state['revision'],
            'source_retained': True, 'derived_version_ids': children,
            'reference_policy': 'historical_sources_remain_exportable'}


@router.get('/projects/{pid}/index')
def index(pid: str, s: Svc, user: User):
    s.project(pid, user)
    return knowledge.index_status(s, pid)


@router.post('/projects/{pid}/index/build')
def build(pid: str, s: Svc, user: User):
    user.allow_write()
    s.project(pid, user)
    return knowledge.build_index(s, pid, user.user)


@router.post('/projects/{pid}/answer')
def answer(pid: str, body: AnswerInput, s: Svc, user: User):
    s.project(pid, user)
    return knowledge.grounded_answer(s, pid, **body.model_dump())


def install(app):
    install_once(app, router, 'knowledge')
