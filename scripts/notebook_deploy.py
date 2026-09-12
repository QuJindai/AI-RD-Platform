"""Install, start and verify an isolated AI-RD deployment from a bundled wheel.

Python 3.8+ bootstrap; application gets its own Python 3.12 environment.
This script does not log in to ModelScope, provision hardware, or publish ports.
"""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

UV_VERSION = '0.12.11'
MARKER = '.ard-notebook-managed.json'


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')
    os.replace(temporary, path)


def prepare_root(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('部署目录必须是独立的managed目录，不能是符号链接')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = path / MARKER
    if marker.exists():
        if json.loads(marker.read_text()) != {'format': 'ai-rd-notebook', 'version': 1}:
            raise ValueError('目录管理标记不匹配')
    else:
        if any(path.iterdir()):
            raise ValueError('拒绝接管非本工具管理的已有目录 (not managed)')
        write_json(marker, {'format': 'ai-rd-notebook', 'version': 1})
    os.chmod(path, 0o700)
    return path


def load_access(root):
    path = root / 'private-access.json'
    if path.exists():
        result = json.loads(path.read_text())
    else:
        result = {'admin_token': secrets.token_urlsafe(32), 'review_token': secrets.token_urlsafe(32)}
        write_json(path, result)
    if (any(not isinstance(result.get(k), str) or len(result[k]) < 32 for k in ('admin_token', 'review_token'))
            or result['admin_token'] == result['review_token']):
        raise ValueError('已有身份配置损坏；未覆盖访问口令')
    os.chmod(path, 0o600)
    return result


def process_alive(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError('无效的进程 process 标识')
    # Use namespace-local OS calls: some Notebook containers mount the host's
    # /proc, whose numeric PIDs do not identify processes in this kernel.
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # A kernel restart may make this service another process's child.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def owned_process(root, state):
    if not process_alive(state.get('pid')):
        return False
    if not state.get('instance_id') or not (root / 'private-access.json').is_file():
        raise ValueError('进程身份缺失，拒绝停止或接管其他 process')
    access = load_access(root)
    try:
        current = request_json(state['port'], '/api/runtime/identity', access['admin_token'])
    except (OSError, ValueError, KeyError, URLError) as exc:
        raise ValueError('无法核对进程身份，未停止或接管其他 process') from exc
    if (current.get('service') != 'AI-RD-Platform' or current.get('pid') != state['pid']
            or current.get('instance_id') != state['instance_id']):
        raise ValueError('进程身份不匹配，拒绝停止或接管其他 process')
    return True


def stop_server(root):
    path = root / 'server.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    if state.get('pid') and owned_process(root, state):
        os.kill(state['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 15
        while process_alive(state['pid']):
            if time.monotonic() > deadline:
                raise RuntimeError('服务仍在优雅退出，未强制终止进程')
            time.sleep(.1)
    write_json(path, {'status': 'STOPPED'})
    return {'status': 'STOPPED', 'data_preserved': True}


def clean_app_environment():
    # Never inherit another deployment's identities, connectors or database configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith('ARD_') and k != 'PYTHONPATH'}
    env.update(PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1')
    return env


def run_logged(command, root, env=None, timeout=900):
    log = root / 'install.log'
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as stream:
        result = subprocess.run([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT,
                                env=env, cwd=root, timeout=timeout)
    if result.returncode:
        raise RuntimeError('安装步骤失败，详情保存在本机 install.log；未将日志加入验收包')


def install(root, payload, wheel):
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    marker = root / 'installed.json'
    python = root / 'venv/bin/python'
    if marker.exists() and python.exists() and json.loads(marker.read_text()).get('wheel_sha256') == digest:
        return python, digest
    state_file = root / 'server.json'
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state.get('pid') and owned_process(root, state):
            raise ValueError('升级安装前请先运行 action="stop"；运行中的服务未被修改')
    bootstrap = root / 'bootstrap'
    env = clean_app_environment()
    if not (bootstrap / 'uv/__main__.py').exists():
        print('安装独立的 uv 引导工具…', flush=True)
        run_logged([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                    '--target', bootstrap, 'uv==' + UV_VERSION], root, env)
    env.update(PYTHONPATH=str(bootstrap), UV_CACHE_DIR=str(root / 'cache'),
               UV_PYTHON_INSTALL_DIR=str(root / 'python'), UV_PYTHON_BIN_DIR=str(root / 'bin'))
    uv = [sys.executable, '-m', 'uv']
    if not python.exists():
        print('创建独立 Python 3.12 环境…', flush=True)
        run_logged(uv + ['venv', '--python', '3.12', str(root / 'venv')], root, env)
    print('安装锁定依赖与内置程序包…', flush=True)
    run_logged(uv + ['pip', 'install', '--python', str(python), '-r', str(payload / 'requirements.lock')], root, env)
    run_logged(uv + ['pip', 'install', '--python', str(python), '--no-deps', str(wheel)], root, env)
    run_logged(uv + ['pip', 'check', '--python', str(python)], root, env)
    write_json(marker, {'wheel_sha256': digest, 'uv_version': UV_VERSION})
    return python, digest


def request_json(port, path, token=None):
    request = Request('http://127.0.0.1:' + str(port) + path,
                      headers={'Authorization': 'Bearer ' + token} if token else {})
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def start_server(root, python, digest, access, port, public_origin):
    state_file = root / 'server.json'
    old = json.loads(state_file.read_text()) if state_file.exists() else {}
    if old.get('pid') and owned_process(root, old):
        if (old['wheel_sha256'] != digest or old.get('public_origin', '') != public_origin
                or (port and old['port'] != port)):
            raise ValueError('服务正在运行且配置不同；请先执行 action="stop"')
        request_json(old['port'], '/api/me', access['admin_token'])
        return old, True
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
        port = sock.getsockname()[1]
    identities = {
        access['admin_token']: {'user': 'notebook-owner', 'role': 'admin', 'projects': ['*']},
        access['review_token']: {'user': 'notebook-reviewer', 'role': 'reviewer', 'projects': ['*']},
    }
    env = clean_app_environment()
    instance_id = secrets.token_hex(32)
    env.update(ARD_IDENTITIES=json.dumps(identities), ARD_PUBLIC_ORIGIN=public_origin,
               ARD_INSTANCE_ID=instance_id)
    command = [str(python), '-m', 'ard', '--host', '127.0.0.1', '--port', str(port),
               '--data-dir', str(root / 'data')]
    fd = os.open(root / 'server.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as log:
        process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    state = {'pid': process.pid, 'instance_id': instance_id, 'port': port, 'wheel_sha256': digest,
             'public_origin': public_origin}
    try:
        write_json(state_file, state)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('服务启动失败；详情保存在本机 server.log')
            try:
                health = request_json(port, '/health')
                if health.get('service') == 'AI-RD-Platform' and owned_process(root, state):
                    return state, False
            except (OSError, ValueError, URLError):
                pass
            time.sleep(.2)
        raise RuntimeError('服务健康检查超时；详情保存在本机 server.log')
    except Exception:
        # This Popen handle belongs to the launch above, including before HTTP
        # becomes available. Never select an unknown process for startup cleanup.
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
        write_json(state_file, {'status': 'STOPPED'})
        raise


CUDA_PROBE = r'''
import json, time
r = {'status': 'UNVERIFIED'}
try:
    import torch
except Exception:
    r['reason'] = 'PYTORCH_UNAVAILABLE'
else:
    r.update(pytorch=torch.__version__, cuda_runtime=torch.version.cuda)
    if not torch.cuda.is_available():
        r.update(status='UNAVAILABLE', reason='CUDA_NOT_AVAILABLE')
    else:
        try:
            torch.manual_seed(42)
            torch.backends.cuda.matmul.allow_tf32 = False
            n = 1024
            a, b = torch.randn(n, n), torch.randn(n, n)
            expected = a @ b
            x, y = a.cuda(), b.cuda()
            for _ in range(3):
                z = x @ y
            torch.cuda.synchronize()
            elapsed = []
            for _ in range(10):
                before = time.perf_counter()
                z = x @ y
                torch.cuda.synchronize()
                elapsed.append((time.perf_counter() - before) * 1000)
            actual = z.cpu()
            ok = bool(torch.allclose(actual, expected, rtol=1e-3, atol=1e-3))
            r.update(status='PASS' if ok else 'FAIL', device=torch.cuda.get_device_name(0),
                     available_devices=torch.cuda.device_count(), dimensions=[n, n], dtype='float32',
                     iterations=10, mean_ms=sum(elapsed)/len(elapsed),
                     max_absolute_error=float((actual-expected).abs().max()),
                     memory_allocated_bytes=torch.cuda.max_memory_allocated())
        except Exception as exc:
            r.update(status='FAIL', reason=type(exc).__name__)
print('ARD_GPU_JSON=' + json.dumps(r, allow_nan=False))
'''


def probe_gpu(python_command, output):
    output = Path(output)
    result = {'status': 'UNVERIFIED', 'hardware': [], 'workload': 'CUDA FP32 matrix multiplication',
              'application_training_backend': 'CPU scikit-learn'}
    try:
        smi = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10)
        if smi.returncode == 0:
            for row in csv.reader(io.StringIO(smi.stdout)):
                if len(row) == 3:
                    result['hardware'].append({'name': row[0].strip(), 'memory_mib': row[1].strip(),
                                               'driver_version': row[2].strip()})
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        probe = subprocess.run(list(python_command) + ['-c', CUDA_PROBE], capture_output=True,
                               text=True, timeout=90)
        lines = [line for line in probe.stdout.splitlines() if line.startswith('ARD_GPU_JSON=')]
        cuda = json.loads(lines[-1].split('=', 1)[1]) if lines else {'status': 'UNVERIFIED', 'reason': 'PROBE_NO_RESULT'}
        if probe.returncode:
            cuda = {'status': 'UNVERIFIED', 'reason': 'PROBE_PROCESS_FAILED'}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        cuda = {'status': 'UNVERIFIED', 'reason': type(exc).__name__}
    result['cuda_test'] = cuda
    result['status'] = cuda['status']
    write_json(output / 'gpu.json', result)
    return result


def verify(root, payload, python, state, access, gpu_python):
    output = root / 'reports' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
    output.mkdir(parents=True, mode=0o700)
    report = {'format': 'ai-rd-notebook-acceptance', 'status': 'PARTIAL',
              'wheel_sha256': state['wheel_sha256'], 'port': state['port'],
              'environment': {'platform': platform.platform(), 'bootstrap_python': platform.python_version()},
              'not_verified': ['External browser rendering and platform port routing',
                               'GPU model training/inference, Docker and external LLM services'], 'tests': {}}
    env = clean_app_environment()
    env.update(ARD_TOKEN=access['admin_token'], ARD_REVIEW_TOKEN=access['review_token'])
    for name, script in [('smoke', 'smoke.py'), ('functional', 'functional_smoke.py')]:
        print('执行 ' + name + ' HTTP 验收…', flush=True)
        target = output / (name + '.json')
        try:
            run = subprocess.run([str(python), str(payload / script), '--base-url',
                                  'http://127.0.0.1:' + str(state['port']), '--output', str(target)],
                                 cwd=root, env=env, capture_output=True, timeout=300)
            if not target.exists():
                log = root / ('acceptance-' + output.name + '-' + name + '.log')
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write((run.stdout + run.stderr)[-65536:])
                write_json(target, {'status': 'FAIL', 'phase': name, 'reason': 'SCRIPT_NO_REPORT',
                                    'exit_code': run.returncode, 'details_local': log.name})
            data = json.loads(target.read_text())
            status = 'PASS' if run.returncode == 0 and data.get('status') == 'PASS' else 'FAIL'
            report['tests'][name] = {'status': status, 'checks': len(data.get('checks', [])),
                                    'duration_seconds': data.get('duration_seconds')}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report['tests'][name] = {'status': 'FAIL', 'reason': type(exc).__name__}
    print('检测实际 GPU 与 CUDA 计算…', flush=True)
    report['gpu'] = probe_gpu([str(gpu_python)], output)
    app_ok = all(v['status'] == 'PASS' for v in report['tests'].values())
    report['application_status'] = 'PASS' if app_ok else 'FAIL'
    report['status'] = ('PASS' if report['gpu']['status'] == 'PASS' else 'PARTIAL') if app_ok else 'FAIL'
    try:
        request_json(state['port'], '/api/me')
        report['anonymous_api_status'] = 'FAIL'
        report['status'] = 'FAIL'
    except HTTPError as exc:
        report['anonymous_api_status'] = 'PASS' if exc.code == 401 else 'FAIL'
        if exc.code != 401:
            report['status'] = 'FAIL'
    write_json(output / 'summary.json', report)
    # Only named evidence files are exported. Logs, runtime database, credentials and notebooks are excluded.
    archive = output / 'acceptance.zip'
    manifest = []
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in ('smoke.json', 'functional.json', 'gpu.json', 'summary.json'):
            path = output / name
            if not path.exists():
                continue
            content = path.read_text()
            for token in access.values():
                content = content.replace(token, '[REDACTED]')
            raw = content.encode()
            bundle.writestr(name, raw)
            manifest.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)})
        bundle.writestr('manifest.json', json.dumps({'files': manifest}, indent=2))
    os.chmod(archive, 0o600)
    report['report_archive'] = str(archive)
    return report


def _deploy(payload, root, port=8000, public_origin='', gpu_python=None, action='deploy'):
    if sys.platform != 'linux':
        raise ValueError('此部署入口面向 Linux Notebook')
    if not 0 <= int(port) <= 65535:
        raise ValueError('端口应在0到65535之间')
    # Reject URLs carrying credentials/paths before any install or service mutation.
    if public_origin:
        parts = urlsplit(public_origin)
        if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password
                or parts.path not in ('', '/') or parts.query or parts.fragment
                or any(c.isspace() or c in '*\\' for c in public_origin)):
            raise ValueError('public_origin仅填写网站来源，如https://notebook.example，不含路径或参数')
        public_origin = public_origin.rstrip('/')
    root = prepare_root(root)
    import fcntl
    with open(root / '.deployment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'stop':
            return stop_server(root)
        payload = Path(payload).resolve()
        wheels = list(payload.glob('ai_rd_platform-*.whl'))
        if len(wheels) != 1:
            raise ValueError('部署包必须包含且仅包含一个AI-RD wheel')
        access = load_access(root)
        python, digest = install(root, payload, wheels[0])
        state, reused = start_server(root, python, digest, access, int(port), public_origin)
        report = verify(root, payload, python, state, access, gpu_python or sys.executable)
        report['server_reused'] = reused
        report['root'] = str(root)
        return report


def deploy(payload, root, port=8000, public_origin='', gpu_python=None, action='deploy'):
    try:
        return _deploy(payload, root, port, public_origin, gpu_python, action)
    except Exception as exc:
        root = Path(root).expanduser().absolute()
        marker = root / MARKER
        if not marker.is_file() or json.loads(marker.read_text()) != {'format': 'ai-rd-notebook', 'version': 1}:
            raise
        report = {'status': 'FAIL', 'phase': 'deployment', 'reason': type(exc).__name__, 'message': str(exc)}
        output = root / 'reports' / ('failure-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
        output.mkdir(parents=True, mode=0o700)
        write_json(output / 'summary.json', report)
        raw = (output / 'summary.json').read_bytes()
        archive = output / 'acceptance.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('summary.json', raw)
            bundle.writestr('manifest.json', json.dumps({'files': [{'path': 'summary.json',
                'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}]}))
        os.chmod(archive, 0o600)
        report['report_archive'] = str(archive)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--public-origin', default='')
    parser.add_argument('--gpu-python', default=sys.executable)
    parser.add_argument('--action', choices=['deploy', 'stop'], default='deploy')
    args = parser.parse_args()
    try:
        report = deploy(args.payload, args.root, args.port, args.public_origin, args.gpu_python, args.action)
    except Exception as exc:
        print(json.dumps({'status': 'FAIL', 'phase': 'deployment', 'reason': type(exc).__name__,
                          'message': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] in ('PASS', 'STOPPED') else 2 if report['status'] == 'PARTIAL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
