"""Actual parser/API tests; embedding/chat fixtures are protocol unit mocks only."""
from io import BytesIO
import hashlib
import json
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ard.api import create_app
from ard.features.knowledge import install
from ard.knowledge_parsers import parse_document


def app_at(path, identities=None):
    app = create_app(path, identities=identities)
    install(app)
    install(app)
    return app


@pytest.fixture
def client(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        yield client


def project(client, name='文档验证', **kwargs):
    response = client.post('/api/projects', json={'name': name, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()['id']


def imported(client, pid, text='合成规范：温度超过阈值必须复核。', **config):
    response = client.post(f'/api/knowledge/projects/{pid}/import',
        files={'file': ('evidence.txt', text.encode(), 'text/plain')}, data=config)
    assert response.status_code == 201, response.text
    return response.json()


def docx_bytes(xml=None, extras=()):
    document = xml or ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                      '<w:body><w:p><w:r><w:t>合成检测标准</w:t></w:r></w:p>'
                      '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>压力阈值 12</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                      '</w:body></w:document>')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', document)
        for name, value in extras:
            archive.writestr(name, value)
    return buffer.getvalue()


def pdf_bytes(text='Pressure limit 12', pages=1, stream_bytes=None, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
             NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(stream_bytes if stream_bytes is not None else f'BT /F1 12 Tf 20 200 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream.flate_encode() if stream_bytes else stream)
    if encrypted:
        writer.encrypt('test-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def xlsx_bytes():
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = '合成工艺'
    sheet.append(['项目', '阈值', '公式原文'])
    sheet.append(['压力', 12, '=1+2'])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(('filename', 'raw', 'expected', 'locator'), [
    ('plain.txt', '首行\r\n第二行 😀'.encode(), '首行\r\n第二行 😀', 'line_start'),
    ('note.md', b'# Heading\n\nSome **evidence**.', '**evidence**', 'line_start'),
    ('source.docx', docx_bytes(), '压力阈值 12', 'paragraph'),
    ('source.html', '<html><head><title>忽略</title></head><body><h1>检测</h1><p>压力 &lt; 12</p><script>secret()</script></body></html>'.encode(), '压力 < 12', 'html_block'),
], ids=['utf8-text', 'markdown', 'docx', 'html'])
def test_actual_text_docx_and_html_parsers(filename, raw, expected, locator):
    parsed = parse_document(filename, raw)
    assert expected in parsed['text']
    assert parsed['segments'] and locator in parsed['segments'][0]
    assert 'secret()' not in parsed['text']
    assert all(parsed['text'][segment['start']:segment['end']] for segment in parsed['segments'])


def test_actual_pdf_and_xlsx_parsers():
    parsed = parse_document('source.pdf', pdf_bytes())
    assert 'Pressure limit 12' in parsed['text']
    assert parsed['segments'][0]['page'] == 1
    parsed = parse_document('source.xlsx', xlsx_bytes())
    assert 'B2: 12' in parsed['text'] and 'C2: =1+2' in parsed['text']
    assert parsed['segments'][1]['sheet'] == '合成工艺'
    assert parsed['segments'][1]['cells'] == ['A2', 'B2', 'C2']


@pytest.mark.parametrize(('filename', 'raw'), [
    ('bad.docx', docx_bytes(extras=[('../escaped.txt', 'x')])),
    ('bad.docx', docx_bytes(extras=[('huge.xml', 'x' * 1_000_000)])),
    ('bad.docx', docx_bytes('<!DOCTYPE x [<!ENTITY y "abc">]><x>&y;</x>')),
    ('bad.docx', b'not a zip'),
    ('bad.docx', docx_bytes('<x>')),
    ('binary.txt', b'\xff\xff\x00'),
    ('big.txt', b'x' * 2_000_001),
    ('big.txt', b'x' * (10 * 1024 * 1024 + 1)),
    ('bad.exe', b'executable'),
    ('../source.txt', b'text'),
    ('bad.pdf', b'not a pdf'),
], ids=['traversal', 'compressed-bomb', 'xml-entity', 'invalid-zip', 'invalid-xml',
        'invalid-text', 'text-limit', 'upload-limit', 'unsupported-extension',
        'filename-traversal', 'invalid-pdf'])
def test_malformed_and_oversized_inputs_fail_closed(filename, raw):
    with pytest.raises(ValueError):
        parse_document(filename, raw)


def test_zip_member_count_and_xml_encoding_guards():
    extras = [(f'entry-{i}', 'x') for i in range(1000)]
    with pytest.raises(ValueError, match='数量'):
        parse_document('bad.docx', docx_bytes(extras=extras))
    declaration = '<!DOCTYPE x [<!ENTITY y "abc">]><x>&y;</x>'.encode('utf-16')
    with pytest.raises(ValueError, match='DTD'):
        parse_document('bad.docx', docx_bytes(declaration))


def test_pdf_resource_and_encryption_bounds():
    with pytest.raises(ValueError):
        parse_document('bomb.pdf', pdf_bytes(stream_bytes=b' ' * (9 * 1024 * 1024)))
    with pytest.raises(ValueError, match='300'):
        parse_document('many.pdf', pdf_bytes(pages=301))
    with pytest.raises(ValueError, match='加密'):
        parse_document('encrypted.pdf', pdf_bytes(encrypted=True))


def test_import_preserves_source_offsets_detail_and_exports(client):
    pid = project(client)
    raw = '# 标题 😀\n\n' + '合成压力记录需要复核。' * 20
    document = imported(client, pid, raw, chunk_size='60', overlap='12', strategy='fixed')
    assert document['source_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    detail = client.get(f'/api/knowledge/documents/{document["id"]}').json()
    assert detail['text'] == raw
    chunks = client.get(f'/api/knowledge/documents/{document["id"]}/chunks?limit=100').json()
    assert len(chunks['chunks']) == document['chunk_count'] > 1
    for chunk in chunks['chunks']:
        assert raw[chunk['start']:chunk['end']] == chunk['text']
    preview = client.get(f'/api/knowledge/documents/{document["id"]}/chunks/1?context=8').json()
    assert preview['preview']['selected'] == chunks['chunks'][1]['text']
    assert preview['preview']['before'] + preview['preview']['selected'] + preview['preview']['after'] == raw[preview['preview']['start']:preview['preview']['end']]
    source = client.get(f'/api/knowledge/documents/{document["id"]}/export?format=source')
    assert source.content == raw.encode()
    exported = client.get(f'/api/knowledge/documents/{document["id"]}/export?format=chunks').json()
    assert exported['chunks'] == [{key: value for key, value in item.items() if key != 'locations'}
                                  for item in chunks['chunks']]
    assert exported['text'] == raw
    assert client.get(f'/api/knowledge/documents/{document["id"]}/chunks/-1').status_code == 404


@pytest.mark.parametrize(('name', 'factory', 'contains'), [('a.docx', docx_bytes, '压力阈值 12'),
    ('a.pdf', pdf_bytes, 'Pressure limit 12'), ('a.xlsx', xlsx_bytes, '=1+2')])
def test_binary_import_via_api_retains_original(client, name, factory, contains):
    pid, raw = project(client), factory()
    response = client.post(f'/api/knowledge/projects/{pid}/import', files={'file': (name, raw)})
    assert response.status_code == 201, response.text
    document = response.json()
    assert client.get(f'/api/knowledge/documents/{document["id"]}/export?format=source').content == raw
    assert contains in client.get(f'/api/knowledge/documents/{document["id"]}').json()['text']


def test_edits_are_immutable_current_versions_and_archival_is_reversible(client):
    pid = project(client)
    original = imported(client, pid, 'oldneedle')
    old_record = client.app.state.service.store.get(original['id'])
    payload = {'name': '修订资料', 'text': 'newneedle', 'expected_revision': original['revision']}
    response = client.post(f'/api/knowledge/documents/{original["id"]}/versions', json=payload)
    assert response.status_code == 201, response.text
    child = response.json()
    assert child['parent_id'] == original['id'] and child['version'] == 2
    assert client.app.state.service.store.get(original['id']) == old_record
    assert client.post(f'/api/knowledge/documents/{original["id"]}/versions', json=payload).status_code == 409
    current = client.get(f'/api/knowledge/projects/{pid}/documents').json()['documents']
    assert [item['id'] for item in current] == [child['id']]
    history = client.get(f'/api/knowledge/projects/{pid}/documents?include_history=true').json()
    assert history['total'] == 2
    assert client.post(f'/api/projects/{pid}/search', json={'query': 'oldneedle', 'threshold': .99}).json()['matches'] == []
    archive = {'archived': True, 'confirmed': True, 'expected_revision': 0}
    assert client.post(f'/api/knowledge/documents/{child["id"]}/archive', json={**archive, 'confirmed': False}).status_code == 400
    response = client.post(f'/api/knowledge/documents/{child["id"]}/archive', json=archive)
    assert response.status_code == 200, response.text
    assert client.post(f'/api/projects/{pid}/search', json={'query': 'newneedle'}).json()['matches'] == []
    assert client.get(f'/api/knowledge/documents/{child["id"]}/export?format=text').text == 'newneedle'
    assert client.post(f'/api/knowledge/documents/{child["id"]}/archive', json=archive).status_code == 409
    restored = client.post(f'/api/knowledge/documents/{child["id"]}/archive', json={**archive, 'archived': False, 'expected_revision': 1})
    assert restored.status_code == 200
    assert client.post(f'/api/projects/{pid}/search', json={'query': 'newneedle'}).json()['matches'][0]['document_id'] == child['id']
    assert client.get('/api/audit/verify').json()['valid']


@pytest.fixture
def embedding_protocol_mock(monkeypatch):
    # Deterministic fixture verifies caching/protocol plumbing, not remote model quality.
    from ard import connectors
    calls = []
    identity = {'provider': 'ollama', 'model': 'synthetic-embed-v1', 'endpoint_sha256': 'a' * 64}

    def embed(texts):
        calls.append(list(texts))
        return np.array([[float('alpha' in text), float('beta' in text), 0.1] for text in texts])

    monkeypatch.setattr(connectors, 'embedding_identity', lambda: dict(identity), raising=False)
    monkeypatch.setattr(connectors, 'embeddings', embed)
    return identity, calls


def test_persistent_index_reuses_unchanged_chunks_and_invalidates(tmp_path, embedding_protocol_mock):
    identity, calls = embedding_protocol_mock
    with TestClient(app_at(tmp_path)) as client:
        pid = project(client)
        document = imported(client, pid, 'alpha\n\nbeta', strategy='paragraph')
        search_url, index_url = f'/api/projects/{pid}/search', f'/api/knowledge/projects/{pid}/index'
        assert client.post(search_url, json={'query': 'alpha', 'mode': 'semantic'}).status_code == 400
        assert calls == []
        first = client.post(index_url + '/build').json()
        assert first['embedded_chunks'] == 2 and calls == [['alpha\n\n', 'beta']]
        assert client.post(index_url + '/build').json()['embedded_chunks'] == 0
        response = client.post(search_url, json={'query': 'alpha', 'mode': 'semantic'})
        assert response.status_code == 200, response.text
        assert response.json()['matches'][0]['text'].startswith('alpha')
        assert calls[-1] == ['alpha']
    with TestClient(app_at(tmp_path)) as client:
        assert client.get(index_url).json()['state'] == 'ready'
        count = len(calls)
        assert client.post(search_url, json={'query': 'beta', 'mode': 'hybrid'}).status_code == 200
        assert calls[count:] == [['beta']]
        response = client.post(f'/api/knowledge/documents/{document["id"]}/versions', json={
            'name': '修订', 'text': 'alpha\n\nbeta changed', 'expected_revision': 1})
        child = response.json()
        assert response.status_code == 201, response.text
        assert client.get(index_url).json()['state'] == 'stale'
        assert client.post(search_url, json={'query': 'alpha', 'mode': 'semantic'}).status_code == 400
        result = client.post(index_url + '/build').json()
        assert result['embedded_chunks'] == 1 and result['reused_chunks'] == 1
        assert calls[-1] == ['beta changed']
        assert client.post(f'/api/knowledge/documents/{child["id"]}/archive', json={
            'archived': True, 'confirmed': True, 'expected_revision': 0}).status_code == 200
        assert client.get(index_url).json()['state'] == 'stale'
        count = len(calls)
        assert client.post(search_url, json={'query': 'alpha', 'mode': 'semantic'}).json()['matches'] == []
        assert client.post(index_url + '/build').json()['chunk_count'] == 0
        assert len(calls) == count
        client.post(f'/api/knowledge/documents/{child["id"]}/archive', json={
            'archived': False, 'confirmed': True, 'expected_revision': 1})
        assert client.post(index_url + '/build').json()['embedded_chunks'] == 0
        identity['model'] = 'synthetic-embed-v2'
        assert client.get(index_url).json()['state'] == 'not_built'
        assert client.post(search_url, json={'query': 'alpha', 'mode': 'semantic'}).status_code == 400
        assert client.post(index_url + '/build').json()['embedded_chunks'] == 2
        identity['endpoint_sha256'] = 'b' * 64
        assert client.get(index_url).json()['state'] == 'not_built'


def test_grounded_answer_no_hit_never_calls_model_and_citations_are_matched(client, monkeypatch):
    from ard import connectors
    pid, calls = project(client), []
    document = imported(client, pid, '温度超过阈值必须复核。')

    def chat(prompt):
        calls.append(prompt)
        return {'answer': '温度超过阈值时必须复核。[S1]', 'model': 'synthetic-chat'}

    monkeypatch.setattr(connectors, 'chat', chat)
    url = f'/api/knowledge/projects/{pid}/answer'
    result = client.post(url, json={'query': 'zzzzzzzz'}).json()
    assert result['answer'] is None and result['model_called'] is False and calls == []
    result = client.post(url, json={'query': '温度阈值'}).json()
    assert result['generated'] and len(calls) == 1 and result['limitations']
    assert result['citations'][0]['document_id'] == document['id']
    assert result['citations'][0]['used'] is True
    assert result['citations'][0]['source_sha256'] == document['source_sha256']
    assert '温度超过阈值必须复核。' in calls[0]
    monkeypatch.setattr(connectors, 'chat', lambda prompt: {'answer': 'Invented [S999]', 'model': 'synthetic'})
    result = client.post(url, json={'query': '温度'}).json()
    assert result['answer'] is None and result['answer_type'] == 'unsupported_generation'


def test_cross_project_and_readonly_denials(tmp_path, embedding_protocol_mock):
    with TestClient(app_at(tmp_path)) as client:
        first, second = project(client), project(client, 'other')
        document = imported(client, first, 'alpha')
    identities = {'developer-token-12345': {'user': 'writer', 'role': 'developer', 'projects': [second]},
                  'auditor-token-123456': {'user': 'reader', 'role': 'auditor', 'projects': [first]}}
    with TestClient(app_at(tmp_path, identities)) as client:
        client.headers['Authorization'] = 'Bearer developer-token-12345'
        for path in [f'/api/knowledge/documents/{document["id"]}', f'/api/knowledge/documents/{document["id"]}/export',
                     f'/api/knowledge/documents/{document["id"]}/chunks', f'/api/knowledge/projects/{first}/documents',
                     f'/api/knowledge/projects/{first}/index']:
            assert client.get(path).status_code == 403
        assert client.post(f'/api/knowledge/projects/{first}/answer', json={'query': 'alpha'}).status_code == 403
        assert client.post(f'/api/knowledge/documents/{document["id"]}/versions', json={
            'name': 'bad', 'text': 'bad', 'expected_revision': 1}).status_code == 403
        assert client.post(f'/api/knowledge/projects/{second}/answer', json={'query': 'alpha'}).json()['answer_type'] == 'no_evidence'
        client.headers['Authorization'] = 'Bearer auditor-token-123456'
        assert client.get(f'/api/knowledge/documents/{document["id"]}').status_code == 200
        assert client.post(f'/api/knowledge/projects/{first}/index/build').status_code == 403
        assert client.post(f'/api/knowledge/projects/{first}/import', files={'file': ('a.txt', b'alpha')}).status_code == 403
        assert client.post(f'/api/knowledge/documents/{document["id"]}/archive', json={
            'archived': True, 'confirmed': True, 'expected_revision': 0}).status_code == 403


def test_failed_import_and_index_quota_leave_no_partial_records(client, embedding_protocol_mock, monkeypatch):
    pid = project(client, quota_bytes=1024)
    response = client.post(f'/api/knowledge/projects/{pid}/import', files={'file': ('a.txt', b'x' * 800)})
    assert response.status_code == 400
    assert client.get(f'/api/knowledge/projects/{pid}/documents').json()['documents'] == []
    document = imported(client, project(client), 'alpha')
    pid = document['project_id']
    service = client.app.state.service
    used = sum(item.get('size_bytes', 0) for item in service.store.list(project_id=pid))
    service.store.update(pid, {'quota_bytes': used + 25}, 'test')
    assert client.post(f'/api/knowledge/projects/{pid}/index/build').status_code == 400
    assert service.store.list('knowledge_embedding', pid) == []
    assert service.store.list('knowledge_index', pid) == []


def test_changed_documents_during_embedding_cannot_publish_stale_index(client, embedding_protocol_mock, monkeypatch):
    from ard import connectors
    pid = project(client)
    document = imported(client, pid, 'alpha')
    original = connectors.embeddings

    def archive_during_embedding(texts):
        client.post(f'/api/knowledge/documents/{document["id"]}/archive', json={
            'archived': True, 'confirmed': True, 'expected_revision': 0})
        return original(texts)

    monkeypatch.setattr(connectors, 'embeddings', archive_during_embedding)
    response = client.post(f'/api/knowledge/projects/{pid}/index/build')
    assert response.status_code == 409
    assert client.app.state.service.store.list('knowledge_index', pid) == []


def test_index_cache_is_scoped_to_project(client, embedding_protocol_mock):
    _, calls = embedding_protocol_mock
    first, second = project(client), project(client, 'second')
    imported(client, first, 'alpha')
    imported(client, second, 'alpha')
    assert client.post(f'/api/knowledge/projects/{first}/index/build').status_code == 200
    assert client.post(f'/api/projects/{second}/search', json={'query': 'alpha', 'mode': 'semantic'}).status_code == 400
    assert client.post(f'/api/knowledge/projects/{second}/index/build').json()['embedded_chunks'] == 1
    assert calls == [['alpha'], ['alpha']]


@pytest.mark.parametrize('vector', [[[float('nan'), 1]], [1, 2], [[1, 2], [1, 2]]])
def test_invalid_embedding_values_do_not_publish_an_index(client, embedding_protocol_mock, monkeypatch, vector):
    from ard import connectors
    pid = project(client)
    imported(client, pid, 'alpha')
    monkeypatch.setattr(connectors, 'embeddings', lambda texts: vector)
    response = client.post(f'/api/knowledge/projects/{pid}/index/build')
    assert response.status_code == 400
    assert client.app.state.service.store.list('knowledge_embedding', pid) == []
    assert client.app.state.service.store.list('knowledge_index', pid) == []


def test_archive_during_generation_blocks_stale_answer(client, monkeypatch):
    from ard import connectors
    pid = project(client)
    document = imported(client, pid, '压力超过阈值需要复核。')

    def chat(prompt):
        client.post(f'/api/knowledge/documents/{document["id"]}/archive', json={
            'archived': True, 'confirmed': True, 'expected_revision': 0})
        return {'answer': '需要复核。[S1]', 'model': 'synthetic-protocol-mock'}

    monkeypatch.setattr(connectors, 'chat', chat)
    response = client.post(f'/api/knowledge/projects/{pid}/answer', json={'query': '压力阈值'})
    assert response.status_code == 409
