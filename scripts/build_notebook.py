"""Build one self-contained deployment notebook from a verified application wheel."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(wheel, output):
    members = {wheel.name: wheel.read_bytes(), 'requirements.lock': (ROOT / 'requirements.lock').read_bytes(),
               'LICENSE': (ROOT / 'LICENSE').read_bytes()}
    for name in ('notebook_deploy.py', 'smoke.py', 'functional_smoke.py'):
        members[name] = (ROOT / 'scripts' / name).read_bytes()
    manifest = {'format': 'ai-rd-notebook-payload', 'version': 1,
                'files': [{'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
                          for name, raw in sorted(members.items())]}
    members['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            bundle.writestr(info, raw)
    raw = archive.getvalue()
    encoded, digest = base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest()
    code = '''# 只需运行这一个单元格。程序包已经内置；依赖仍需通过软件源下载。
from pathlib import Path
import base64, hashlib, io, json, os, runpy, sys, tempfile, zipfile
from IPython.display import display, HTML, FileLink
from html import escape

DEPLOY_DIR = (Path('/mnt/workspace') if Path('/mnt/workspace').is_dir() else Path.cwd()) / 'ai-rd-platform-notebook'
PORT = 8000
# 填写端口预览页的网站来源，例如 https://notebook.example（不含路径、账号或参数）。
# 留空仍可完成本机 HTTP 验收；如端口页面报 Invalid host / 跨来源，再填写此项并 stop 后重启。
PUBLIC_ORIGIN = ''
ACTION = 'deploy'  # 改成 'stop' 可停止本部署进程，保留数据与口令。
GPU_PYTHON = sys.executable  # 使用 Notebook 原有的 PyTorch/CUDA 环境探测 GPU。

PAYLOAD_SHA256 = '__DIGEST__'
PAYLOAD = '__PAYLOAD__'
raw = base64.b64decode(PAYLOAD, validate=True)
assert hashlib.sha256(raw).hexdigest() == PAYLOAD_SHA256, '部署包 SHA-256 校验失败'
with tempfile.TemporaryDirectory(prefix='ai-rd-installer-') as temporary:
    payload = Path(temporary)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        members = bundle.infolist()
        assert len(members) == len({member.filename for member in members}), '部署包成员重复'
        assert sum(member.file_size for member in members) <= 10 * 1024 * 1024, '部署包超出大小限制'
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['format'] == 'ai-rd-notebook-payload' and manifest['version'] == 1
        assert {member.filename for member in members} == {'manifest.json', *(item['path'] for item in manifest['files'])}
        for item in manifest['files']:
            name = item['path']
            assert name not in ('.', '..') and '/' not in name and '\\\\' not in name and ':' not in name
            content = bundle.read(name)
            assert len(content) == item['size_bytes'] and hashlib.sha256(content).hexdigest() == item['sha256'], name
            (payload / name).write_bytes(content)
    installer = runpy.run_path(str(payload / 'notebook_deploy.py'), run_name='ai_rd_installer')
    result = installer['deploy'](payload, DEPLOY_DIR, PORT, PUBLIC_ORIGIN, GPU_PYTHON, ACTION)

print(json.dumps(result, ensure_ascii=False, indent=2))
if result.get('report_archive'):
    print('验收包（仅此 ZIP 用于回传，不含访问口令、运行日志或业务数据库）：')
    display(FileLink(os.path.relpath(result['report_archive'], Path.cwd())))
if result.get('application_status') == 'PASS':
    print('在 Notebook 的端口/Port 面板打开端口', result['port'], '，访问地址末尾保留 /。')
    access = installer['load_access'](DEPLOY_DIR.absolute())
    display(HTML('<details><summary>查看本工作台访问口令（仅供本人使用）</summary><p>在工作台右上角凭据设置中填入：</p><code>'
                 + escape(access['admin_token']) + '</code><p>此输出含私密口令。请只回传上面的验收 ZIP。</p></details>'))
'''.replace('__DIGEST__', digest).replace('__PAYLOAD__', encoded)
    introduction = '''# AI-RD-Platform · 魔搭 Notebook 部署与验收

适用于已打开的 **Linux GPU Notebook**。在魔搭自身页面操作即可；本文件不登录魔搭、不创建或购买算力。

1. 将本文件上传到 Notebook 并打开。
2. 如已知端口预览域名，填写下方 `PUBLIC_ORIGIN`，只填 `https://域名` 部分。
3. 点击 **运行** 执行唯一代码单元格，等待安装、启动和验收结束。
4. 下载输出中的 **acceptance.zip** 并回传。打开 Port 面板中的 **8000** 端口可使用工作台。

程序 wheel、锁文件和测试脚本全部内置，无需 Git 克隆。首次仍需从 PyPI 等软件源下载依赖；
若环境缺少 Python 3.12，uv 会在本部署目录下载独立 Python，不替换 Notebook 的 Python/PyTorch。
服务默认只监听本机，并要求独立访问口令；不会建立公网隧道。

结果含 13 项基础 HTTP 验收、61 项功能验收、GPU 型号/驱动检测与真实 CUDA 矩阵计算。
缺少 GPU 或 PyTorch 会标为 **UNAVAILABLE / UNVERIFIED / PARTIAL**，不会当作通过。
应用现有机器学习训练使用 CPU；CUDA 检测通过不代表完成 GPU 模型训练、微调或推理服务接入。

重复运行会复用本部署的服务和口令，并新建合成验收项目。`ACTION='stop'` 只停止本工具的进程并保留数据。
服务随云 Notebook 停止而中断；目录是否持久保存以平台实际配置为准。

**回传 acceptance.zip 即可。运行后的 Notebook 输出可能含工作台访问口令，请勿公开分享这些输出。**
'''
    notebook = {'nbformat': 4, 'nbformat_minor': 5,
                'metadata': {'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
                             'language_info': {'name': 'python'}, 'ai_rd_payload_sha256': digest},
                'cells': [{'id': 'instructions', 'cell_type': 'markdown', 'metadata': {},
                           'source': introduction.splitlines(keepends=True)},
                          {'id': 'deploy-and-verify', 'cell_type': 'code', 'execution_count': None,
                           'metadata': {}, 'outputs': [], 'source': code.splitlines(keepends=True)}]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return {'notebook': str(output), 'payload_sha256': digest,
            'notebook_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'size_bytes': output.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'deploy/AI_RD_ModelScope.ipynb')
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.wheel.resolve(), arguments.output), indent=2))
