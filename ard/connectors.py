"""Adapters use administrator-provided endpoints, never endpoints from model output."""
import os
from urllib.parse import urlsplit

import httpx
import numpy as np


def capabilities():
    return {'ollama': {'configured': bool(os.environ.get('ARD_OLLAMA_URL')), 'live_verified': False,
                      'chat_model_configured': bool(os.environ.get('ARD_CHAT_MODEL')),
                      'embedding_model_configured': bool(os.environ.get('ARD_EMBED_MODEL'))},
            'cpu_training': {'configured': True, 'engine': 'scikit-learn'},
            'gpu_scheduler': {'configured': False, 'state': 'not_implemented'},
            'mcp_runtime': {'configured': False, 'state': 'not_implemented'}}


def _call(path, payload=None):
    base = os.environ.get('ARD_OLLAMA_URL', '').rstrip('/')
    if not base:
        raise ValueError('Ollama尚未连接：请在服务器配置ARD_OLLAMA_URL')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Ollama端点配置无效')
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=5), follow_redirects=False, trust_env=False) as client:
            method = 'POST' if payload is not None else 'GET'
            with client.stream(method, base + path, json=payload) as response:
                if response.status_code != 200:
                    raise ValueError(f'Ollama调用失败，HTTP {response.status_code}')
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise ValueError('Ollama响应超过8MiB限制')
                    chunks.append(chunk)
        import json
        return json.loads(b''.join(chunks))
    except httpx.HTTPError as exc:
        raise ValueError('无法连接Ollama或请求超时') from exc
    except (TypeError, KeyError) as exc:
        raise ValueError('Ollama响应结构无效') from exc


def ollama_probe():
    data = _call('/api/tags')
    models = data.get('models')
    if not isinstance(models, list):
        raise ValueError('Ollama模型目录响应无效')
    return {'status': 'connected', 'models': [m.get('name', '') for m in models if isinstance(m, dict)], 'live_verified': True}


def chat(prompt):
    model = os.environ.get('ARD_CHAT_MODEL')
    if not model:
        raise ValueError('未配置ARD_CHAT_MODEL，不能执行LLM节点')
    data = _call('/api/chat', {'model': model, 'stream': False, 'messages': [{'role': 'user', 'content': prompt}]})
    message = data.get('message', {}).get('content')
    if not isinstance(message, str):
        raise ValueError('Ollama聊天响应缺少文本')
    return {'answer': message, 'model': model, 'eval_count': data.get('eval_count')}


def embeddings(texts):
    model = os.environ.get('ARD_EMBED_MODEL')
    if not model:
        raise ValueError('语义检索需要配置ARD_EMBED_MODEL和Ollama服务')
    vectors = []
    for i in range(0, len(texts), 32):
        data = _call('/api/embed', {'model': model, 'input': texts[i:i + 32]})
        batch = data.get('embeddings')
        if not isinstance(batch, list) or len(batch) != len(texts[i:i + 32]):
            raise ValueError('Ollama向量数量与输入不匹配')
        vectors.extend(batch)
    try:
        array = np.asarray(vectors, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Ollama向量结构无效') from exc
    if array.ndim != 2 or array.shape[1] == 0 or not np.isfinite(array).all():
        raise ValueError('Ollama返回了无效向量')
    return array
