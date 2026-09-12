"""Versioned data catalogue, safe media and independently reviewed annotation."""
from __future__ import annotations

import copy
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from collections import deque
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from ard.engines.data import compare_distributions, partition_rows
from ard.features.common import Input, Svc, User, install_once
from ard.store import encode, now

router = APIRouter(tags=['data features'])
_RESERVED = {'id', 'kind', 'project_id', 'revision', 'created_at', 'updated_at'}
MAX_UPLOAD = 20 * 1024 * 1024
MAX_ANNOTATION_ROWS = 5000
RowIndex = Annotated[int, Field(strict=True, ge=0)]


def _archive_state(s, asset):
    events = [r for r in s.store.list('dataset_archive_event', asset['project_id']) if r['asset_id'] == asset['id']]
    latest = max(events, key=lambda r: r['event_version'], default=None)
    return {'archived': latest['archived'] if latest else False,
            'revision': latest['event_version'] if latest else 0,
            'reason': latest['reason'] if latest else ''}


def is_archived(s, asset):
    """Read archival state for an already-authorized dataset record."""
    return _archive_state(s, asset)['archived']


def require_active(s, asset):
    """Guard new operations while retaining historical reads and original blobs."""
    if is_archived(s, asset):
        raise HTTPException(409, '数据版本已归档，请先恢复后再操作')
    return asset


def _dataset(s, asset_id, user, active=False):
    asset = s.record(asset_id, 'dataset', user)
    return require_active(s, asset) if active else asset


def _reference(value, asset_id):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(v for k, v in item.items() if k not in {'id', 'name', 'description', 'comment', 'logs'})
        elif isinstance(item, list):
            pending.extend(item)
        elif item == asset_id:
            return True
    return False


def dataset_references(s, asset, user):
    result = []
    for kind in ('job', 'model', 'workflow', 'approval', 'annotation_task', 'experiment', 'experiment_baseline', 'model_baseline', 'model_evaluation', 'model_comparison'):
        for record in s.list(kind, asset['project_id'], user):
            if kind == 'approval' and record.get('status') != 'PENDING':
                continue
            if kind == 'annotation_task' and record.get('status') in {'EXPORTED', 'REJECTED'}:
                continue
            if _reference(record, asset['id']):
                result.append({'id': record['id'], 'kind': kind, 'name': record.get('name', ''), 'status': record.get('status')})
    return result


def _lineages(records):
    by_id = {r['id']: r for r in records}
    memo, children, incoming = {}, {r['id']: [] for r in records}, {}
    for record in records:
        parents = {p for p in record.get('parents', []) if p in by_id}
        incoming[record['id']] = len(parents)
        for parent in parents:
            children[parent].append(record['id'])
    ready = deque(asset_id for asset_id, degree in incoming.items() if degree == 0)
    while ready:
        asset_id = ready.popleft()
        parents = by_id[asset_id].get('parents', [])
        ancestors = [memo[p] for p in parents if p in memo]
        roots = sorted({root for ancestor in ancestors for root in ancestor['root_ids']}) if ancestors else [asset_id]
        memo[asset_id] = {'depth': 1 + max((a['depth'] for a in ancestors), default=0), 'root_ids': roots,
                          'parents': parents, 'children': children[asset_id]}
        for child in children[asset_id]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if len(memo) != len(records):
        raise ValueError('数据版本谱系包含循环引用')
    return memo


@router.get('/api/projects/{pid}/data-catalog')
def catalog(pid: str, s: Svc, user: User, q: str = Query(default='', max_length=200),
            tag: str = Query(default='', max_length=80), lineage_id: str | None = None,
            include_archived: bool = False):
    records = s.list('dataset', pid, user)
    lineages = _lineages(records)
    if lineage_id:
        selected = _dataset(s, lineage_id, user)
        if selected['project_id'] != pid:
            raise ValueError('版本谱系必须属于当前项目')
        roots = set(lineages[lineage_id]['root_ids'])
    else:
        roots = set()
    needle = q.casefold().strip()
    events = s.list('dataset_archive_event', pid, user)
    states = {}
    for event in events:
        previous = states.get(event['asset_id'])
        if previous is None or previous['event_version'] < event['event_version']:
            states[event['asset_id']] = event
    output = []
    for record in records:
        event = states.get(record['id'])
        archived = bool(event and event['archived'])
        if archived and not include_archived:
            continue
        if tag and tag not in record.get('tags', []):
            continue
        if roots and not roots.intersection(lineages[record['id']]['root_ids']):
            continue
        searchable = ' '.join([record['id'], record['name'], record.get('description', ''), record.get('version_label', ''), f"v{lineages[record['id']]['depth']}",
                               *record.get('tags', []), *record.get('parents', [])]).casefold()
        if needle and needle not in searchable:
            continue
        output.append({**record, 'lineage': lineages[record['id']], 'archive': {
            'archived': archived, 'revision': event['event_version'] if event else 0,
            'reason': event['reason'] if event else ''}})
    return output


class MetadataInput(Input):
    name: str = Field(min_length=1, max_length=150, pattern=r'.*\S.*')
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default='', max_length=2000)
    version_label: str = Field(default='', max_length=80)
    expected_revision: int = Field(ge=1)


@router.post('/api/assets/{asset_id}/metadata-version', status_code=201)
def metadata_version(asset_id: str, body: MetadataInput, s: Svc, user: User):
    user.allow_write()
    if len(set(body.tags)) != len(body.tags) or any(not t.strip() or len(t) > 80 for t in body.tags):
        raise ValueError('标签不能重复、为空或超过80字符')
    with s.store.transaction():
        asset = _dataset(s, asset_id, user, active=True)
        if asset['revision'] != body.expected_revision:
            raise HTTPException(409, 'revision conflict; 请重新加载数据版本')
        data = {k: copy.deepcopy(v) for k, v in asset.items() if k not in _RESERVED}
        # Count each logical immutable version against quota, as the core service does.
        s._quota(asset['project_id'], data['size_bytes'])
        data.update(body.model_dump(exclude={'expected_revision'}), parents=[asset_id], creator=user.user)
        return s.store.create('dataset', asset['project_id'], data, user.user)


class PartitionGroup(Input):
    name: str = Field(min_length=1, max_length=100)
    row_indexes: list[RowIndex] = Field(min_length=1, max_length=50000)


class PartitionInput(Input):
    column: str | None = Field(default=None, max_length=200)
    groups: list[PartitionGroup] | None = Field(default=None, min_length=2, max_length=30)


@router.post('/api/assets/{asset_id}/partition', status_code=201)
def partition(asset_id: str, body: PartitionInput, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        asset = _dataset(s, asset_id, user, active=True)
        parts = partition_rows(s.rows(asset_id), column=body.column,
                               groups=[g.model_dump() for g in body.groups] if body.groups is not None else None)
        s._quota(asset['project_id'], sum(len(encode(rows)) for _, rows in parts))
        return [s.add_dataset(asset['project_id'], (asset['name'] + ' · ' + name)[:150], rows, asset['tags'], user, [asset_id])
                for name, rows in parts]


class DistributionInput(Input):
    left_id: str
    right_id: str
    columns: list[str] = Field(min_length=1, max_length=20)


@router.post('/api/projects/{pid}/data-distributions')
def distributions(pid: str, body: DistributionInput, s: Svc, user: User):
    s.project(pid, user)
    assets = [_dataset(s, asset_id, user) for asset_id in (body.left_id, body.right_id)]
    if any(asset['project_id'] != pid for asset in assets):
        raise ValueError('分布比较不能跨项目')
    return compare_distributions(s.rows(body.left_id), s.rows(body.right_id), body.columns)


class ArchiveInput(Input):
    archived: bool
    confirm: str
    expected_revision: int = Field(ge=0)
    reason: str = Field(default='', max_length=1000)


@router.get('/api/assets/{asset_id}/references')
def references(asset_id: str, s: Svc, user: User):
    asset = _dataset(s, asset_id, user)
    return {'asset_id': asset_id, 'archive': _archive_state(s, asset), 'references': dataset_references(s, asset, user)}


@router.post('/api/assets/{asset_id}/archive', status_code=201)
def archive(asset_id: str, body: ArchiveInput, s: Svc, user: User):
    user.allow_write()
    if body.confirm != asset_id:
        raise ValueError('请明确确认完整的数据版本 ID')
    with s.store.transaction():
        asset = _dataset(s, asset_id, user)
        state = _archive_state(s, asset)
        if state['revision'] != body.expected_revision or state['archived'] == body.archived:
            raise HTTPException(409, 'revision conflict; 归档状态已变化')
        refs = dataset_references(s, asset, user) if body.archived else []
        if refs:
            raise HTTPException(409, {'message': '数据被任务、模型、工作流、待审批事项或标注任务引用', 'references': refs})
        return s.store.create('dataset_archive_event', asset['project_id'], {'asset_id': asset_id,
            'archived': body.archived, 'event_version': state['revision'] + 1, 'reason': body.reason, 'creator': user.user}, user.user)


def _inspect_image(raw):
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {'JPEG', 'PNG', 'BMP', 'TIFF', 'GIF', 'WEBP'}:
                raise ValueError('不支持此图片编码')
            width, height = source.size
            if width * height > 20_000_000:
                raise ValueError('图片超过2000万像素限制')
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            pixels, frames = 0, 0
            for index in range(101):
                try:
                    source.seek(index)
                except EOFError:
                    break
                if index == 100 or source.width * source.height > 20_000_000:
                    raise ValueError('图片超过100帧或每帧2000万像素限制')
                pixels += source.width * source.height
                if pixels > 64_000_000:
                    raise ValueError('图片解码总像素超过6400万限制')
                source.load()
                frames += 1
            source.seek(0)
            preview = ImageOps.exif_transpose(source).convert('RGB')
            preview.thumbnail((1024, 1024))
            preview.info.clear()
            output = io.BytesIO()
            preview.save(output, format='PNG')
            mime = Image.MIME.get(source.format, 'application/octet-stream')
            return {'media_type': 'image', 'mime': mime, 'width': width, 'height': height,
                    'frames': frames, 'encoding': source.format, 'integrity': 'all_frames_decoded'}, output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('图片损坏、编码不支持或解压超过限制') from exc


def _inspect_video(raw, suffix):
    executable = shutil.which('ffprobe')
    if not executable:
        raise HTTPException(503, 'ffprobe 未安装，视频导入不可用')
    signatures = {'.mp4': len(raw) > 12 and raw[4:8] == b'ftyp',
                  '.avi': len(raw) > 12 and raw[:4] == b'RIFF' and raw[8:12] == b'AVI ',
                  '.mkv': raw[:4] == b'\x1aE\xdf\xa3'}
    if not signatures.get(suffix):
        raise ValueError('视频容器签名与扩展名不一致')
    with tempfile.TemporaryDirectory(prefix='ard-media-') as directory:
        path = Path(directory) / ('upload' + suffix)
        path.write_bytes(raw)
        command = [executable, '-v', 'error', '-max_alloc', '33554432', '-protocol_whitelist', 'file,pipe',
                   '-format_whitelist', 'mov,matroska,webm,avi', '-probesize', '5000000', '-analyzeduration', '2000000',
                   '-select_streams', 'v:0', '-show_entries',
                   'stream=codec_type,codec_name,width,height,duration,nb_frames,r_frame_rate:format=duration,format_name,size',
                   '-of', 'json', str(path)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=10, cwd=directory, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('视频探测超过10秒限制') from exc
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('视频容器无法通过 ffprobe 检查')
    try:
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        duration = float(data.get('format', {}).get('duration', stream.get('duration', 0)))
        if width < 1 or height < 1 or width * height > 32_000_000 or not math.isfinite(duration) or not 0 < duration <= 604800:
            raise ValueError('视频尺寸或时长超过限制')
        return {'media_type': 'video', 'mime': {'.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska'}[suffix],
                'width': width, 'height': height, 'duration_seconds': duration, 'encoding': stream.get('codec_name', 'unknown'),
                'frame_rate': stream.get('r_frame_rate'), 'container': data['format']['format_name'],
                'integrity': 'container_and_stream_probe_only'}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError('视频缺少有效的视频流、尺寸或时长') from exc


@router.post('/api/projects/{pid}/media-import', status_code=201)
async def media_import(pid: str, s: Svc, user: User, file: UploadFile = File(...), name: str = Form(default='', max_length=150)):
    user.allow_write()
    s.project(pid, user)
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, '媒体文件超过20MiB限制')
    filename = file.filename or ''
    if not filename or len(filename) > 200 or Path(filename).name != filename or '\\' in filename or any(ord(c) < 32 for c in filename):
        raise ValueError('媒体文件名不允许路径或控制字符')
    suffix = Path(filename).suffix.lower()
    preview = None
    if suffix in {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.gif', '.webp'}:
        metadata, preview = await run_in_threadpool(_inspect_image, raw)
    elif suffix in {'.mp4', '.avi', '.mkv'}:
        metadata = await run_in_threadpool(_inspect_video, raw, suffix)
    else:
        raise ValueError('支持 JPG/PNG/BMP/TIF/GIF/WebP 图片和 MP4/AVI/MKV 视频')
    with s.store.transaction():
        metadata.update(filename=filename, name=name.strip() or filename, creator=user.user,
                        size_bytes=len(raw) + (len(preview) if preview else 0))
        # Reserve both binary and manifest costs before either record is committed.
        manifest = {k: metadata[k] for k in ('filename', 'media_type', 'mime', 'width', 'height', 'encoding', 'integrity')}
        manifest['media_id'] = 'x' * 32
        if metadata.get('duration_seconds') is not None:
            manifest['duration_seconds'] = metadata['duration_seconds']
        s._quota(pid, metadata['size_bytes'] + len(encode([manifest])))
        metadata['sha256'] = s.store.blob(raw)
        if preview:
            metadata['preview_sha256'] = s.store.blob(preview)
        media = s.store.create('media', pid, metadata, user.user)
        manifest['media_id'] = media['id']
        dataset = s.add_dataset(pid, metadata['name'], [manifest], ['media', metadata['media_type']], user)
        return {'media': media, 'dataset': dataset}


@router.get('/api/projects/{pid}/media')
def media_list(pid: str, s: Svc, user: User):
    return s.list('media', pid, user)


@router.get('/api/media/{media_id}')
def media_metadata(media_id: str, s: Svc, user: User):
    return s.record(media_id, 'media', user)


@router.get('/api/media/{media_id}/content')
def media_content(media_id: str, s: Svc, user: User):
    media = s.record(media_id, 'media', user)
    return Response(s.store.read_blob(media['sha256']), media_type=media['mime'], headers={
        'X-Content-Type-Options': 'nosniff', 'Content-Disposition': f'inline; filename="media-{media_id}{Path(media["filename"]).suffix.lower()}"',
        'Cache-Control': 'private, no-store'})


@router.get('/api/media/{media_id}/preview')
def media_preview(media_id: str, s: Svc, user: User):
    media = s.record(media_id, 'media', user)
    if media['media_type'] != 'image':
        raise HTTPException(409, '视频使用原始内容播放，未生成缩略图')
    return Response(s.store.read_blob(media['preview_sha256']), media_type='image/png',
                    headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


class AnnotationInput(Input):
    dataset_id: str
    name: str = Field(min_length=1, max_length=150)
    task_type: Literal['row_classification', 'text_classification', 'span', 'qa']
    text_column: str | None = Field(default=None, max_length=200)
    question: str = Field(default='', max_length=1000)
    labels: list[str] = Field(default_factory=list, max_length=100)
    annotators: list[str] = Field(min_length=1, max_length=10)
    row_indexes: list[RowIndex] | None = Field(default=None, min_length=1, max_length=MAX_ANNOTATION_ROWS)


def _task(s, task_id, user):
    return s.record(task_id, 'annotation_task', user)


def _load_labels(s, record):
    return json.loads(s.store.read_blob(record['sha256']))


def _submissions(s, task, user):
    return [r for r in s.list('annotation_submission', task['project_id'], user) if r['task_id'] == task['id']]


def _agreement(s, task, submissions):
    all_items = [{item['row_index']: item['value'] for item in _load_labels(s, submission)} for submission in submissions]
    complete = len(submissions) == len(task['annotators'])
    compared, differing, disagreements = 0, 0, []
    for index in task['row_indexes']:
        fingerprints = [encode(items[index]) for items in all_items]
        row_pairs, row_different = 0, 0
        for i, left in enumerate(fingerprints):
            for right in fingerprints[i + 1:]:
                row_pairs += 1
                row_different += left != right
        compared += row_pairs
        differing += row_different
        if len(set(fingerprints)) > 1:
            disagreements.append(index)
    return {'complete': complete, 'submitted': len(submissions), 'assigned': len(task['annotators']),
            'compared_pairs': compared, 'disagreeing_pairs': differing,
            'pairwise_disagreement_rate': differing / compared if compared else None,
            'disagreement_rows': disagreements, 'method': 'Exact canonical label equality for each unordered annotator pair and row.'}


def _task_view(s, task, user):
    submissions = _submissions(s, task, user)
    complete = len(submissions) == len(task['annotators'])
    own = next((r for r in submissions if r['creator'] == user.user), None)
    reviewer = user.role in {'admin', 'reviewer'} and user.user not in task.get('submitted_users', [])
    result = {**task, 'submitted_count': len(submissions), 'can_review': reviewer and (user.local or user.user != task['creator']) and task['status'] == 'SUBMITTED',
              'own_submission': _load_labels(s, own) if own else None}
    if complete:
        result['agreement'] = _agreement(s, task, submissions)
    if reviewer and complete:
        result['submissions'] = [{'annotator': r['creator'], 'items': _load_labels(s, r)} for r in submissions]
    if task.get('review_id'):
        review = s.record(task['review_id'], 'annotation_review', user)
        result['review'] = {k: v for k, v in review.items() if k != 'sha256'}
        if review.get('sha256') and (reviewer or task['status'] in {'ACCEPTED', 'EXPORTED'}):
            result['review']['items'] = _load_labels(s, review)
    return result


@router.get('/api/projects/{pid}/annotation-annotators')
def annotation_annotators(pid: str, request: Request, s: Svc, user: User):
    s.project(pid, user)
    identities = request.app.state.security.identities
    if not identities:
        return [{'user': 'local', 'role': 'admin'}]
    available = {(v['user'], v['role']) for v in identities.values()
                 if v['role'] in {'admin', 'developer'} and ('*' in v['projects'] or pid in v['projects'])}
    return [{'user': name, 'role': role} for name, role in sorted(available)]


@router.post('/api/projects/{pid}/annotation-tasks', status_code=201)
def annotation_create(pid: str, body: AnnotationInput, request: Request, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        asset = _dataset(s, body.dataset_id, user, active=True)
        if asset['project_id'] != pid:
            raise ValueError('标注来源必须属于当前项目')
        s.project(pid, user)
        if len(set(body.annotators)) != len(body.annotators):
            raise ValueError('标注账户不能重复')
        available = {item['user'] for item in annotation_annotators(pid, request, s, user)}
        if any(name not in available for name in body.annotators):
            raise ValueError('标注账户必须具有当前项目开发者或管理员权限')
        if len(set(body.labels)) != len(body.labels) or any(not label.strip() or len(label) > 100 for label in body.labels):
            raise ValueError('标签必须唯一、非空且不超过100字符')
        if body.task_type != 'qa' and not body.labels:
            raise ValueError('分类和片段标注必须设置标签')
        rows = s.rows(asset['id'])
        indexes = body.row_indexes if body.row_indexes is not None else list(range(len(rows)))
        if len(indexes) > MAX_ANNOTATION_ROWS or len(set(indexes)) != len(indexes) or any(i < 0 or i >= len(rows) for i in indexes):
            raise ValueError('标注行必须唯一、有效且不超过5000行')
        if body.task_type != 'row_classification':
            if not body.text_column or any(not isinstance(rows[i].get(body.text_column), str) for i in indexes):
                raise ValueError('文本列必须在每个标注行中包含字符串')
        if body.task_type == 'qa' and not body.question.strip():
            raise ValueError('问答标注必须给出共同问题')
        data = body.model_dump()
        data.update(row_indexes=indexes, creator=user.user, status='OPEN', submitted_users=[])
        return s.store.create('annotation_task', pid, data, user.user)


@router.get('/api/projects/{pid}/annotation-tasks')
def annotation_tasks(pid: str, s: Svc, user: User):
    return s.list('annotation_task', pid, user)


@router.get('/api/annotation-tasks/{task_id}')
def annotation_detail(task_id: str, s: Svc, user: User):
    return _task_view(s, _task(s, task_id, user), user)


@router.get('/api/annotation-tasks/{task_id}/rows')
def annotation_rows(task_id: str, s: Svc, user: User, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    task = _task(s, task_id, user)
    _dataset(s, task['dataset_id'], user)
    rows = s.rows(task['dataset_id'])
    indexes = task['row_indexes'][offset:offset + limit]
    return {'total': len(task['row_indexes']), 'rows': [{'row_index': i, 'row': rows[i]} for i in indexes]}


class LabelItem(Input):
    row_index: int = Field(ge=0, strict=True)
    value: Any


class SubmissionInput(Input):
    expected_revision: int = Field(ge=1)
    items: list[LabelItem] = Field(min_length=1, max_length=MAX_ANNOTATION_ROWS)


def _offset(value, length):
    if type(value) is not int or not 0 <= value <= length:
        raise ValueError('文本偏移必须是有效的 Unicode 码点位置')
    return value


def _canonical_labels(task, items, rows):
    by_row = {}
    allowed_indexes = set(task['row_indexes'])
    for item in items:
        index, value = item.row_index, item.value
        if index not in allowed_indexes or index in by_row:
            raise ValueError('提交行必须来自任务且每行恰好一次')
        kind = task['task_type']
        if kind in {'row_classification', 'text_classification'}:
            if not isinstance(value, str) or value not in task['labels']:
                raise ValueError('分类值必须是任务定义的标签')
            canonical = value
        else:
            text = rows[index][task['text_column']]
            if kind == 'span':
                if not isinstance(value, list) or len(value) > 200:
                    raise ValueError('片段标注值必须是最多200项的数组')
                canonical = []
                for span in value:
                    if not isinstance(span, dict) or set(span) != {'start', 'end', 'label'} or span['label'] not in task['labels']:
                        raise ValueError('片段必须包含 start、end 和有效 label')
                    start, end = _offset(span['start'], len(text)), _offset(span['end'], len(text))
                    if end <= start:
                        raise ValueError('片段结束位置必须大于开始位置')
                    canonical.append({'start': start, 'end': end, 'label': span['label']})
                canonical.sort(key=lambda x: (x['start'], x['end'], x['label']))
                if any(a['end'] > b['start'] for a, b in zip(canonical, canonical[1:])):
                    raise ValueError('片段不能重叠')
            else:
                if isinstance(value, dict) and set(value) == {'unanswerable'} and value['unanswerable'] is True:
                    canonical = {'unanswerable': True}
                else:
                    if not isinstance(value, dict) or set(value) != {'start', 'end'}:
                        raise ValueError('问答必须包含 start、end 或 unanswerable:true')
                    start, end = _offset(value['start'], len(text)), _offset(value['end'], len(text))
                    if end <= start:
                        raise ValueError('答案必须包含非空文本')
                    canonical = {'start': start, 'end': end}
        by_row[index] = canonical
    if set(by_row) != allowed_indexes:
        raise ValueError('提交必须覆盖任务全部标注行')
    return [{'row_index': i, 'value': by_row[i]} for i in task['row_indexes']]


@router.post('/api/annotation-tasks/{task_id}/submit', status_code=201)
def annotation_submit(task_id: str, body: SubmissionInput, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        task = _task(s, task_id, user)
        _dataset(s, task['dataset_id'], user, active=True)
        if user.user not in task['annotators']:
            raise HTTPException(403, '当前账户未被分配此标注任务')
        if task['revision'] != body.expected_revision or task['status'] != 'OPEN' or user.user in task['submitted_users']:
            raise HTTPException(409, 'revision conflict; 任务状态已变化或当前账户已经提交')
        items = _canonical_labels(task, body.items, s.rows(task['dataset_id']))
        raw = encode(items)
        s._quota(task['project_id'], len(raw))
        s.store.create('annotation_submission', task['project_id'], {'task_id': task_id, 'creator': user.user,
            'sha256': s.store.blob(raw), 'size_bytes': len(raw), 'row_count': len(items), 'sealed': True}, user.user)
        submitted = task['submitted_users'] + [user.user]
        updated = s.store.update(task_id, {'submitted_users': submitted, 'status': 'SUBMITTED' if len(submitted) == len(task['annotators']) else 'OPEN'}, user.user, body.expected_revision)
        return _task_view(s, updated, user)


class ReviewInput(Input):
    expected_revision: int = Field(ge=1)
    decision: Literal['accept', 'reject']
    comment: str = Field(default='', max_length=2000)
    items: list[LabelItem] | None = Field(default=None, min_length=1, max_length=MAX_ANNOTATION_ROWS)


@router.post('/api/annotation-tasks/{task_id}/review', status_code=201)
def annotation_review(task_id: str, body: ReviewInput, s: Svc, user: User):
    with s.store.transaction():
        task = _task(s, task_id, user)
        user.allow_approve(task['creator'])
        if user.user in task['submitted_users']:
            raise HTTPException(403, '审核者不能审核自己提交的标注')
        if task['revision'] != body.expected_revision or task['status'] != 'SUBMITTED':
            raise HTTPException(409, 'revision conflict; 标注必须全部提交且只审核一次')
        submissions = _submissions(s, task, user)
        agreement = _agreement(s, task, submissions)
        data = {'task_id': task_id, 'decision': body.decision, 'creator': user.user, 'comment': body.comment,
                'agreement': agreement, 'sealed': True}
        if body.decision == 'accept':
            if body.items is None:
                if agreement['disagreement_rows']:
                    raise ValueError('存在分歧，审核者必须提交全部行的最终标签')
                items = _load_labels(s, submissions[0])
            else:
                items = _canonical_labels(task, body.items, s.rows(task['dataset_id']))
            raw = encode(items)
            s._quota(task['project_id'], len(raw))
            data.update(sha256=s.store.blob(raw), size_bytes=len(raw), row_count=len(items))
        elif body.items is not None:
            raise ValueError('驳回时不接受最终标签')
        review = s.store.create('annotation_review', task['project_id'], data, user.user)
        updated = s.store.update(task_id, {'review_id': review['id'], 'status': 'ACCEPTED' if body.decision == 'accept' else 'REJECTED'}, user.user, body.expected_revision)
        return _task_view(s, updated, user)


class AnnotationExportInput(Input):
    expected_revision: int = Field(ge=1)
    name: str = Field(default='', max_length=150)
    label_column: str = Field(default='_label', min_length=1, max_length=100, pattern=r'.*\S.*')


@router.post('/api/annotation-tasks/{task_id}/export', status_code=201)
def annotation_export(task_id: str, body: AnnotationExportInput, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        task = _task(s, task_id, user)
        if task['revision'] != body.expected_revision or task['status'] != 'ACCEPTED':
            raise HTTPException(409, 'revision conflict; 仅审核通过且尚未导出的任务可导出')
        asset = _dataset(s, task['dataset_id'], user, active=True)
        rows = s.rows(asset['id'])
        if body.label_column == '_source_row' or body.label_column in asset['columns'] or '_source_row' in asset['columns']:
            raise ValueError('标签列或 _source_row 与原始字段冲突，请使用其他标签列或重命名源字段')
        review = s.record(task['review_id'], 'annotation_review', user)
        output = []
        for item in _load_labels(s, review):
            index, value = item['row_index'], copy.deepcopy(item['value'])
            if task['task_type'] == 'qa':
                text = rows[index][task['text_column']]
                value.update(question=task['question'], answer='' if value.get('unanswerable') else text[value['start']:value['end']])
            if task['task_type'] == 'span':
                text = rows[index][task['text_column']]
                value = [{**span, 'text': text[span['start']:span['end']]} for span in value]
            output.append({**copy.deepcopy(rows[index]), body.label_column: value, '_source_row': index})
        dataset = s.add_dataset(task['project_id'], body.name.strip() or (task['name'] + ' · 已审核标签')[:150], output,
                                list(dict.fromkeys(asset['tags'] + ['labelled', task['task_type']])), user, [asset['id']])
        s.store.update(task_id, {'status': 'EXPORTED', 'exported_dataset_id': dataset['id'], 'exported_at': now()}, user.user, body.expected_revision)
        return {'dataset': dataset, 'task_id': task_id, 'review_id': review['id']}


def install(app):
    install_once(app, router, 'data')
