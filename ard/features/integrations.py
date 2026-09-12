"""Immutable package inventory, bounded HTTP MCP, and owned Docker runtimes.

No uploaded package is imported or executed by this module. Remote configuration
is private; public records contain opaque references and operational metadata.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
import base64
import gzip
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import PurePosixPath
import re
import selectors
import shutil
import socket
import ssl
import stat
import subprocess
import tarfile
import threading
import time
import tomllib
from typing import Literal
from urllib.parse import urlsplit, quote
import uuid
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import Field, SecretStr, ValidationError

from ard import __version__
from ard.features.common import Input, Svc, User, admin, install_once
from ard.store import encode, now

router = APIRouter(tags=['integrations'])
UPLOAD_LIMIT = 20 * 1024 * 1024
EXPANDED_LIMIT = 60 * 1024 * 1024
MEMBER_LIMIT = 10 * 1024 * 1024
MEMBER_COUNT = 1000
MCP_LIMIT = 1024 * 1024
PROTOCOL = '2025-06-18'
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
TOOL_NAME = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')
IMAGE = re.compile(r'^sha256:[0-9a-f]{64}$')
ID = re.compile(r'^[0-9a-f]{32}$')
CONTAINER_ID = re.compile(r'^[0-9a-f]{64}$')
SOURCES = {'.py', '.js', '.ts', '.sh', '.r', '.jl', '.c', '.cpp', '.h', '.go', '.rs', '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.sql'}
_dns_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ard-mcp-dns')
_dns_slots = threading.BoundedSemaphore(4)


def _leaf(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,149}', name):
        raise ValueError('文件名需为不含路径的英文、数字、点、加号、减号或下划线')
    return name


def _member_path(name):
    if (not isinstance(name, str) or not name or len(name) > 240 or '\\' in name or ':' in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name) or name.startswith('/')):
        raise ValueError('归档成员路径不安全')
    parts = name.rstrip('/').split('/')
    if len(parts) > 16 or any(p in ('', '.', '..') for p in parts):
        raise ValueError('归档成员路径不安全')
    return '/'.join(parts)


def inspect_package(filename, raw):
    """Read bounded members in memory; never extract to the filesystem."""
    _leaf(filename)
    if not raw or len(raw) > UPLOAD_LIMIT:
        raise ValueError('工具包必须非空且不超过20MiB')
    members, seen, files, total = [], {}, {}, 0

    def add(path, size, stream=None, directory=False):
        nonlocal total
        path = _member_path(path)
        key = path.casefold()
        if len(seen) >= MEMBER_COUNT or key in seen:
            raise ValueError('归档成员过多或路径重复')
        for parent in PurePosixPath(key).parents:
            if str(parent) != '.' and seen.get(str(parent)) is False:
                raise ValueError('归档文件与目录路径冲突')
        if not directory and any(p.startswith(key + '/') for p in seen):
            raise ValueError('归档文件与目录路径冲突')
        seen[key] = directory
        if directory:
            return
        if size < 0 or size > MEMBER_LIMIT or total + size > EXPANDED_LIMIT:
            raise ValueError('归档展开大小超过限制')
        content = stream.read(size + 1)
        if len(content) != size:
            raise ValueError('归档成员大小不一致')
        total += size
        if total > max(1024 * 1024, len(raw) * 100):
            raise ValueError('归档压缩比超过限制')
        members.append({'path': path, 'size_bytes': size, 'sha256': hashlib.sha256(content).hexdigest()})
        if PurePosixPath(path).name in ('METADATA', 'PKG-INFO', 'pyproject.toml', 'ard-package.json') or re.fullmatch(r'requirements[^/]*\.txt', PurePosixPath(path).name):
            if len(content) > 256 * 1024:
                raise ValueError('包元数据文件超过256KiB')
            files[path] = content

    try:
        if filename.lower().endswith(('.zip', '.whl')):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > MEMBER_COUNT:
                    raise ValueError('归档成员过多')
                for info in archive.infolist():
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if info.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise ValueError('归档禁止加密文件、符号链接和特殊文件')
                    if info.file_size > max(1024 * 1024, info.compress_size * 100):
                        raise ValueError('归档压缩比超过限制')
                    if info.is_dir():
                        add(info.filename, 0, directory=True)
                    else:
                        with archive.open(info) as stream:
                            add(info.filename, info.file_size, stream)
            package_format = 'wheel' if filename.lower().endswith('.whl') else 'zip'
        elif filename.lower().endswith(('.tar.gz', '.tgz')):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:
                unpacked = zipped.read(EXPANDED_LIMIT + 2 * 1024 * 1024 + 1)
            if len(unpacked) > EXPANDED_LIMIT + 2 * 1024 * 1024 or len(unpacked) > max(2 * 1024 * 1024, len(raw) * 100):
                raise ValueError('归档展开大小或压缩比超过限制')
            with tarfile.open(fileobj=io.BytesIO(unpacked), mode='r:') as archive:
                for info in archive:
                    if not (info.isfile() or info.isdir()) or info.sparse is not None or info.linkname:
                        raise ValueError('归档禁止链接、稀疏文件和特殊文件')
                    if info.isdir():
                        add(info.name, 0, directory=True)
                    else:
                        with archive.extractfile(info) as stream:
                            add(info.name, info.size, stream)
            package_format = 'tar.gz'
        elif PurePosixPath(filename).suffix.lower() in SOURCES:
            add(filename, len(raw), io.BytesIO(raw))
            package_format = 'source'
        else:
            raise ValueError('仅支持whl、zip、tar.gz、tgz和声明的源码/配置文件扩展名')
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, RuntimeError) as exc:
        raise ValueError('归档格式损坏或使用不支持的压缩格式') from exc
    if not members:
        raise ValueError('工具包不含普通文件')
    metadata, requirements, warnings = [], [], []
    for path, content in sorted(files.items()):
        leaf = PurePosixPath(path).name
        try:
            if leaf in ('METADATA', 'PKG-INFO'):
                msg = BytesParser().parsebytes(content)
                item = {'source': path, 'name': msg.get('Name'), 'version': msg.get('Version')}
                reqs = msg.get_all('Requires-Dist', [])
            elif leaf == 'pyproject.toml':
                doc = tomllib.loads(content.decode('utf-8'))
                project = doc.get('project', {})
                item = {'source': path, 'name': project.get('name'), 'version': project.get('version')}
                reqs = project.get('dependencies', [])
                if project.get('dynamic'):
                    warnings.append(path + ': 动态元数据未执行，需手工指定版本')
            elif leaf == 'ard-package.json':
                doc = json.loads(content)
                if not isinstance(doc, dict):
                    raise ValueError()
                item = {'source': path, 'name': doc.get('name'), 'version': doc.get('version')}
                reqs = doc.get('requirements', [])
            else:
                item = {'source': path}
                reqs = [line.strip() for line in content.decode('utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')]
            if any(v is not None and (not isinstance(v, str) or len(v) > 200) for k, v in item.items() if k != 'source'):
                raise ValueError()
            if not isinstance(reqs, list) or len(reqs) > 500 or any(not isinstance(x, str) or len(x) > 2000 for x in reqs):
                raise ValueError()
            metadata.append({k: v for k, v in item.items() if v is not None})
            requirements.extend({'source': path, 'requirement': req} for req in reqs)
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
            raise ValueError('包元数据格式无效') from exc
    if len(requirements) > 1000:
        raise ValueError('依赖声明过多')
    if package_format == 'wheel' and not any(x['source'].endswith('.dist-info/METADATA') and x.get('name') and x.get('version') for x in metadata):
        raise ValueError('wheel缺少有效dist-info/METADATA')
    return {'format': package_format, 'files': members, 'expanded_bytes': total,
            'metadata_sources': metadata, 'requirements': requirements, 'warnings': warnings,
            'execution': '未执行；依赖仅发现，不解析安装、不联网获取'}


def _package_state(s, package):
    states = [x for x in s.store.list('integration_package_state', package['project_id']) if x['package_id'] == package['id']]
    if len(states) != 1:
        raise ValueError('工具包状态记录缺失或重复')
    return states[0]


def _package_public(s, package):
    state = _package_state(s, package)
    return {**package, 'archived': state['archived'], 'state_revision': state['revision']}


@router.get('/api/projects/{pid}/packages')
def packages(pid: str, s: Svc, user: User):
    return [_package_public(s, item) for item in s.list('integration_package', pid, user)]


@router.post('/api/projects/{pid}/packages', status_code=201)
async def upload_package(pid: str, s: Svc, user: User, file: UploadFile = File(), name: str = Form(''),
                         version: str = Form(''), category: Literal['tool', 'dependency'] = Form('tool'),
                         parent_id: str = Form('')):
    user.allow_write()
    s.project(pid, user)
    raw = await file.read(UPLOAD_LIMIT + 1)
    manifest = inspect_package(file.filename or '', raw)
    name = name.strip() or next((x['name'] for x in manifest['metadata_sources'] if x.get('name')), '')
    version = version.strip() or next((x['version'] for x in manifest['metadata_sources'] if x.get('version')), '')
    if not NAME.fullmatch(name) or not NAME.fullmatch(version):
        raise ValueError('请指定有效名称和版本：1–100位英文、数字、点、减号或下划线')
    with s.store.transaction():
        if parent_id:
            parent = s.record(parent_id, 'integration_package', user)
            if parent['project_id'] != pid or parent['name'] != name or parent['category'] != category:
                raise ValueError('父版本必须属于同项目、同名称、同类别')
        if any(x['name'] == name and x['version'] == version and x['category'] == category for x in s.list('integration_package', pid, user)):
            raise ValueError('同名同版本已存在；请创建新版本')
        manifest_raw = encode(manifest)
        s._quota(pid, len(raw) + len(manifest_raw))
        package = s.store.create('integration_package', pid, {'name': name, 'version': version, 'category': category,
                  'filename': file.filename, 'format': manifest['format'], 'sha256': s.store.blob(raw),
                  'manifest_sha256': s.store.blob(manifest_raw), 'size_bytes': len(raw) + len(manifest_raw),
                  'artifact_size_bytes': len(raw), 'file_count': len(manifest['files']), 'parent_id': parent_id or None,
                  'creator': user.user, 'sealed': True}, user.user)
        s.store.create('integration_package_state', pid, {'package_id': package['id'], 'archived': False}, user.user)
        return _package_public(s, package)


@router.get('/api/packages/{package_id}/manifest')
def package_manifest(package_id: str, s: Svc, user: User):
    record = s.record(package_id, 'integration_package', user)
    return json.loads(s.store.read_blob(record['manifest_sha256']))


@router.get('/api/packages/{package_id}/download')
def download_package(package_id: str, s: Svc, user: User):
    record = s.record(package_id, 'integration_package', user)
    return Response(s.store.read_blob(record['sha256']), media_type='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="' + _leaf(record['filename']) + '"'})


@router.get('/api/packages/{package_id}/requirements')
def package_requirements(package_id: str, s: Svc, user: User):
    data = package_manifest(package_id, s, user)
    # A discovery report, deliberately not an executable pip input file.
    report = '\n'.join(json.dumps(x, ensure_ascii=False) for x in data['requirements']) + '\n'
    return Response(report, media_type='application/x-ndjson', headers={'Content-Disposition': 'attachment; filename="requirements-discovery.jsonl"'})


class ArchiveInput(Input):
    expected_revision: int = Field(ge=1)
    archived: bool
    confirmed: Literal[True]


@router.post('/api/packages/{package_id}/archive')
def archive_package(package_id: str, body: ArchiveInput, s: Svc, user: User):
    user.allow_write()
    with s.store.transaction():
        package = s.record(package_id, 'integration_package', user)
        if body.archived and any(package_id in r.get('package_ids', []) and r['status'] not in ('STOPPED', 'REMOVED', 'FAILED', 'EXITED') for r in s.list('integration_runtime', package['project_id'], user)):
            raise ValueError('工具包被运行实例引用，请先停止实例')
        state = _package_state(s, package)
        s.store.update(state['id'], {'archived': body.archived}, user.user, body.expected_revision)
        return _package_public(s, package)


class RecipeInput(Input):
    package_ids: list[str] = Field(min_length=1, max_length=20)


@router.post('/api/projects/{pid}/packages/docker-context')
def docker_context(pid: str, body: RecipeInput, s: Svc, user: User):
    s.project(pid, user)
    if len(set(body.package_ids)) != len(body.package_ids):
        raise ValueError('工具包选择不能重复')
    selected, total = [], 0
    for item_id in body.package_ids:
        item = s.record(item_id, 'integration_package', user)
        if item['project_id'] != pid or _package_state(s, item)['archived']:
            raise ValueError('工具包需在当前项目且未归档')
        total += item['artifact_size_bytes']
        if total > EXPANDED_LIMIT:
            raise ValueError('导出上下文超过60MiB')
        selected.append(item)
    dockerfile = ['FROM python:3.12-slim', 'WORKDIR /opt/ard-packages', 'COPY ["packages/", "/opt/ard-packages/"]']
    wheels = [f"/opt/ard-packages/{x['id']}/{x['filename']}" for x in selected if x['format'] == 'wheel']
    if wheels:
        dockerfile.append('RUN ' + json.dumps(['python', '-m', 'pip', 'install', '--no-index', '--no-deps', *wheels]))
    dockerfile += ['USER 65534:65534', 'CMD ["python", "-c", "print(\'ARD package context ready\')"]', '']
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('Dockerfile', '\n'.join(dockerfile))
        archive.writestr('README.txt', '此导出未构建、未运行。基础镜像由构建者获取并审核。wheel离线安装且不解析依赖；其他源码/归档仅复制。使用可信Python3镜像，在隔离Docker构建器中审核构建。运行平台仅接受已存在的镜像sha256 ID，固定无网络、无挂载和资源上限。\n')
        for item in selected:
            archive.writestr(f"packages/{item['id']}/{item['filename']}", s.store.read_blob(item['sha256']))
            archive.writestr(f"manifests/{item['id']}.json", s.store.read_blob(item['manifest_sha256']))
    return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="ard-docker-context.zip"'})


def _private_dir(s):
    directory = s.store.root / 'private' / 'integrations'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def _write_private(s, data):
    key = uuid.uuid4().hex
    path = _private_dir(s) / (key + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encode(data))
    return key


def _read_private(s, key):
    if not ID.fullmatch(key):
        raise ValueError('私有配置引用无效')
    try:
        return json.loads((_private_dir(s) / (key + '.json')).read_bytes())
    except (OSError, ValueError) as exc:
        raise HTTPException(503, '私有连接配置不可用；请管理员重新配置') from exc


def _redact(value, secrets=()):
    if isinstance(value, dict):
        return {_redact(k, secrets): '[已隐藏]' if re.search(r'token|password|secret|authorization|api.?key|session', k, re.I)
                else _redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(x, secrets) for x in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                for candidate in (secret, quote(secret, safe=''), base64.b64encode(secret.encode()).decode()):
                    value = value.replace(candidate, '[已隐藏]')
        return value
    return value


def _mcp_public(record):
    return {k: v for k, v in record.items() if k != 'config_key'}


class MCPConfig(Input):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)
    token: SecretStr = SecretStr('')
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)
    allow_private: bool = False
    timeout_seconds: int = Field(default=10, ge=2, le=20)
    enabled: bool = True
    expected_revision: int | None = Field(default=None, ge=1)


def _endpoint(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.query or len(url) > 2000
                or any(ord(c) < 33 or ord(c) > 126 for c in url) or '\\' in url):
            raise ValueError()
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        if port < 1 or port > 65535:
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise ValueError('MCP端点需为无账号、查询参数或片段的HTTP(S)地址') from exc
    return parts, port


async def _config_body(request):
    try:
        raw = await request.body()
        if len(raw) > 65536:
            raise ValueError()
        body = MCPConfig.model_validate_json(raw)
        _endpoint(body.url)
        token = body.token.get_secret_value()
        if len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError()
        if len(set(body.allowed_tools)) != len(body.allowed_tools) or any(not TOOL_NAME.fullmatch(x) for x in body.allowed_tools):
            raise ValueError()
        return body
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        # FastAPI validation errors echo rejected values; never echo credential input.
        raise HTTPException(400, 'MCP配置无效，请检查端点、工具白名单、超时和凭据格式') from None


def _save_mcp(s, pid, body, user, current=None):
    parts, _ = _endpoint(body.url)
    token = body.token.get_secret_value()
    public = {'name': body.name, 'endpoint_origin': f'{parts.scheme}://{parts.netloc}',
              'allowed_tools': body.allowed_tools, 'allow_private': body.allow_private,
              'timeout_seconds': body.timeout_seconds, 'enabled': body.enabled,
              'has_credentials': bool(token), 'last_probe': None, 'creator': user.user}
    public = _redact(public, (token,))
    with s.store.transaction():
        if current and s.store.get(current['id'])['revision'] != body.expected_revision:
            raise ValueError('revision conflict; reload configuration')
        public['config_key'] = _write_private(s, {'url': body.url, 'token': token})
        if current:
            result = s.store.update(current['id'], public, user.user, body.expected_revision)
        else:
            result = s.store.create('integration_mcp', pid, public, user.user)
    return _mcp_public(result)


@router.get('/api/projects/{pid}/mcp')
def mcp_configs(pid: str, s: Svc, user: User):
    records = s.list('integration_mcp', pid, user)
    events = s.list('integration_mcp_event', pid, user)
    results = []
    for record in records:
        public = _mcp_public(record)
        event = next((x for x in events if x['config_id'] == record['id'] and x.get('config_revision') == record['revision'] and x['operation'] == 'probe'), None)
        if event:
            public['last_probe'] = {'at': event['created_at'], 'available': event['status'] == 'SUCCEEDED'}
        results.append(public)
    return results


@router.post('/api/projects/{pid}/mcp', status_code=201)
async def create_mcp(pid: str, request: Request, s: Svc, user: User):
    admin(user)
    s.project(pid, user)
    body = await _config_body(request)
    return _save_mcp(s, pid, body, user)


@router.put('/api/mcp/{config_id}')
async def update_mcp(config_id: str, request: Request, s: Svc, user: User):
    admin(user)
    current = s.record(config_id, 'integration_mcp', user)
    body = await _config_body(request)
    if body.expected_revision is None:
        raise HTTPException(400, '修改MCP配置需指定expected_revision')
    return _save_mcp(s, current['project_id'], body, user, current)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('MCP操作超时')
    return remaining


def _resolve(host, port, allow_private, deadline):
    if not _dns_slots.acquire(blocking=False):
        raise ValueError('MCP解析服务繁忙')
    try:
        future = _dns_pool.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except BaseException:
        _dns_slots.release()
        raise
    future.add_done_callback(lambda _: _dns_slots.release())
    addresses = future.result(timeout=_remaining(deadline))
    approved = []
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (not allow_private and not ip.is_global):
            raise ValueError('MCP地址不在允许的网络范围')
        approved.append(address[0])
    if not approved:
        raise ValueError('MCP域名没有可用地址')
    return approved[0]


def _http_exchange(url, payload, headers, allow_private, deadline, method='POST'):
    """One pinned-address HTTP request; no redirects, proxies or decompression."""
    parts, port = _endpoint(url)
    ip = _resolve(parts.hostname, port, allow_private, deadline)
    conn = http.client.HTTPConnection(parts.hostname, port, timeout=_remaining(deadline))
    sock = None
    try:
        sock = socket.create_connection((ip, port), timeout=_remaining(deadline))
        if parts.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        conn.sock = sock
        sock.settimeout(_remaining(deadline))
        conn.request(method, parts.path or '/', body=encode(payload) if payload is not None else None, headers=headers)
        response = conn.getresponse()
        if 300 <= response.status < 400:
            raise ValueError('MCP端点重定向被拒绝')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('MCP压缩响应不受支持')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > MCP_LIMIT):
            raise ValueError('MCP响应大小超过限制')
        out_headers = {k.lower(): v for k, v in response.getheaders()}
        if len(out_headers) > 100 or any(len(v) > 8192 for v in out_headers.values()):
            raise ValueError('MCP响应头超过限制')
        content = bytearray()
        is_sse = out_headers.get('content-type', '').split(';')[0].strip() == 'text/event-stream'
        while True:
            sock.settimeout(_remaining(deadline))
            chunk = response.read1(min(65536, MCP_LIMIT + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MCP_LIMIT:
                raise ValueError('MCP响应大小超过限制')
            # Streaming endpoints may keep the socket open after this response.
            if is_sse and payload and 'id' in payload:
                parsed = _sse_response(bytes(content), payload['id'], incomplete=True)
                if parsed is not None:
                    return response.status, out_headers, encode(parsed)
        if is_sse and payload and 'id' in payload:
            parsed = _sse_response(bytes(content), payload['id'])
            return response.status, out_headers, encode(parsed)
        return response.status, out_headers, bytes(content)
    finally:
        conn.close()
        if sock is not None:
            sock.close()


def _sse_response(raw, request_id, incomplete=False):
    try:
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError:
        if incomplete:
            return None
        raise ValueError('MCP SSE编码无效') from None
    events = text.split('\n\n')
    if len(events) > 1000:
        raise ValueError('MCP SSE事件过多')
    for event in events[:-1]:
        lines = [line[5:].lstrip(' ') for line in event.split('\n') if line.startswith('data:')]
        if not lines:
            continue
        try:
            message = json.loads('\n'.join(lines))
        except (ValueError, RecursionError):
            raise ValueError('MCP SSE消息无效') from None
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise ValueError('MCP SSE消息格式无效')
        if 'method' in message and 'id' in message:
            raise ValueError('不支持MCP服务端主动请求')
        if message.get('id') == request_id and 'method' not in message:
            return message
    if incomplete:
        return None
    raise ValueError('MCP SSE缺少匹配响应')


def _json_bound(value, limit=65536):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 5000 or depth > 12:
            raise ValueError('MCP JSON结构超过限制')
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    if len(encode(value)) > limit:
        raise ValueError('MCP JSON内容超过限制')


class MCPClient:
    def __init__(self, config, secret):
        self.config, self.secret = config, secret
        self.deadline = time.monotonic() + config['timeout_seconds']
        self.session, self.counter, self.version = None, 0, None

    def headers(self):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Accept-Encoding': 'identity'}
        if self.secret.get('token'):
            headers['Authorization'] = 'Bearer ' + self.secret['token']
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        if self.version:
            headers['MCP-Protocol-Version'] = self.version
        return headers

    def send(self, method, params=None, notify=False):
        payload = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            payload['params'] = params
        if not notify:
            self.counter += 1
            payload['id'] = self.counter
        status, headers, raw = _http_exchange(self.secret['url'], payload, self.headers(), self.config['allow_private'], self.deadline)
        if status not in (200, 202, 204):
            raise ValueError('MCP服务返回HTTP失败状态')
        session = headers.get('mcp-session-id')
        if session:
            if not re.fullmatch(r'[!-~]{1,256}', session) or (self.session is not None and self.session != session):
                raise ValueError('MCP会话标识无效或中途变化')
            self.session = session
        if notify:
            if status != 202 or raw.strip():
                raise ValueError('MCP通知应返回无正文的202响应')
            return None
        content_type = headers.get('content-type', '').split(';')[0].strip()
        if status != 200 or content_type not in ('application/json', 'text/event-stream'):
            raise ValueError('MCP响应类型无效')
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError('MCP JSON响应无效') from None
        _json_bound(message, MCP_LIMIT)
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or 'method' in message or type(message.get('id')) is not int or message['id'] != self.counter:
            raise ValueError('MCP响应标识不匹配')
        if 'error' in message:
            raise ValueError('MCP返回协议错误；远端详情已隐藏')
        if not isinstance(message.get('result'), dict):
            raise ValueError('MCP响应缺少result对象')
        return message['result']

    def initialize(self):
        result = self.send('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'AI-RD-Platform', 'version': __version__}})
        if result.get('protocolVersion') != PROTOCOL or not isinstance(result.get('capabilities'), dict) or not isinstance(result.get('serverInfo'), dict):
            raise ValueError('MCP版本或初始化响应不受支持')
        if not isinstance(result['capabilities'].get('tools'), dict):
            raise ValueError('MCP服务未声明tools能力')
        self.version = PROTOCOL
        self.send('notifications/initialized', notify=True)

    def tools(self):
        tools, cursor, cursors = [], None, set()
        for _ in range(5):
            result = self.send('tools/list', {'cursor': cursor} if cursor else {})
            page = result.get('tools')
            if not isinstance(page, list):
                raise ValueError('MCP工具列表格式无效')
            for item in page:
                if (not isinstance(item, dict) or not isinstance(item.get('name'), str) or not TOOL_NAME.fullmatch(item['name'])
                        or not isinstance(item.get('inputSchema'), dict) or item['inputSchema'].get('type') != 'object'):
                    raise ValueError('MCP工具定义无效')
                _json_bound(item['inputSchema'], 16384)
                description = item.get('description', '')
                if not isinstance(description, str) or len(description) > 4000:
                    raise ValueError('MCP工具描述超过限制')
                tools.append({'name': item['name'], 'description': description, 'inputSchema': item['inputSchema'],
                              'allowed': item['name'] in self.config['allowed_tools']})
            if len(tools) > 100 or len({x['name'] for x in tools}) != len(tools):
                raise ValueError('MCP工具过多或重名')
            cursor = result.get('nextCursor')
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or not cursor or len(cursor) > 1000 or cursor in cursors:
                raise ValueError('MCP分页游标无效')
            cursors.add(cursor)
        raise ValueError('MCP工具分页超过5页')

    def close(self):
        if self.session:
            try:
                _http_exchange(self.secret['url'], None, self.headers(), self.config['allow_private'], time.monotonic() + 1, method='DELETE')
            except Exception:
                pass  # DELETE is optional; no invocation is retried.


@contextmanager
def _mcp(s, config_id, user):
    config = s.record(config_id, 'integration_mcp', user)
    if not config['enabled']:
        raise HTTPException(409, 'MCP连接已禁用')
    secret = _read_private(s, config['config_key'])
    client = MCPClient(config, secret)
    try:
        yield client
    except (OSError, ValueError, TimeoutError, FutureTimeout, http.client.HTTPException) as exc:
        # No transport exception text, endpoint path, remote error, or credentials.
        raise HTTPException(502, 'MCP请求失败、超时或协议不兼容；未自动重试工具调用') from None
    finally:
        client.close()


@router.post('/api/mcp/{config_id}/probe')
def probe_mcp(config_id: str, s: Svc, user: User):
    user.allow_write()
    config = s.record(config_id, 'integration_mcp', user)
    event = {'config_id': config_id, 'config_revision': config['revision'], 'operation': 'probe', 'creator': user.user}
    try:
        with _mcp(s, config_id, user) as client:
            client.initialize()
            result = client.tools()
            redacted = _redact(result, (client.secret.get('token'), client.session, client.secret['url']))
            event.update(status='SUCCEEDED', tool_count=len(result))
            return {'available': True, 'protocol_version': PROTOCOL, 'tools': redacted, 'checked_at': now()}
    except Exception:
        event['status'] = 'FAILED'
        raise
    finally:
        s.store.create('integration_mcp_event', config['project_id'], event, user.user)


class MCPCall(Input):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)
    confirmed: Literal[True]


@router.post('/api/mcp/{config_id}/call')
def call_mcp(config_id: str, body: MCPCall, s: Svc, user: User):
    user.allow_write()
    _json_bound(body.arguments)
    config = s.record(config_id, 'integration_mcp', user)
    if body.tool not in config['allowed_tools']:
        raise HTTPException(403, '工具不在管理员白名单内')
    started = time.monotonic()
    event = {'config_id': config_id, 'operation': 'call', 'tool': body.tool, 'creator': user.user}
    try:
        with _mcp(s, config_id, user) as client:
            if body.tool not in client.config['allowed_tools']:
                raise HTTPException(403, '工具白名单已更新，请重新发现工具')
            client.initialize()
            if body.tool not in {x['name'] for x in client.tools()}:
                raise ValueError('MCP服务未声明此工具')
            latest = s.record(config_id, 'integration_mcp', user)
            if latest['revision'] != client.config['revision']:
                raise HTTPException(409, 'MCP配置在发现期间已更改，请重新发起调用')
            result = client.send('tools/call', {'name': body.tool, 'arguments': body.arguments})
            if not isinstance(result.get('content'), list) or type(result.get('isError', False)) is not bool:
                raise ValueError('MCP工具结果格式无效')
            result = _redact(result, (client.secret.get('token'), client.session, client.secret['url']))
            event['status'] = 'TOOL_ERROR' if result.get('isError') else 'SUCCEEDED'
            return {'result': result, 'elapsed_ms': round((time.monotonic() - started) * 1000, 1)}
    except Exception:
        event['status'] = 'FAILED_OR_UNKNOWN'
        raise
    finally:
        event['elapsed_ms'] = round((time.monotonic() - started) * 1000, 1)
        s.store.create('integration_mcp_event', config['project_id'], event, user.user)


def _docker(args, timeout=10):
    binary = shutil.which('docker')
    if not binary:
        raise HTTPException(503, '未检测到Docker CLI；可导出构建上下文')
    # Only the local daemon is supported. Never inherit DOCKER_HOST/context.
    try:
        process = subprocess.Popen([binary, '--host', 'unix:///var/run/docker.sock', *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, shell=False)
    except OSError:
        raise HTTPException(503, 'Docker CLI无法启动') from None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline, output = time.monotonic() + timeout, bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(503, 'Docker操作超时；请刷新实例状态')
            for key, _ in selector.select(min(.2, remaining)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output.extend(chunk)
                    if len(output) > 256 * 1024:
                        raise HTTPException(503, 'Docker响应超过限制')
                else:
                    selector.unregister(key.fileobj)
            if process.poll() is not None and not selector.get_map():
                break
        if process.returncode:
            raise HTTPException(503, 'Docker守护进程不可用或操作失败')
        return output.decode('utf-8').strip()
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stdout.close()


def _runtime_status():
    try:
        version = json.loads(_docker(['info', '--format', '{{json .ServerVersion}}'], timeout=3))
        if not isinstance(version, str) or len(version) > 100:
            raise ValueError()
        return {'available': True, 'server_version': version, 'network': 'none', 'backend': 'local-docker',
                'limits': {'memory_mib': [64, 1024], 'cpus': [0.1, 2], 'ttl_seconds': [10, 3600]}}
    except (HTTPException, ValueError, OSError):
        return {'available': False, 'backend': 'local-docker', 'reason': 'Docker CLI或本地守护进程不可用；可以导出构建上下文', 'network': 'none'}


@router.get('/api/runtime/status')
def runtime_status(user: User):
    return _runtime_status()


def _owner(s):
    with s.store.lock:
        path = _private_dir(s) / 'runtime-owner'
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(uuid.uuid4().hex)
        value = path.read_text().strip()
        if not ID.fullmatch(value):
            raise HTTPException(503, '运行实例所有权配置无效')
        return value


def _owned_inspect(s, runtime):
    target = runtime.get('container_id') or runtime['container_name']
    if runtime.get('container_id') and not CONTAINER_ID.fullmatch(target):
        raise ValueError('运行实例容器标识无效')
    try:
        data = json.loads(_docker(['container', 'inspect', target]))
        info = data[0]
        labels = info.get('Config', {}).get('Labels', {})
        expected = {'io.ard.owner': _owner(s), 'io.ard.project': runtime['project_id'], 'io.ard.runtime': runtime['id']}
        if (len(data) != 1 or any(labels.get(k) != v for k, v in expected.items())
                or not CONTAINER_ID.fullmatch(info.get('Id', '')) or info.get('Name') != '/' + runtime['container_name']):
            raise HTTPException(403, '拒绝操作不属于当前平台与项目的容器')
        if runtime.get('container_id') and info['Id'] != runtime['container_id']:
            raise HTTPException(403, '容器标识不匹配')
        return info
    except (ValueError, TypeError, KeyError, IndexError):
        raise HTTPException(503, 'Docker检查响应无效') from None


class RuntimeInput(Input):
    image: str = Field(pattern=r'^sha256:[0-9a-f]{64}$')
    argv: list[str] = Field(min_length=1, max_length=20)
    package_ids: list[str] = Field(default_factory=list, max_length=20)
    memory_mib: int = Field(default=256, ge=64, le=1024)
    cpus: float = Field(default=0.5, ge=0.1, le=2)
    ttl_seconds: int = Field(default=300, ge=10, le=3600)
    confirmed: Literal[True]


def _runtime_argv(body):
    if any(not x or len(x) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in x) for x in body.argv):
        raise ValueError('容器参数不能为空或含控制字符，单项最多1000字符')
    if body.argv[0].startswith('-'):
        raise ValueError('容器程序名不能以减号开头')
    if len(encode(body.argv)) > 8192:
        raise ValueError('容器参数总长超过限制')
    return body.argv


def _supervisor_code(ttl):
    return ("import subprocess,sys\ntry:\n r=subprocess.run(sys.argv[1:], timeout=" + str(ttl) + ")\n sys.exit(r.returncode)\nexcept subprocess.TimeoutExpired:\n sys.exit(124)\n")


@router.get('/api/projects/{pid}/runtimes')
def runtimes(pid: str, s: Svc, user: User):
    return s.list('integration_runtime', pid, user)


@router.post('/api/projects/{pid}/runtimes', status_code=201)
def create_runtime(pid: str, body: RuntimeInput, s: Svc, user: User):
    admin(user)
    project = s.project(pid, user)
    argv = _runtime_argv(body)
    if len(set(body.package_ids)) != len(body.package_ids):
        raise ValueError('工具包引用不能重复')
    for item_id in body.package_ids:
        package = s.record(item_id, 'integration_package', user)
        if package['project_id'] != pid or _package_state(s, package)['archived']:
            raise ValueError('运行实例只能引用当前项目的未归档工具包')
    if not _runtime_status()['available']:
        raise HTTPException(503, 'Docker不可用；本次未创建运行实例')
    try:
        image = json.loads(_docker(['image', 'inspect', body.image, '--format', '{{json .Id}}']))
        if image != body.image:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, '需要本地已存在且可信、含Python3的镜像sha256 ID') from None
    owner = _owner(s)
    with s.store.transaction():
        # Repeat under the same lock as reservation: archive must not race launch.
        for item_id in body.package_ids:
            if _package_state(s, s.record(item_id, 'integration_package', user))['archived']:
                raise ValueError('工具包已归档，请重新选择')
        active = [x for x in s.list('integration_runtime', pid, user) if x['status'] not in ('STOPPED', 'REMOVED', 'FAILED', 'EXITED')]
        if len(active) >= project['max_jobs']:
            raise ValueError('项目运行实例数量达到max_jobs上限')
        record = s.store.create('integration_runtime', pid, {'image': image, 'argv': argv, 'package_ids': body.package_ids,
                    'memory_mib': body.memory_mib, 'cpus': body.cpus, 'ttl_seconds': body.ttl_seconds, 'network': 'none',
                    'status': 'CREATING', 'creator': user.user, 'container_id': None,
                    'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)).isoformat()}, user.user)
        name = 'ard-' + owner[:8] + '-' + record['id']
        record = s.store.update(record['id'], {'container_name': name}, user.user)
    args = ['container', 'create', '--pull', 'never', '--name', name,
            '--label', 'io.ard.owner=' + owner, '--label', 'io.ard.project=' + pid, '--label', 'io.ard.runtime=' + record['id'],
            '--network', 'none', '--memory', str(body.memory_mib) + 'm', '--memory-swap', str(body.memory_mib) + 'm',
            '--cpus', str(body.cpus), '--pids-limit', '64', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--user', '65534:65534', '--init',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16777216', '--restart', 'no', '--log-driver', 'none',
            '--entrypoint', 'python', image, '-c', _supervisor_code(body.ttl_seconds), *argv]
    try:
        container_id = _docker(args)
        if not CONTAINER_ID.fullmatch(container_id):
            raise HTTPException(503, 'Docker创建响应无效')
        record = s.store.update(record['id'], {'container_id': container_id, 'status': 'CREATED'}, user.user)
        info = _owned_inspect(s, record)
        _docker(['container', 'start', info['Id']])
        info = _owned_inspect(s, record)
        status = 'RUNNING' if info.get('State', {}).get('Running') else 'EXITED'
        record = s.store.update(record['id'], {'status': status, 'checked_at': now(),
                    'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=body.ttl_seconds)).isoformat()}, user.user)
        if status == 'RUNNING':
            timer = threading.Timer(body.ttl_seconds, _expire_runtime, args=(s, record['id']))
            timer.daemon = True
            timer.start()
        return record
    except Exception:
        s.store.update(record['id'], {'status': 'UNKNOWN', 'error': '创建或启动失败；请刷新并清理此平台拥有的实例'}, user.user)
        raise


def _expire_runtime(s, runtime_id):
    try:
        with s.store.lock:
            runtime = s.store.get(runtime_id, 'integration_runtime')
            if runtime['status'] in ('REMOVED', 'STOPPED'):
                return
            info = _owned_inspect(s, runtime)
            if info.get('State', {}).get('Running'):
                _docker(['container', 'stop', '--time', '1', info['Id']], timeout=4)
            s.store.update(runtime_id, {'status': 'STOPPED', 'checked_at': now(), 'stop_reason': 'ttl'}, 'runtime-watchdog')
    except Exception:
        # The in-container Python supervisor remains the independent time fence.
        return


class RuntimeAction(Input):
    action: Literal['refresh', 'stop', 'remove']
    expected_revision: int = Field(ge=1)
    confirmed: Literal[True]


@router.post('/api/runtimes/{runtime_id}/action')
def runtime_action(runtime_id: str, body: RuntimeAction, s: Svc, user: User):
    admin(user)
    with s.store.lock:
        runtime = s.record(runtime_id, 'integration_runtime', user)
        if runtime['revision'] != body.expected_revision:
            raise ValueError('revision conflict; reload runtime')
        if runtime['status'] == 'REMOVED':
            raise ValueError('运行实例已移除')
        info = _owned_inspect(s, runtime)
        running = bool(info.get('State', {}).get('Running'))
        expired = datetime.fromisoformat(runtime['expires_at']) <= datetime.now(timezone.utc)
        if body.action in ('stop', 'remove') or (expired and running):
            if running:
                _docker(['container', 'stop', '--time', '1', info['Id']], timeout=4)
            status = 'STOPPED'
        else:
            status = 'RUNNING' if running else 'EXITED'
        if body.action == 'remove':
            # A second ownership check is required immediately before deletion.
            _owned_inspect(s, runtime)
            _docker(['container', 'rm', info['Id']])
            status = 'REMOVED'
        return s.store.update(runtime_id, {'status': status, 'checked_at': now()}, user.user, body.expected_revision)


def install(app):
    install_once(app, router, 'integrations')
