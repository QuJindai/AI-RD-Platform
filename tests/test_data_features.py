"""Content, scope, immutability and hostile-input regression tests for data features."""
import io
import json
import shutil
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient

from ard.api import create_app
from ard.engines.data import compare_distributions, parse_table, partition_rows
from ard.features.data import install, require_active


IDENTITIES = {
    'creator-token-123456': {'user': 'manager', 'role': 'developer', 'projects': ['*']},
    'annotator-token-1234': {'user': 'alice', 'role': 'developer', 'projects': ['*']},
    'annotator-token-5678': {'user': 'bob', 'role': 'developer', 'projects': ['*']},
    'reviewer-token-12345': {'user': 'reviewer', 'role': 'reviewer', 'projects': ['*']},
    'auditor-token-123456': {'user': 'auditor', 'role': 'auditor', 'projects': ['*']},
    'outsider-token-12345': {'user': 'outsider', 'role': 'developer', 'projects': []},
    'admin-token-12345678': {'user': 'manager', 'role': 'admin', 'projects': ['*']},
    'alice-admin-token-123': {'user': 'alice', 'role': 'admin', 'projects': ['*']},
}


def login(c, token='creator-token-123456'):
    c.headers['Authorization'] = 'Bearer ' + token


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, identities=IDENTITIES)
    install(app)
    install(app)
    with TestClient(app) as c:
        login(c)
        yield c


def setup_data(c, rows=None):
    p = c.post('/api/projects', json={'name': '合成数据验证'}).json()
    d = c.post(f'/api/projects/{p["id"]}/datasets', json={'name': 'synthetic', 'rows': rows or [
        {'text': '好产品', 'class': 'good', 'x': 1}, {'text': '坏产品', 'class': 'bad', 'x': 2}], 'tags': ['synthetic']}).json()
    return p, d


def test_metadata_catalog_archive_and_persistence(client):
    p, d = setup_data(client)
    url = f'/api/assets/{d["id"]}'
    changed = client.post(url + '/metadata-version', json={'name': '新名称', 'tags': ['gold'], 'description': '中文说明',
        'version_label': 'release-1', 'expected_revision': 1})
    assert changed.status_code == 201, changed.text
    child = changed.json()
    assert child['sha256'] == d['sha256'] and child['parents'] == [d['id']]
    assert client.get(url).json()['name'] == 'synthetic'
    catalogue = f'/api/projects/{p["id"]}/data-catalog'
    assert client.get(catalogue, params={'q': 'release-1', 'tag': 'gold'}).json()[0]['id'] == child['id']
    lineage = client.get(catalogue, params={'lineage_id': d['id']}).json()
    assert len(lineage) == 2 and next(r for r in lineage if r['id'] == child['id'])['lineage']['depth'] == 2
    assert client.post(url + '/archive', json={'archived': True, 'confirm': 'yes', 'expected_revision': 0}).status_code == 400
    result = client.post(url + '/archive', json={'archived': True, 'confirm': d['id'], 'expected_revision': 0, 'reason': '历史版本'})
    assert result.status_code == 201, result.text
    assert len(client.get(catalogue).json()) == 1
    assert len(client.get(catalogue, params={'include_archived': True}).json()) == 2
    assert client.get(url + '/export').content == client.app.state.service.store.read_blob(d['sha256'])
    assert client.post(url + '/metadata-version', json={'name': 'blocked', 'expected_revision': 1}).status_code == 409
    assert client.post(url + '/archive', json={'archived': False, 'confirm': d['id'], 'expected_revision': 0}).status_code == 409
    assert client.post(url + '/archive', json={'archived': False, 'confirm': d['id'], 'expected_revision': 1}).status_code == 201
    assert require_active(client.app.state.service, d)['id'] == d['id']
    assert client.get('/api/audit/verify').json()['valid']


@pytest.mark.parametrize('kind,field', [('job','payload'), ('model','dataset_id'), ('workflow','nodes'), ('approval','asset_id'),
                                      ('model_evaluation','dataset_id')])
def test_archive_denies_live_references(client, kind, field):
    p, d = setup_data(client)
    value = {'asset_id': d['id']} if field == 'payload' else [{'asset_id': d['id']}] if field == 'nodes' else d['id']
    client.app.state.service.store.create(kind, p['id'], {field: value, 'status': 'PENDING'}, 'manager')
    response = client.post(f'/api/assets/{d["id"]}/archive', json={'archived': True, 'confirm': d['id'], 'expected_revision': 0})
    assert response.status_code == 409, response.text
    assert response.json()['detail']['references'][0]['kind'] == kind


def test_catalog_partitions_and_comparisons_scope_and_atomicity(client):
    p, d = setup_data(client)
    p2, d2 = setup_data(client)
    result = client.post(f'/api/assets/{d["id"]}/partition', json={'column': 'class'})
    assert result.status_code == 201 and [x['row_count'] for x in result.json()] == [1, 1]
    invalid = client.post(f'/api/assets/{d["id"]}/partition', json={'groups': [{'name': 'A', 'row_indexes': [0]}, {'name': 'B', 'row_indexes': [0]}]})
    assert invalid.status_code == 400
    manual = client.post(f'/api/assets/{d["id"]}/partition', json={'groups': [{'name': 'A', 'row_indexes': [1]}, {'name': 'B', 'row_indexes': [0]}]})
    assert manual.status_code == 201
    assert client.get(f'/api/assets/{manual.json()[0]["id"]}/rows').json()['rows'][0]['text'] == '坏产品'
    assert client.post(f'/api/projects/{p["id"]}/data-distributions', json={'left_id': d['id'], 'right_id': d2['id'], 'columns': ['x']}).status_code == 400
    assert client.get(f'/api/projects/{p["id"]}/data-catalog', params={'lineage_id': d2['id']}).status_code == 400
    login(client, 'auditor-token-123456')
    assert client.post(f'/api/assets/{d["id"]}/partition', json={'column': 'class'}).status_code == 403
    login(client, 'outsider-token-12345')
    assert client.get(f'/api/projects/{p["id"]}/data-catalog').status_code == 403
    assert client.get(f'/api/assets/{d["id"]}/references').status_code == 403


def test_distribution_exact_values_and_partition_validation():
    result = compare_distributions([{'x': 1}, {'x': 1}], [{'x': 1}, {'x': 3}], ['x'])['columns'][0]
    assert result['total_variation'] == .5 and result['right']['numeric']['mean'] == 2
    assert result['left']['numeric']['stddev'] == 0
    with pytest.raises(ValueError):
        partition_rows([{'x': 1}, {'x': 2}], groups=[{'name': 'A', 'row_indexes': [True]}, {'name': 'B', 'row_indexes': [0]}])


@pytest.mark.parametrize('suffix,content,expected', [
    ('.yaml', '- name: Ada\n  score: 3\n', [{'name': 'Ada', 'score': 3}]),
    ('.xml', '<rows><row id="a"><name>Ada</name><score>3</score></row></rows>', [{'@id': 'a', 'name': 'Ada', 'score': '3'}]),
    ('.html', '<table><tr><th>name</th><th>score</th></tr><tr><td><b>Ada</b><script>evil()</script></td><td>3</td></tr></table>', [{'name': 'Ada', 'score': '3'}]),
])
def test_real_semistructured_import(suffix, content, expected):
    assert parse_table('test' + suffix, content.encode()) == expected


def test_xlsx_and_parquet_import_real_values():
    import openpyxl
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import date
    out = io.BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active.append(['name', 'score', 'at'])
    workbook.active.append(['Ada', 3, date(2026, 1, 2)])
    workbook.save(out)
    assert parse_table('table.xlsx', out.getvalue()) == [{'name': 'Ada', 'score': 3, 'at': '2026-01-02T00:00:00'}]
    workbook.active['B2'] = '=1+2'
    out = io.BytesIO()
    workbook.save(out)
    with pytest.raises(ValueError, match='formulas'):
        parse_table('table.xlsx', out.getvalue())
    out = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([{'name': 'Ada', 'score': 3}]), out)
    assert parse_table('table.parquet', out.getvalue()) == [{'name': 'Ada', 'score': 3}]


@pytest.mark.parametrize('name,raw', [
    ('a.yaml', b'- x: &a [1]\n- x: *a'), ('a.yaml', b'- x: !!python/object/apply:os.system [echo]'),
    ('a.yaml', b'- x: 1\n  x: 2'), ('a.yaml', b'- x: .nan'),
    ('a.xml', b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x><r><v>&y;</v></r></x>'),
    ('a.xml', b'<x><r><v><nested/></v></r></x>'),
    ('a.html', b'<table><tr><th colspan="2">x</th></tr></table>'), ('a.parquet', b'not parquet'),
])
def test_import_rejects_unsafe_or_fake_inputs(name, raw):
    with pytest.raises(ValueError):
        parse_table(name, raw)


def test_image_import_preview_and_scope(client):
    from PIL import Image
    p, _ = setup_data(client)
    image = io.BytesIO()
    Image.new('RGB', (24, 12), 'red').save(image, 'PNG')
    response = client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('synthetic.png', image.getvalue(), 'image/png')})
    assert response.status_code == 201, response.text
    media, dataset = response.json()['media'], response.json()['dataset']
    assert media['width'] == 24 and media['integrity'] == 'all_frames_decoded'
    assert client.get(f'/api/media/{media["id"]}/content').content == image.getvalue()
    preview = client.get(f'/api/media/{media["id"]}/preview')
    assert preview.headers['content-type'] == 'image/png'
    assert Image.open(io.BytesIO(preview.content)).size == (24, 12)
    assert client.get(f'/api/assets/{dataset["id"]}/rows').json()['rows'][0]['media_id'] == media['id']
    assert client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('../escape.png', image.getvalue())}).status_code == 400
    assert client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('fake.png', b'<svg/>')}).status_code == 400
    original = io.BytesIO()
    image_with_metadata = Image.new('RGB', (24, 12), 'red')
    exif = Image.Exif()
    exif[315] = 'synthetic metadata'
    exif[274] = 6
    image_with_metadata.save(original, 'JPEG', exif=exif)
    result = client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('synthetic.jpg', original.getvalue())})
    assert result.status_code == 201, result.text
    rendered = Image.open(io.BytesIO(client.get(f'/api/media/{result.json()["media"]["id"]}/preview').content))
    assert rendered.size == (12, 24) and not rendered.getexif()
    login(client, 'outsider-token-12345')
    assert client.get(f'/api/media/{media["id"]}/preview').status_code == 403


@pytest.mark.parametrize('suffix', ['.mp4', '.avi', '.mkv'])
def test_video_probe_real_container(client, tmp_path, suffix):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg or not shutil.which('ffprobe'):
        pytest.skip('system FFmpeg tools unavailable')
    path = tmp_path / ('synthetic' + suffix)
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x24:d=0.2',
                    '-c:v', 'mpeg4', '-y', str(path)], check=True, timeout=10)
    p, _ = setup_data(client)
    response = client.post(f'/api/projects/{p["id"]}/media-import', files={'file': (path.name, path.read_bytes(), 'application/octet-stream')})
    assert response.status_code == 201, response.text
    media = response.json()['media']
    assert media['width'] == 32 and media['height'] == 24 and media['duration_seconds'] > 0
    assert media['integrity'] == 'container_and_stream_probe_only'


def create_task(c, p, d, kind='text_classification', **extra):
    response = c.post(f'/api/projects/{p["id"]}/annotation-tasks', json={'dataset_id': d['id'], 'name': '合成独立标注',
        'task_type': kind, 'text_column': 'text', 'labels': ['good', 'bad'], 'annotators': ['alice', 'bob'], **extra})
    assert response.status_code == 201, response.text
    return response.json()


def submit(c, task, token, values):
    login(c, token)
    detail = c.get(f'/api/annotation-tasks/{task["id"]}').json()
    return c.post(f'/api/annotation-tasks/{task["id"]}/submit', json={'expected_revision': detail['revision'],
        'items': [{'row_index': i, 'value': value} for i, value in enumerate(values)]})


def test_independent_blinded_classification_review_export_and_restart(client, tmp_path):
    p, d = setup_data(client)
    task = create_task(client, p, d)
    response = submit(client, task, 'annotator-token-1234', ['good', 'bad'])
    assert response.status_code == 201, response.text
    login(client, 'annotator-token-5678')
    before = client.get(f'/api/annotation-tasks/{task["id"]}').json()
    assert before['own_submission'] is None and 'submissions' not in before and 'agreement' not in before
    assert submit(client, task, 'annotator-token-5678', ['bad', 'bad']).status_code == 201
    assert submit(client, task, 'annotator-token-5678', ['good', 'good']).status_code == 409
    login(client, 'reviewer-token-12345')
    detail = client.get(f'/api/annotation-tasks/{task["id"]}').json()
    assert detail['agreement']['pairwise_disagreement_rate'] == .5
    assert detail['agreement']['disagreement_rows'] == [0]
    review_url = f'/api/annotation-tasks/{task["id"]}/review'
    body = {'decision': 'accept', 'expected_revision': detail['revision']}
    assert client.post(review_url, json=body).status_code == 400
    final_items = [{'row_index': 0, 'value': 'good'}, {'row_index': 1, 'value': 'bad'}]
    accepted = client.post(review_url, json={**body, 'items': final_items})
    assert accepted.status_code == 201, accepted.text
    assert client.post(review_url, json={**body, 'items': final_items}).status_code == 409
    login(client)
    exported = client.post(f'/api/annotation-tasks/{task["id"]}/export', json={'expected_revision': accepted.json()['revision'], 'label_column': 'label'})
    assert exported.status_code == 201, exported.text
    labelled = exported.json()['dataset']
    rows = client.get(f'/api/assets/{labelled["id"]}/export').json()
    assert rows[0]['label'] == 'good' and rows[0]['_source_row'] == 0 and labelled['parents'] == [d['id']]
    assert client.get(f'/api/assets/{d["id"]}/export').json()[0].get('label') is None
    assert client.get('/api/audit/verify').json()['valid']
    # A separately initialized service reads persisted immutable label payloads.
    from ard.service import Service
    restarted = Service(client.app.state.service.store.root)
    try:
        assert restarted.rows(labelled['id']) == rows
    finally:
        restarted.close()


@pytest.mark.parametrize('kind,values,expected', [
    ('row_classification', ['good', 'bad'], 'good'),
    ('span', [[{'start': 0, 'end': 1, 'label': 'good'}], []], [{'start': 0, 'end': 1, 'label': 'good', 'text': '好'}]),
    ('qa', [{'start': 1, 'end': 3}, {'unanswerable': True}], {'start': 1, 'end': 3, 'question': '提到什么？', 'answer': '产品'}),
])
def test_annotation_types_real_export(client, kind, values, expected):
    p, d = setup_data(client)
    task = create_task(client, p, d, kind, question='提到什么？' if kind == 'qa' else '')
    assert submit(client, task, 'annotator-token-1234', values).status_code == 201
    response = submit(client, task, 'annotator-token-5678', values)
    assert response.status_code == 201, response.text
    login(client, 'reviewer-token-12345')
    accepted = client.post(f'/api/annotation-tasks/{task["id"]}/review', json={'expected_revision': response.json()['revision'], 'decision': 'accept'})
    assert accepted.status_code == 201, accepted.text
    login(client)
    exported = client.post(f'/api/annotation-tasks/{task["id"]}/export', json={'expected_revision': accepted.json()['revision']})
    assert exported.status_code == 201, exported.text
    assert client.get(f'/api/assets/{exported.json()["dataset"]["id"]}/rows').json()['rows'][0]['_label'] == expected


def test_annotation_assignment_offsets_permissions_and_quota(client):
    p, d = setup_data(client)
    task = create_task(client, p, d, 'span')
    invalid = submit(client, task, 'annotator-token-1234', [[{'start': 0, 'end': 100, 'label': 'good'}], []])
    assert invalid.status_code == 400
    login(client)
    assert client.post(f'/api/annotation-tasks/{task["id"]}/submit', json={'expected_revision': 1, 'items': [{'row_index': 0, 'value': []}, {'row_index': 1, 'value': []}]}).status_code == 403
    assert client.post(f'/api/assets/{d["id"]}/archive', json={'archived': True, 'confirm': d['id'], 'expected_revision': 0}).status_code == 409
    assert submit(client, task, 'annotator-token-1234', [[], []]).status_code == 201
    response = submit(client, task, 'annotator-token-5678', [[], []])
    login(client, 'alice-admin-token-123')
    assert client.post(f'/api/annotation-tasks/{task["id"]}/review', json={'expected_revision': response.json()['revision'], 'decision': 'accept'}).status_code == 403
    login(client, 'admin-token-12345678')
    assert client.post(f'/api/annotation-tasks/{task["id"]}/review', json={'expected_revision': response.json()['revision'], 'decision': 'accept'}).status_code == 403
    login(client, 'outsider-token-12345')
    assert client.get(f'/api/annotation-tasks/{task["id"]}/rows').status_code == 403


def test_quota_failure_rolls_back_versions_media_and_annotations(client):
    from PIL import Image
    p = client.post('/api/projects', json={'name': 'quota synthetic', 'quota_bytes': 1024}).json()
    source = client.post(f'/api/projects/{p["id"]}/datasets', json={'name': 'near quota',
        'rows': [{'text': 'a' * 400, 'class': 'a'}, {'text': 'b' * 400, 'class': 'b'}]}).json()
    assert 'id' in source
    store = client.app.state.service.store
    initial = len(store.list('dataset', p['id']))
    assert client.post(f'/api/assets/{source["id"]}/metadata-version', json={'name': 'too much', 'expected_revision': 1}).status_code == 400
    assert client.post(f'/api/assets/{source["id"]}/partition', json={'column': 'class'}).status_code == 400
    assert len(store.list('dataset', p['id'])) == initial
    image = io.BytesIO()
    Image.new('RGB', (16, 16), 'blue').save(image, format='BMP')
    before_objects = set(store.objects.iterdir())
    assert client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('synthetic.bmp', image.getvalue())}).status_code == 400
    assert store.list('media', p['id']) == [] and set(store.objects.iterdir()) == before_objects
    task = create_task(client, p, source, labels=['x' * 100, 'y' * 100])
    response = submit(client, task, 'annotator-token-1234', ['x' * 100, 'y' * 100])
    assert response.status_code == 400, response.text
    assert store.list('annotation_submission', p['id']) == []
    assert client.get(f'/api/annotation-tasks/{task["id"]}').json()['revision'] == 1
    assert store.verify_audit()['valid']


def test_parser_depth_dimension_and_archive_limits(monkeypatch):
    import openpyxl
    import ard.engines.data as engine
    nested = '0'
    for _ in range(25):
        nested = '[' + nested + ']'
    with pytest.raises(ValueError, match='nesting'):
        parse_table('nested.json', ('[{"x":' + nested + '}]').encode())
    workbook = openpyxl.Workbook()
    workbook.active['A1'] = 'x'
    workbook.active['GS2'] = 'too many columns'
    output = io.BytesIO()
    workbook.save(output)
    with pytest.raises(ValueError, match='row/column'):
        parse_table('wide.xlsx', output.getvalue())
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('C:/outside.csv', 'x\n1')
    with pytest.raises(ValueError, match='unsafe path'):
        engine.parse_archive(output.getvalue())
    monkeypatch.setattr(engine, 'MAX_ARCHIVE_BYTES', 8)
    with pytest.raises(ValueError, match='input limit'):
        parse_table('large.json', b'[{"x":123456}]')


def test_media_frame_limit_and_missing_video_probe(client, monkeypatch):
    from PIL import Image
    import ard.features.data as feature
    p, _ = setup_data(client)
    frames = [Image.new('RGB', (2, 2), (n, 0, 0)) for n in range(101)]
    output = io.BytesIO()
    frames[0].save(output, 'TIFF', save_all=True, append_images=frames[1:])
    response = client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('too-many.tif', output.getvalue())})
    assert response.status_code == 400, response.text
    monkeypatch.setattr(feature.shutil, 'which', lambda _: None)
    response = client.post(f'/api/projects/{p["id"]}/media-import', files={'file': ('synthetic.mp4', b'\x00\x00\x00\x18ftypisom0000')})
    assert response.status_code == 503 and 'ffprobe' in response.text


def test_annotation_strict_row_indexes_qa_offsets_and_reserved_export_column(client):
    p, d = setup_data(client, [{'text': 'A😀中B'}, {'text': '第二行'}])
    response = client.post(f'/api/projects/{p["id"]}/annotation-tasks', json={'dataset_id': d['id'], 'name': 'bad indexes',
        'task_type': 'qa', 'text_column': 'text', 'question': 'what', 'annotators': ['alice'], 'row_indexes': [True]})
    assert response.status_code == 422
    task = create_task(client, p, d, 'qa', question='选择一个字符')
    assert submit(client, task, 'annotator-token-1234', [{'start': True, 'end': 2}, {'unanswerable': True}]).status_code == 400
    assert submit(client, task, 'annotator-token-1234', [{'start': 1, 'end': 2}, {'unanswerable': 1}]).status_code == 400
    values = [{'start': 1, 'end': 2}, {'unanswerable': True}]
    assert submit(client, task, 'annotator-token-1234', values).status_code == 201
    response = submit(client, task, 'annotator-token-5678', values)
    login(client, 'reviewer-token-12345')
    accepted = client.post(f'/api/annotation-tasks/{task["id"]}/review', json={'expected_revision': response.json()['revision'], 'decision': 'accept'}).json()
    login(client)
    url = f'/api/annotation-tasks/{task["id"]}/export'
    assert client.post(url, json={'expected_revision': accepted['revision'], 'label_column': '_source_row'}).status_code == 400
    exported = client.post(url, json={'expected_revision': accepted['revision']})
    assert exported.status_code == 201, exported.text
    assert client.get(f'/api/assets/{exported.json()["dataset"]["id"]}/rows').json()['rows'][0]['_label']['answer'] == '😀'


def test_long_version_lineage_avoids_recursion_limits():
    from ard.features.data import _lineages
    records = [{'id': str(i), 'parents': [str(i - 1)] if i else []} for i in range(1500)]
    result = _lineages(list(reversed(records)))
    assert result['1499']['depth'] == 1500 and result['1499']['root_ids'] == ['0']


def test_distribution_finite_extremes():
    import math
    result = compare_distributions([{'x': 1.7976931348623157e308}] * 7, [{'x': -1.7976931348623157e308}] * 7, ['x'])
    assert math.isfinite(result['columns'][0]['left']['numeric']['mean'])
    assert result['columns'][0]['left']['numeric']['stddev'] == 0
    with pytest.raises(ValueError, match='floating-point range'):
        compare_distributions([{'x': 10 ** 500}], [{'x': 1}], ['x'])
