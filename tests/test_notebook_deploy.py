"""Deployment boundaries: explicit browser origin, private state, safe recovery."""
import importlib.util
import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from ard.api import create_app

ROOT = Path(__file__).resolve().parents[1]
IDENTITIES = {'synthetic-notebook-admin-token': {'user': 'owner', 'role': 'admin', 'projects': ['*']}}


def test_explicit_notebook_origin_with_auth_works_through_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv('ARD_PUBLIC_ORIGIN', 'https://notebook.example')
    with TestClient(create_app(tmp_path, IDENTITIES)) as client:
        headers = {'host': 'notebook.example', 'origin': 'https://notebook.example',
                   'authorization': 'Bearer synthetic-notebook-admin-token'}
        assert client.get('/api/me', headers=headers).status_code == 200
        assert client.get('/api/me', headers={**headers, 'origin': 'https://other.example'}).status_code == 403
        assert client.get('/api/me', headers={**headers, 'authorization': ''}).status_code == 401
        assert client.get('/api/me', headers={**headers, 'host': 'other.example'}).status_code == 400


@pytest.mark.parametrize('origin', ['https://user:secret@notebook.example', 'https://notebook.example/?token=secret',
                                  'https://notebook.example/path', 'https://*.example', 'javascript:alert(1)'])
def test_notebook_origin_rejects_non_origin_configuration(tmp_path, monkeypatch, origin):
    monkeypatch.setenv('ARD_PUBLIC_ORIGIN', origin)
    with pytest.raises(ValueError, match='origin|Origin|来源'):
        create_app(tmp_path, IDENTITIES)


def test_public_origin_never_enables_anonymous_admin(tmp_path, monkeypatch):
    monkeypatch.setenv('ARD_PUBLIC_ORIGIN', 'https://notebook.example')
    with pytest.raises(ValueError, match='身份|identit'):
        create_app(tmp_path, {})


def test_runtime_identity_requires_authenticated_global_admin(tmp_path, monkeypatch):
    monkeypatch.setenv('ARD_INSTANCE_ID', 'synthetic-launch-identity')
    identities = {**IDENTITIES,
                  'synthetic-reviewer': {'user': 'reviewer', 'role': 'reviewer', 'projects': ['*']},
                  'synthetic-scoped-admin': {'user': 'scoped', 'role': 'admin', 'projects': ['project-one']}}
    with TestClient(create_app(tmp_path, identities)) as client:
        endpoint = '/api/runtime/identity'
        assert client.get(endpoint).status_code == 401
        for token in ('synthetic-reviewer', 'synthetic-scoped-admin'):
            assert client.get(endpoint, headers={'authorization': 'Bearer ' + token}).status_code == 403
        response = client.get(endpoint, headers={'authorization': 'Bearer synthetic-notebook-admin-token'})
        assert response.status_code == 200
        assert response.json() == {'service': 'AI-RD-Platform', 'pid': os.getpid(),
                                   'instance_id': 'synthetic-launch-identity'}
        assert 'instance_id' not in client.get('/health').json()
    with TestClient(create_app(tmp_path / 'local', {})) as client:
        assert client.get(endpoint).status_code == 403


def load_deployer():
    path = ROOT / 'scripts/notebook_deploy.py'
    assert path.is_file(), 'Notebook deployment implementation is missing'
    spec = importlib.util.spec_from_file_location('notebook_deploy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployment_refuses_to_adopt_an_existing_unrelated_directory(tmp_path):
    deploy = load_deployer()
    (tmp_path / 'existing-work.txt').write_text('keep')
    with pytest.raises(ValueError, match='管理|managed'):
        deploy.prepare_root(tmp_path)
    assert (tmp_path / 'existing-work.txt').read_text() == 'keep'


def test_deployment_credentials_survive_rerun_and_remain_private(tmp_path, capsys):
    deploy = load_deployer()
    root = deploy.prepare_root(tmp_path / 'deployment')
    first = deploy.load_access(root)
    assert deploy.load_access(root) == first
    assert first['admin_token'] != first['review_token']
    assert len(first['admin_token']) >= 32
    assert (root / 'private-access.json').stat().st_mode & 0o777 == 0o600
    assert first['admin_token'] not in capsys.readouterr().out


def test_stop_refuses_an_unrelated_live_process(tmp_path):
    deploy = load_deployer()
    root = deploy.prepare_root(tmp_path / 'deployment')
    (root / 'server.json').write_text(json.dumps({'pid': os.getpid(), 'start_time': 'forged', 'port': 8000}))
    with pytest.raises(ValueError, match='进程|process'):
        deploy.stop_server(root)
    os.kill(os.getpid(), 0)


def test_missing_torch_cannot_be_reported_as_gpu_pass(tmp_path):
    deploy = load_deployer()
    # -S removes all site packages; this child exercises a real missing-dependency failure.
    report = deploy.probe_gpu([sys.executable, '-S'], tmp_path)
    assert report['status'] != 'PASS'
    assert report['cuda_test']['status'] == 'UNVERIFIED'
    assert report['cuda_test']['reason'] == 'PYTORCH_UNAVAILABLE'


def test_incomplete_payload_returns_downloadable_failure_evidence(tmp_path):
    import zipfile
    deploy = load_deployer()
    payload = tmp_path / 'incomplete'
    payload.mkdir()
    result = deploy.deploy(payload, tmp_path / 'deployment')
    assert result['status'] == 'FAIL'
    with zipfile.ZipFile(result['report_archive']) as archive:
        assert set(archive.namelist()) == {'summary.json', 'manifest.json'}
        assert json.loads(archive.read('summary.json'))['phase'] == 'deployment'
