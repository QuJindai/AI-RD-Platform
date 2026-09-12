"""Content and protocol tests. HTTP/Docker fixtures are not vendor verification."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
import stat
import tarfile
import threading
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from ard.api import create_app
from ard.features import integrations as feature


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    feature.install(app)
    route_count = len(app.routes)
    feature.install(app)
    assert len(app.routes) == route_count
    with TestClient(app) as client:
        yield client


def project(client, name='合成工具项目'):
    response = client.post('/api/projects', json={'name': name})
    assert response.status_code == 201, response.text
    return response.json()['id']


def zipped(members, compression=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=compression) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return out.getvalue()


def tarred(members):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = '../outside'
            else:
                info.size = len(content)
            archive.addfile(info, io.BytesIO(content) if info.size else None)
    return out.getvalue()


def upload(client, pid, filename='demo.py', raw=b'raise RuntimeError("must never execute")', **fields):
    return client.post(f'/api/projects/{pid}/packages', data={'name': 'demo', 'version': '1.0', **fields},
                       files={'file': (filename, raw, 'application/octet-stream')})


def test_source_package_persistence_immutable_archive_and_quota(client):
    pid = project(client)
    response = upload(client, pid)
    assert response.status_code == 201, response.text
    package = response.json()
    with pytest.raises(ValueError, match='immutable'):
        client.app.state.service.store.update(package['id'], {'name': 'cannot-overwrite'}, 'synthetic')
    manifest = client.get(f'/api/packages/{package["id"]}/manifest').json()
    assert manifest['files'][0]['sha256'] == hashlib.sha256(b'raise RuntimeError("must never execute")').hexdigest()
    assert client.get(f'/api/packages/{package["id"]}/download').content == b'raise RuntimeError("must never execute")'
    assert package['sha256'] == manifest['files'][0]['sha256']
    assert upload(client, pid).status_code == 400
    version = upload(client, pid, version='2.0', parent_id=package['id']).json()
    assert version['parent_id'] == package['id']
    archived = client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 1, 'archived': True, 'confirmed': True})
    assert archived.status_code == 200
    assert archived.json()['revision'] == 1 and archived.json()['state_revision'] == 2
    assert client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 1, 'archived': False, 'confirmed': True}).status_code == 409
    assert client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 2, 'archived': False, 'confirmed': True}).json()['archived'] is False
    assert client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 3, 'archived': True, 'confirmed': False}).status_code == 422
    tiny = client.post('/api/projects', json={'name': '小配额', 'quota_bytes': 1024}).json()['id']
    assert upload(client, tiny, raw=b'#' * 1100).status_code == 400
    assert client.get(f'/api/projects/{tiny}/packages').json() == []


def test_wheel_manifest_requirements_and_exact_docker_export(client):
    pid = project(client)
    raw = zipped([('demo/__init__.py', 'VALUE = 2\n'),
                  ('demo-1.2.dist-info/METADATA', 'Metadata-Version: 2.1\nName: demo\nVersion: 1.2\nRequires-Dist: sample>=1\n\n'),
                  ('demo-1.2.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: py3-none-any\n')])
    response = client.post(f'/api/projects/{pid}/packages', files={'file': ('demo-1.2-py3-none-any.whl', raw)})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['version'] == '1.2'
    manifest = client.get(f'/api/packages/{record["id"]}/manifest').json()
    assert manifest['requirements'] == [{'source': 'demo-1.2.dist-info/METADATA', 'requirement': 'sample>=1'}]
    discovered = client.get(f'/api/packages/{record["id"]}/requirements')
    assert discovered.headers['content-type'].startswith('application/x-ndjson')
    assert json.loads(discovered.text)['requirement'] == 'sample>=1'
    response = client.post(f'/api/projects/{pid}/packages/docker-context', json={'package_ids': [record['id']]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read(f'packages/{record["id"]}/{record["filename"]}') == raw
        dockerfile = archive.read('Dockerfile').decode()
        assert '--no-index' in dockerfile and '--no-deps' in dockerfile
        assert 'USER 65534:65534' in dockerfile
        assert json.loads(archive.read(f'manifests/{record["id"]}.json')) == manifest
    assert client.post(f'/api/projects/{pid}/packages/docker-context', json={'package_ids': [record['id'], record['id']]}).status_code == 400
    other = project(client, '另一个合成项目')
    assert client.post(f'/api/projects/{other}/packages/docker-context', json={'package_ids': [record['id']]}).status_code == 400


def test_tar_pyproject_and_requirement_discovery(client):
    pid = project(client)
    raw = tarred([('src/pyproject.toml', b'[project]\nname="demo"\nversion="2.0"\ndependencies=["sample==1"]\n', tarfile.REGTYPE),
                  ('src/requirements.txt', b'# comment\n-r other.txt\nsample2==3\n', tarfile.REGTYPE),
                  ('src/tool.py', b'print(1)', tarfile.REGTYPE)])
    response = client.post(f'/api/projects/{pid}/packages', files={'file': ('demo.tar.gz', raw)})
    assert response.status_code == 201, response.text
    manifest = client.get(f'/api/packages/{response.json()["id"]}/manifest').json()
    assert len(manifest['files']) == 3
    assert [x['requirement'] for x in manifest['requirements']] == ['sample==1', '-r other.txt', 'sample2==3']
    assert not (client.app.state.service.store.root / 'src').exists()


@pytest.mark.parametrize('path', ['../escape.py', '/escape.py', 'a/../b.py', 'a\\b.py', 'C:/bad.py', 'a//b.py', 'a/./b.py', 'x\x01.py'])
def test_zip_member_traversal_rejected(client, path):
    response = upload(client, project(client), 'bad.zip', zipped([(path, 'bad')]))
    assert response.status_code == 400


def test_archive_duplicates_links_conflicts_bombs_and_corruption(client):
    pid = project(client)
    link = zipfile.ZipInfo('link.py')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    malicious = [zipped([('A.py', '1'), ('a.py', '2')]), zipped([('file', '1'), ('file/sub', '2')]),
                 zipped([(link, '../../outside')]), zipped([('bomb', b'0' * (2 * 1024 * 1024))]), b'not zip']
    for raw in malicious:
        assert upload(client, pid, 'bad.zip', raw).status_code == 400
    for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
        assert upload(client, pid, 'bad.tar.gz', tarred([('bad', b'', kind)])).status_code == 400
    assert upload(client, pid, 'bad.tar.gz', tarred([('../bad', b'1', tarfile.REGTYPE)])).status_code == 400
    assert upload(client, pid, 'bad.whl', zipped([('a.py', '1')])).status_code == 400
    assert upload(client, pid, 'bad.zip', zipped([(f'x{i}', '') for i in range(1001)])).status_code == 400
    assert upload(client, pid, 'demo.exe', b'1').status_code == 400
    assert upload(client, pid, 'demo.py', b'').status_code == 400


def test_invalid_manifest_and_source_upload_limit(client, monkeypatch):
    pid = project(client)
    assert upload(client, pid, 'demo.zip', zipped([('ard-package.json', '{bad')])).status_code == 400
    assert upload(client, pid, 'demo.zip', zipped([('pyproject.toml', '[project]\ndependencies="not-list"')])).status_code == 400
    monkeypatch.setattr(feature, 'UPLOAD_LIMIT', 10)
    assert upload(client, pid, raw=b'x' * 11).status_code == 400


@pytest.fixture
def http_mcp():
    """Synthetic local HTTP fixture; not an external MCP service acceptance."""
    state = {'requests': [], 'mode': 'json', 'token': 'synthetic-secret-123456', 'session': 'synthetic-session-123'}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def send_data(self, status, data=b'', content_type='application/json', session=False):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            if session:
                self.send_header('Mcp-Session-Id', state['session'])
            self.end_headers()
            self.wfile.write(data)

        def do_DELETE(self):
            state['requests'].append({'method': 'DELETE', 'headers': dict(self.headers)})
            self.send_data(204)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append({'body': body, 'headers': dict(self.headers), 'path': self.path})
            method = body['method']
            if state['mode'] == 'redirect':
                self.send_response(307)
                self.send_header('Location', '/leak/' + state['token'])
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if state['mode'] == 'oversize':
                self.send_response(200)
                self.send_header('Content-Length', str(feature.MCP_LIMIT + 1))
                self.end_headers()
                self.close_connection = True
                return
            if method == 'initialize':
                result = {'protocolVersion': feature.PROTOCOL if state['mode'] != 'bad-version' else '0',
                          'serverInfo': {'name': 'synthetic', 'version': '1'}, 'capabilities': {'tools': {}}}
            elif method == 'notifications/initialized':
                self.send_data(202)
                return
            elif method == 'tools/list':
                result = {'tools': [{'name': 'echo', 'description': '合成回显工具', 'inputSchema': {'type': 'object'}},
                                    {'name': 'blocked', 'inputSchema': {'type': 'object'}}]}
                if state['mode'] == 'cursor-loop':
                    result = {'tools': [], 'nextCursor': 'same'}
            elif method == 'tools/call':
                result = {'content': [{'type': 'text', 'text': json.dumps(body['params']['arguments'], ensure_ascii=False)}], 'isError': False}
                if state['mode'] == 'secrets':
                    result = {'content': [{'type': 'text', 'text': state['token'] + ' ' + state['session']}], 'authorization': state['token']}
            else:
                self.send_data(500)
                return
            response = {'jsonrpc': '2.0', 'id': body['id'], 'result': result}
            if state['mode'] == 'wrong-id':
                response['id'] = 1000
            if state['mode'] == 'remote-error':
                response = {'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': -32603, 'message': state['token']}}
            data = json.dumps(response, ensure_ascii=False).encode()
            if state['mode'] == 'sse':
                data = b': keepalive\r\n\r\nevent: message\r\ndata: ' + data + b'\r\n\r\n'
                self.send_data(200, data, 'text/event-stream', method == 'initialize')
            else:
                self.send_data(200, data, session=method == 'initialize')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state['url'] = f'http://127.0.0.1:{server.server_port}/mcp'
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def mcp_config(client, pid, fixture, **fields):
    response = client.post(f'/api/projects/{pid}/mcp', json={'name': '合成协议夹具', 'url': fixture['url'],
                          'token': fixture['token'], 'allowed_tools': ['echo'], 'allow_private': True, **fields})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize('mode', ['json', 'sse'])
def test_actual_http_protocol_fixture_handshake_session_list_call(client, http_mcp, mode):
    http_mcp['mode'] = mode
    config = mcp_config(client, project(client), http_mcp)
    probe = client.post(f'/api/mcp/{config["id"]}/probe')
    assert probe.status_code == 200, probe.text
    assert probe.json()['available'] is True
    assert probe.json()['tools'][0]['allowed'] is True
    assert probe.json()['tools'][1]['allowed'] is False
    call = client.post(f'/api/mcp/{config["id"]}/call', json={'tool': 'echo', 'arguments': {'text': '中文合成输入'}, 'confirmed': True})
    assert call.status_code == 200, call.text
    assert json.loads(call.json()['result']['content'][0]['text']) == {'text': '中文合成输入'}
    methods = [x.get('body', {}).get('method', x.get('method')) for x in http_mcp['requests']]
    assert methods == ['initialize', 'notifications/initialized', 'tools/list', 'DELETE', 'initialize', 'notifications/initialized', 'tools/list', 'tools/call', 'DELETE']
    for request in http_mcp['requests']:
        headers = {k.lower(): v for k, v in request['headers'].items()}
        assert headers['authorization'] == 'Bearer ' + http_mcp['token']
        if request.get('body', {}).get('method') != 'initialize':
            assert headers['mcp-session-id'] == http_mcp['session']
            assert headers['mcp-protocol-version'] == feature.PROTOCOL


def test_mcp_config_secret_private_file_masking_and_revision(client, http_mcp):
    pid = project(client)
    config = mcp_config(client, pid, http_mcp)
    assert 'token' not in config and 'url' not in config and 'config_key' not in config
    assert config['has_credentials'] is True
    store = client.app.state.service.store
    assert http_mcp['token'] not in json.dumps(store.list(), ensure_ascii=False)
    assert http_mcp['token'] not in json.dumps(store.audits(), ensure_ascii=False)
    files = list((store.root / 'private' / 'integrations').glob('*.json'))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert json.loads(files[0].read_text())['token'] == http_mcp['token']
    http_mcp['mode'] = 'secrets'
    call = client.post(f'/api/mcp/{config["id"]}/call', json={'tool': 'echo', 'arguments': {'password': 'synthetic-argument-secret'}, 'confirmed': True})
    assert call.status_code == 200, call.text
    assert http_mcp['token'] not in call.text and http_mcp['session'] not in call.text
    assert '[已隐藏]' in call.text
    assert 'synthetic-argument-secret' not in json.dumps(store.list())
    change = {'name': '修改后', 'url': http_mcp['url'], 'token': '', 'allowed_tools': [], 'expected_revision': 1, 'enabled': False}
    assert client.put(f'/api/mcp/{config["id"]}', json=change).json()['revision'] == 2
    assert client.put(f'/api/mcp/{config["id"]}', json=change).status_code == 409
    assert client.post(f'/api/mcp/{config["id"]}/probe').status_code == 409
    bad = client.post(f'/api/projects/{pid}/mcp', json={'name': 'x', 'url': http_mcp['url'], 'token': http_mcp['token'] + '\n'})
    assert bad.status_code == 400 and http_mcp['token'] not in bad.text


@pytest.mark.parametrize('mode', ['redirect', 'oversize', 'bad-version', 'wrong-id', 'remote-error', 'cursor-loop'])
def test_mcp_remote_failure_is_bounded_and_redacted(client, http_mcp, mode):
    http_mcp['mode'] = mode
    config = mcp_config(client, project(client), http_mcp)
    response = client.post(f'/api/mcp/{config["id"]}/probe')
    assert response.status_code == 502, response.text
    assert http_mcp['token'] not in response.text
    assert all(x.get('path', '/mcp') == '/mcp' for x in http_mcp['requests'])
    assert not any(x.get('body', {}).get('method') == 'tools/call' for x in http_mcp['requests'])
    state = client.get(f'/api/projects/{config["project_id"]}/mcp').json()[0]
    assert state['last_probe']['available'] is False


def test_mcp_explicit_invocation_allowlist_limits_and_forbidden_endpoint(client, http_mcp):
    pid = project(client)
    config = mcp_config(client, pid, http_mcp)
    path = f'/api/mcp/{config["id"]}/call'
    assert client.post(path, json={'tool': 'blocked', 'confirmed': True}).status_code == 403
    assert client.post(path, json={'tool': 'echo', 'confirmed': False}).status_code == 422
    assert client.post(path, json={'tool': 'echo', 'confirmed': True, 'url': http_mcp['url']}).status_code == 422
    assert client.post(path, json={'tool': 'echo', 'confirmed': True, 'arguments': {'text': 'x' * 65536}}).status_code == 400
    assert http_mcp['requests'] == []
    config2 = mcp_config(client, pid, http_mcp, allow_private=False)
    assert client.post(f'/api/mcp/{config2["id"]}/probe').status_code == 502
    assert http_mcp['requests'] == []
    for url in ('file:///etc/passwd', 'http://user:pass@localhost/mcp', 'http://localhost/mcp?token=secret', 'https://localhost/#frag', 'http://localhost:99999/mcp'):
        response = client.post(f'/api/projects/{pid}/mcp', json={'name': 'x', 'url': url})
        assert response.status_code == 400


def test_mcp_network_metadata_and_json_depth_limits(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 80))])
    with pytest.raises(ValueError):
        feature._resolve('metadata.synthetic', 80, True, time.monotonic() + 1)
    data = value = {}
    for _ in range(14):
        value['a'] = {}
        value = value['a']
    with pytest.raises(ValueError):
        feature._json_bound(data)
    with pytest.raises(ValueError):
        feature._sse_response(b'data: {"jsonrpc":"2.0","id":2,"method":"sampling/createMessage"}\n\n', 1)
    assert feature._sse_response(b'data: {"jsonrpc":"2.0",\ndata: "id":1,"result":{}}\n\n', 1)['result'] == {}


def test_mcp_timeout_and_ambiguous_call_not_retried_or_logged(client, http_mcp, monkeypatch):
    config = mcp_config(client, project(client), http_mcp)
    original = feature._http_exchange
    calls = []

    def exchange(url, payload, headers, allow_private, deadline, method='POST'):
        if payload and payload.get('method') == 'tools/call':
            calls.append(payload)
            raise TimeoutError('untrusted-remote-error ' + http_mcp['token'])
        return original(url, payload, headers, allow_private, deadline, method)

    monkeypatch.setattr(feature, '_http_exchange', exchange)
    response = client.post(f'/api/mcp/{config["id"]}/call', json={'tool': 'echo', 'arguments': {'secret': 'synthetic-user-value'}, 'confirmed': True})
    assert response.status_code == 502
    assert len(calls) == 1 and http_mcp['token'] not in response.text
    store = client.app.state.service.store
    event = store.list('integration_mcp_event', config['project_id'])[0]
    assert event['status'] == 'FAILED_OR_UNKNOWN'
    assert 'synthetic-user-value' not in json.dumps(store.list())
    assert http_mcp['token'] not in json.dumps(store.audits())


def test_config_changed_during_discovery_prevents_call(client, http_mcp, monkeypatch):
    config = mcp_config(client, project(client), http_mcp)
    original = feature.MCPClient.tools

    def tools(mcp):
        result = original(mcp)
        client.app.state.service.store.update(config['id'], {'allowed_tools': []}, 'synthetic-admin', config['revision'])
        return result

    monkeypatch.setattr(feature.MCPClient, 'tools', tools)
    response = client.post(f'/api/mcp/{config["id"]}/call', json={'tool': 'echo', 'confirmed': True})
    assert response.status_code == 409
    assert not any(x.get('body', {}).get('method') == 'tools/call' for x in http_mcp['requests'])


def test_secret_redaction_in_keys_and_dns_deadline(monkeypatch):
    token = 'synthetic-access-value'
    redacted = feature._redact({token: [token], 'nested': {'sessionId': token}}, [token])
    assert token not in json.dumps(redacted)
    release = threading.Event()

    def slow_dns(*args, **kwargs):
        release.wait(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]

    monkeypatch.setattr(socket, 'getaddrinfo', slow_dns)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            feature._resolve('synthetic.invalid', 80, True, started + .05)
        assert time.monotonic() - started < .5
    finally:
        release.set()


def test_mcp_sse_rejects_malformed_or_unmatched_responses():
    # The bounded parser rejects unsupported or malformed JSON/SSE forms.
    for raw in (b'data: []\n\n', b'data: {bad}\n\n', b'data: {"jsonrpc":"2.0","id":2,"result":{}}\n\n'):
        with pytest.raises(ValueError):
            feature._sse_response(raw, 1)


def test_package_member_size_limit_and_archived_export(client, monkeypatch):
    pid = project(client)
    package = upload(client, pid).json()
    client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 1, 'archived': True, 'confirmed': True})
    assert client.post(f'/api/projects/{pid}/packages/docker-context', json={'package_ids': [package['id']]}).status_code == 400
    monkeypatch.setattr(feature, 'MEMBER_LIMIT', 10)
    assert upload(client, pid, 'large.zip', zipped([('a.py', b'x' * 11)]), version='2').status_code == 400
    assert upload(client, pid, 'large.tar.gz', tarred([('a.py', b'x' * 11, tarfile.REGTYPE)]), version='2').status_code == 400


def test_role_constraints_and_project_isolation(tmp_path, http_mcp):
    identities = {'a' * 16: {'user': 'admin', 'role': 'admin', 'projects': ['*']},
                  'd' * 16: {'user': 'developer', 'role': 'developer', 'projects': ['*']},
                  'v' * 16: {'user': 'viewer', 'role': 'auditor', 'projects': ['*']},
                  'z' * 16: {'user': 'outsider', 'role': 'admin', 'projects': []}}
    app = create_app(tmp_path, identities)
    feature.install(app)
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + 'a' * 16
        pid = project(client)
        package = upload(client, pid).json()
        config = mcp_config(client, pid, http_mcp)
        client.headers['Authorization'] = 'Bearer ' + 'd' * 16
        assert client.post(f'/api/projects/{pid}/mcp', json={'name': 'x', 'url': http_mcp['url']}).status_code == 403
        assert client.post(f'/api/projects/{pid}/runtimes', json={'image': 'sha256:' + 'a' * 64, 'argv': ['python'], 'confirmed': True}).status_code == 403
        client.headers['Authorization'] = 'Bearer ' + 'v' * 16
        assert client.get(f'/api/projects/{pid}/mcp').status_code == 200
        assert upload(client, pid, version='2').status_code == 403
        assert client.post(f'/api/mcp/{config["id"]}/probe').status_code == 403
        assert client.post(f'/api/mcp/{config["id"]}/call', json={'tool': 'echo', 'confirmed': True}).status_code == 403
        client.headers['Authorization'] = 'Bearer ' + 'z' * 16
        assert client.get(f'/api/packages/{package["id"]}/download').status_code == 403
        assert client.get(f'/api/projects/{pid}/packages').status_code == 403
        assert client.post(f'/api/mcp/{config["id"]}/probe').status_code == 403


@pytest.fixture
def docker_fixture(monkeypatch):
    """Docker command argument fixture; deliberately does not start Docker."""
    state = {'calls': [], 'containers': {}, 'image': 'sha256:' + 'a' * 64}

    def docker(args, timeout=10):
        state['calls'].append(args)
        if args[0] == 'info':
            return json.dumps('synthetic-daemon')
        if args[:2] == ['image', 'inspect']:
            return json.dumps(state['image'])
        if args[:2] == ['container', 'create']:
            name = args[args.index('--name') + 1]
            labels = dict(args[i + 1].split('=', 1) for i, arg in enumerate(args) if arg == '--label')
            cid = 'b' * 64
            state['containers'][cid] = {'Id': cid, 'Name': '/' + name, 'Config': {'Labels': labels}, 'State': {'Running': False}}
            return cid
        cid = args[-1]
        if args[:2] == ['container', 'inspect']:
            return json.dumps([state['containers'][cid]])
        if args[:2] == ['container', 'start']:
            state['containers'][cid]['State']['Running'] = True
            return cid
        if args[:2] == ['container', 'stop']:
            state['containers'][cid]['State']['Running'] = False
            return cid
        if args[:2] == ['container', 'rm']:
            del state['containers'][cid]
            return cid
        raise AssertionError(args)

    monkeypatch.setattr(feature, '_docker', docker)
    return state


def test_runtime_unavailable_and_invalid_lifecycle_arguments(client, monkeypatch):
    monkeypatch.setattr(feature.shutil, 'which', lambda _: None)
    assert client.get('/api/runtime/status').json()['available'] is False
    pid = project(client)
    body = {'image': 'sha256:' + 'a' * 64, 'argv': ['python', '-c', 'print(1)'], 'confirmed': True}
    assert client.post(f'/api/projects/{pid}/runtimes', json=body).status_code == 503
    assert client.get(f'/api/projects/{pid}/runtimes').json() == []
    for change in ({'image': 'python:latest; touch /tmp/nope'}, {'memory_mib': 99999}, {'cpus': 99}, {'ttl_seconds': 10000}, {'argv': []}, {'confirmed': False}, {'network': 'host'}, {'volumes': ['/etc:/etc']}):
        assert client.post(f'/api/projects/{pid}/runtimes', json={**body, **change}).status_code == 422
    for argv in (['-c', 'echo unsafe'], ['echo', 'bad\nargument'], ['x' * 1001]):
        assert client.post(f'/api/projects/{pid}/runtimes', json={**body, 'argv': argv}).status_code == 400


def test_docker_fixture_owned_lifecycle_resource_bounds_and_archive_refs(client, docker_fixture):
    pid = project(client)
    package = upload(client, pid).json()
    body = {'image': docker_fixture['image'], 'argv': ['python', '-c', 'print("literal;$(no-host-shell)")'],
            'package_ids': [package['id']], 'memory_mib': 128, 'cpus': .2, 'ttl_seconds': 300, 'confirmed': True}
    response = client.post(f'/api/projects/{pid}/runtimes', json=body)
    assert response.status_code == 201, response.text
    runtime = response.json()
    assert runtime['status'] == 'RUNNING'
    args = next(x for x in docker_fixture['calls'] if x[:2] == ['container', 'create'])
    assert args[args.index('--network') + 1] == 'none'
    assert args[args.index('--memory') + 1] == args[args.index('--memory-swap') + 1] == '128m'
    assert '--read-only' in args and '--cap-drop' in args and '--pids-limit' in args
    assert not any(x in args for x in ('--privileged', '--volume', '-v', '-p', '--publish', '--gpus'))
    assert args[-3:] == body['argv']
    assert 'timeout=300' in args[-4]
    assert client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 1, 'archived': True, 'confirmed': True}).status_code == 400
    path = f'/api/runtimes/{runtime["id"]}/action'
    assert client.post(path, json={'action': 'stop', 'expected_revision': 1, 'confirmed': True}).status_code == 409
    response = client.post(path, json={'action': 'stop', 'expected_revision': runtime['revision'], 'confirmed': True})
    assert response.status_code == 200, response.text
    stopped = response.json()
    assert stopped['status'] == 'STOPPED'
    assert client.post(f'/api/packages/{package["id"]}/archive', json={'expected_revision': 1, 'archived': True, 'confirmed': True}).status_code == 200
    response = client.post(path, json={'action': 'remove', 'expected_revision': stopped['revision'], 'confirmed': True})
    assert response.json()['status'] == 'REMOVED'
    assert docker_fixture['containers'] == {}


def test_docker_fixture_refuses_unowned_container(client, docker_fixture):
    pid = project(client)
    runtime = client.post(f'/api/projects/{pid}/runtimes', json={'image': docker_fixture['image'], 'argv': ['true'], 'confirmed': True}).json()
    docker_fixture['containers'][runtime['container_id']]['Config']['Labels']['io.ard.owner'] = 'unrelated-owner'
    count = len(docker_fixture['calls'])
    response = client.post(f'/api/runtimes/{runtime["id"]}/action', json={'action': 'remove', 'expected_revision': runtime['revision'], 'confirmed': True})
    assert response.status_code == 403
    assert not any(x[:2] in (['container', 'stop'], ['container', 'rm']) for x in docker_fixture['calls'][count:])
    assert runtime['container_id'] in docker_fixture['containers']


def test_docker_fixture_rechecks_ownership_before_delete(client, docker_fixture, monkeypatch):
    pid = project(client)
    runtime = client.post(f'/api/projects/{pid}/runtimes', json={'image': docker_fixture['image'], 'argv': ['true'], 'confirmed': True}).json()
    original = feature._docker

    def changed(args, timeout=10):
        result = original(args, timeout)
        if args[:2] == ['container', 'stop']:
            docker_fixture['containers'][runtime['container_id']]['Config']['Labels']['io.ard.project'] = 'different-project'
        return result

    monkeypatch.setattr(feature, '_docker', changed)
    response = client.post(f'/api/runtimes/{runtime["id"]}/action', json={'action': 'remove', 'expected_revision': runtime['revision'], 'confirmed': True})
    assert response.status_code == 403
    assert not any(x[:2] == ['container', 'rm'] for x in docker_fixture['calls'])


def test_docker_fixture_expiry_stops_only_owned_instance(client, docker_fixture):
    pid = project(client)
    runtime = client.post(f'/api/projects/{pid}/runtimes', json={'image': docker_fixture['image'], 'argv': ['true'], 'confirmed': True}).json()
    feature._expire_runtime(client.app.state.service, runtime['id'])
    stopped = client.get(f'/api/projects/{pid}/runtimes').json()[0]
    assert stopped['status'] == 'STOPPED' and stopped['stop_reason'] == 'ttl'
    assert docker_fixture['containers'][runtime['container_id']]['State']['Running'] is False


def test_new_service_reads_persisted_packages_and_mcp_private_config(tmp_path, http_mcp):
    app = create_app(tmp_path)
    feature.install(app)
    with TestClient(app) as client:
        pid = project(client)
        package = upload(client, pid).json()
        config = mcp_config(client, pid, http_mcp)
    app2 = create_app(tmp_path)
    feature.install(app2)
    with TestClient(app2) as client:
        assert client.get(f'/api/projects/{pid}/packages').json()[0]['id'] == package['id']
        assert client.post(f'/api/mcp/{config["id"]}/probe').status_code == 200
