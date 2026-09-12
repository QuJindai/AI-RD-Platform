"""Execute the actual distributed notebook in an isolated Linux directory.

Exercises fresh installation, repeat execution, stop/restart, persisted data and
credential-free exported evidence. Does not claim browser or GPU availability.
"""
import argparse
import ast
import base64
from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import Request, urlopen
import zipfile

from verify_wheel import verify as verify_wheel


@contextmanager
def isolated_directory():
    path = Path(tempfile.mkdtemp(prefix='ard-notebook-release-'))
    try:
        yield path
    except Exception:
        print('Failed verification workspace retained at: ' + str(path), flush=True)
        raise
    else:
        shutil.rmtree(path)


def run(notebook, output):
    document = json.loads(notebook.read_text())
    cells = [cell for cell in document['cells'] if cell['cell_type'] == 'code']
    assert len(cells) == 1 and cells[0]['outputs'] == [], 'release must be unexecuted and have one code cell'
    source = ''.join(cells[0]['source'])
    original = ast.parse(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    with isolated_directory() as temporary:
        root = Path(temporary) / 'deployment'
        constants = {statement.targets[0].id: ast.literal_eval(statement.value)
                     for statement in original.body if isinstance(statement, ast.Assign)
                     and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
                     and statement.targets[0].id in ('PAYLOAD', 'PAYLOAD_SHA256')}
        raw = base64.b64decode(constants['PAYLOAD'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == constants['PAYLOAD_SHA256']
        repository = Path(__file__).resolve().parents[1]
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            for name in ('notebook_deploy.py', 'smoke.py', 'functional_smoke.py'):
                assert bundle.read(name) == (repository / 'scripts' / name).read_bytes(), 'stale payload: ' + name
            assert bundle.read('requirements.lock') == (repository / 'requirements.lock').read_bytes()
            wheel_name = next(name for name in bundle.namelist() if name.endswith('.whl'))
            wheel = Path(temporary) / wheel_name
            wheel.write_bytes(bundle.read(wheel_name))
            verify_wheel(wheel, repository)

        def execute(action='deploy', allow_failure=False):
            tree = ast.parse(source)
            overrides = {'DEPLOY_DIR': ast.parse('Path(' + repr(str(root)) + ')', mode='eval').body,
                         'PORT': ast.Constant(0), 'PUBLIC_ORIGIN': ast.Constant('https://notebook.example'),
                         'ACTION': ast.Constant(action)}
            changed = set()
            for statement in tree.body:
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                    target = statement.targets[0]
                    if isinstance(target, ast.Name) and target.id in overrides:
                        statement.value = overrides[target.id]
                        changed.add(target.id)
            assert changed == set(overrides), 'notebook configuration contract changed'
            namespace = {}
            captured = io.StringIO()
            with redirect_stdout(captured):
                exec(compile(ast.fix_missing_locations(tree), str(notebook), 'exec'), namespace)
            result = namespace['result']
            if result['status'] == 'FAIL' and not allow_failure:
                raise AssertionError('Notebook execution failed: ' + json.dumps(result, ensure_ascii=False))
            return result

        def state():
            return json.loads((root / 'server.json').read_text())

        def projects(access):
            request = Request('http://127.0.0.1:' + str(state()['port']) + '/api/projects',
                              headers={'Authorization': 'Bearer ' + access['admin_token']})
            with urlopen(request, timeout=10) as response:
                return {project['id'] for project in json.load(response)}

        print('Executing released notebook: fresh isolated install', flush=True)
        try:
            first = execute()
            assert first['application_status'] == 'PASS'
            first_state = state()
            access = json.loads((root / 'private-access.json').read_text())
            original_projects = projects(access)
            print('Executing released notebook: repeat, then stop/restart', flush=True)
            second = execute()
            assert second['server_reused'] and state()['pid'] == first_state['pid']
            assert json.loads((root / 'private-access.json').read_text()) == access
            assert original_projects <= projects(access)
            # A stale or altered launch marker must never authorize a signal.
            state_file = root / 'server.json'
            original_state = state_file.read_bytes()
            try:
                state_file.write_text(json.dumps({**state(), 'instance_id': 'incorrect-instance'}))
                rejected = execute('stop', allow_failure=True)
                assert rejected['status'] == 'FAIL' and rejected['reason'] == 'ValueError'
                assert original_projects <= projects(access), 'wrong identity stopped the live service'
            finally:
                state_file.write_bytes(original_state)
            stopped = execute('stop')
            assert stopped == {'status': 'STOPPED', 'data_preserved': True}
            try:
                os.kill(first_state['pid'], 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError('stop reported success while the service process remained alive')
            third = execute()
            assert not third['server_reused'] and state()['pid'] != first_state['pid']
            assert json.loads((root / 'private-access.json').read_text()) == access
            assert original_projects <= projects(access)
            for report in (first, second, third):
                assert report['application_status'] == 'PASS'
                assert report['tests']['smoke']['checks'] == 13
                assert report['tests']['functional']['checks'] == 61
                with zipfile.ZipFile(report['report_archive']) as bundle:
                    manifest = json.loads(bundle.read('manifest.json'))
                    assert set(bundle.namelist()) == {'manifest.json', *(entry['path'] for entry in manifest['files'])}
                    for entry in manifest['files']:
                        raw = bundle.read(entry['path'])
                        assert len(raw) == entry['size_bytes'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
                        assert all(token.encode() not in raw for token in access.values())
            with zipfile.ZipFile(first['report_archive']) as bundle:
                for name in ('smoke.json', 'functional.json', 'gpu.json', 'summary.json'):
                    (output.parent / ('notebook-' + name)).write_bytes(bundle.read(name))
            result = {'status': 'PASS', 'scope': 'Released notebook execution and recovery on local Linux',
                      'notebook_sha256': hashlib.sha256(notebook.read_bytes()).hexdigest(),
                      'payload_sha256': document['metadata']['ai_rd_payload_sha256'],
                      'wheel_sha256': first['wheel_sha256'], 'payload_matches_current_source': True,
                      'fresh_install': True, 'repeat_reused_server': True,
                      'identity_mismatch_rejected': True, 'stopped_process_exited': True,
                      'restart_preserved_projects': len(original_projects), 'credentials_preserved': True,
                      'exported_manifests_valid': True, 'credentials_excluded_from_reports': True,
                      'http_acceptance_each_run': {'baseline': 13, 'functional': 61}, 'runs': 3,
                      'gpu_result': first['gpu'], 'modelscope_execution': 'NOT_RUN',
                      'browser_rendering': 'NOT_RUN'}
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        finally:
            if (root / '.ard-notebook-managed.json').exists():
                execute('stop')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notebook', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.notebook, args.output), ensure_ascii=False, indent=2))
