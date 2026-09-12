"""Bounded adapters for server-configured model services; never deserialize code.

Ollama remains the default. ARD_MODEL_PROVIDER=openai selects an OpenAI-compatible
server whose ARD_OPENAI_URL includes its API prefix (normally /v1). Credentials
remain in environment variables and are never included in public configuration.
"""
from __future__ import annotations

import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import time
from urllib.parse import urlsplit

import httpx
import numpy as np

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_DIMENSIONS = 8192
_MAX_VECTOR_VALUES = 8 * 1024 * 1024


def _provider():
    provider = os.environ.get('ARD_MODEL_PROVIDER', 'ollama').strip().lower()
    if provider not in ('ollama', 'openai'):
        raise ValueError('ARD_MODEL_PROVIDER只能配置为ollama或openai')
    return provider


def _base(provider):
    variable = 'ARD_OPENAI_URL' if provider == 'openai' else 'ARD_OLLAMA_URL'
    base = os.environ.get(variable, '').strip().rstrip('/')
    if not base:
        raise ValueError(f'模型服务尚未连接：请在服务器配置{variable}')
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError as exc:
        raise ValueError('模型服务端点配置无效') from exc
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or len(base) > 2048
            or any(ord(char) < 33 for char in base) or (port is not None and port < 1)):
        raise ValueError('模型服务端点配置无效')
    return base


def _model(provider, kind):
    variable = ('ARD_OPENAI_' if provider == 'openai' else 'ARD_') + kind.upper() + '_MODEL'
    value = os.environ.get(variable, '').strip()
    if not value or len(value) > 300 or any(ord(char) < 32 for char in value):
        raise ValueError(f'请在服务器配置有效的{variable}')
    return value


def _deadline():
    try:
        timeout = float(os.environ.get('ARD_MODEL_TIMEOUT_SECONDS', '45'))
    except ValueError as exc:
        raise ValueError('ARD_MODEL_TIMEOUT_SECONDS必须为1至120秒') from exc
    if not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError('ARD_MODEL_TIMEOUT_SECONDS必须为1至120秒')
    return time.monotonic() + timeout


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('模型服务请求超过总时限')
    return remaining


def _finite_json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('模型服务返回非有限数值')
    return result


def _bad_constant(value):
    raise ValueError('模型服务返回非标准JSON数值')


def _call(path, payload=None, *, provider='ollama', deadline=None):
    """One bounded JSON call. The legacy two-argument form still calls Ollama."""
    base = _base(provider)
    deadline = _deadline() if deadline is None else deadline
    remaining = _remaining(deadline)
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if provider == 'openai':
        key = os.environ.get('ARD_OPENAI_API_KEY', '')
        if key:
            if len(key) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key):
                raise ValueError('ARD_OPENAI_API_KEY配置无效')
            headers['Authorization'] = 'Bearer ' + key
    async def request_bytes():
        # Cancellation covers the entire exchange, including a slow trickle response.
        # The synchronous public functions retain compatibility with workflow callers.
        async with asyncio.timeout(_remaining(deadline)):
            async with httpx.AsyncClient(timeout=httpx.Timeout(remaining, connect=min(5, remaining),
                                                              read=min(5, remaining)),
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream('POST' if payload is not None else 'GET', base + path,
                                         json=payload, headers=headers) as response:
                    _remaining(deadline)
                    if response.status_code != 200:
                        raise ValueError(f'模型服务调用失败，HTTP {response.status_code}')
                    if response.headers.get('content-encoding', 'identity').lower() not in ('identity', ''):
                        raise ValueError('模型服务必须返回未压缩JSON')
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        _remaining(deadline)
                        size += len(chunk)
                        if size > _MAX_RESPONSE_BYTES:
                            raise ValueError('模型服务响应超过8MiB限制')
                        chunks.append(chunk)
                _remaining(deadline)
                return b''.join(chunks)
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            raw = asyncio.run(request_bytes())
        else:
            # A sync connector called from async application code must not nest loops.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ard-model-http') as executor:
                raw = executor.submit(lambda: asyncio.run(request_bytes())).result()
        data = json.loads(raw, parse_constant=_bad_constant, parse_float=_finite_json_float)
        _remaining(deadline)
        if not isinstance(data, dict):
            raise ValueError('模型服务响应必须为JSON对象')
        return data
    except httpx.HTTPError as exc:
        raise ValueError('无法连接模型服务或请求超时') from exc
    except TimeoutError as exc:
        raise ValueError('模型服务请求超过总时限') from exc
    except httpx.InvalidURL as exc:
        raise ValueError('模型服务端点配置无效') from exc
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('模型服务响应不是有效JSON对象') from exc


def capabilities():
    """Keep original capability keys; configuration is not proof of availability."""
    return {
        'ollama': {'configured': bool(os.environ.get('ARD_OLLAMA_URL')), 'live_verified': False,
                   'chat_model_configured': bool(os.environ.get('ARD_CHAT_MODEL')),
                   'embedding_model_configured': bool(os.environ.get('ARD_EMBED_MODEL'))},
        'openai': {'configured': bool(os.environ.get('ARD_OPENAI_URL')), 'live_verified': False,
                   'chat_model_configured': bool(os.environ.get('ARD_OPENAI_CHAT_MODEL')),
                   'embedding_model_configured': bool(os.environ.get('ARD_OPENAI_EMBED_MODEL')),
                   'api_key_configured': bool(os.environ.get('ARD_OPENAI_API_KEY'))},
        'cpu_training': {'configured': True, 'engine': 'scikit-learn'},
        'gpu_scheduler': {'configured': False, 'state': 'not_implemented'},
        'mcp_runtime': {'configured': False, 'state': 'not_implemented'},
    }


def configuration():
    """Public read-only status: endpoint is a digest, key presence is a boolean."""
    provider = _provider()
    configured = bool(os.environ.get('ARD_OPENAI_URL' if provider == 'openai' else 'ARD_OLLAMA_URL'))
    prefix = 'ARD_OPENAI_' if provider == 'openai' else 'ARD_'
    return {'provider': provider, 'configured': configured,
            'endpoint_sha256': hashlib.sha256(_base(provider).encode()).hexdigest() if configured else None,
            'chat_model': os.environ.get(prefix + 'CHAT_MODEL', ''),
            'embedding_model': os.environ.get(prefix + 'EMBED_MODEL', ''),
            'api_key_configured': bool(os.environ.get('ARD_OPENAI_API_KEY')) if provider == 'openai' else False,
            'editable': False, 'live_verified': False}


def embedding_identity():
    """Return {provider, model, endpoint_sha256} for persistent index binding."""
    provider = _provider()
    return {'provider': provider, 'model': _model(provider, 'embed'),
            'endpoint_sha256': hashlib.sha256(_base(provider).encode()).hexdigest()}


def _probe(provider):
    data = _call('/models' if provider == 'openai' else '/api/tags', provider=provider)
    items = data.get('data' if provider == 'openai' else 'models')
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError('模型目录响应无效或超过1000条')
    models = []
    for item in items:
        name = item.get('id' if provider == 'openai' else 'name') if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or len(name) > 300 or any(ord(c) < 32 for c in name):
            raise ValueError('模型目录必须包含有效的模型名称')
        models.append(name)
    return {'status': 'connected', 'models': models, 'live_verified': True, 'provider': provider}


def ollama_probe():
    return _probe('ollama')


def model_probe():
    return _probe(_provider())


def _text(value, limit=200000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'模型输入必须为1至{limit}字符的非空文本')
    return value


def _counter(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
        raise ValueError('模型服务用量字段无效')
    return value


def _finite_component(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def chat(prompt):
    provider = _provider()
    model = _model(provider, 'chat')
    payload = {'model': model, 'stream': False, 'messages': [{'role': 'user', 'content': _text(prompt)}]}
    data = _call('/chat/completions' if provider == 'openai' else '/api/chat', payload,
                 provider=provider, deadline=_deadline())
    if provider == 'openai':
        choices = data.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError('聊天响应必须包含一个回答')
        message = choices[0].get('message')
        usage = data.get('usage')
        if usage is not None and not isinstance(usage, dict):
            raise ValueError('聊天响应用量结构无效')
        count = _counter((usage or {}).get('completion_tokens'))
    else:
        message = data.get('message')
        count = _counter(data.get('eval_count'))
    answer = message.get('content') if isinstance(message, dict) else None
    if not isinstance(answer, str) or len(answer) > 500000:
        raise ValueError('聊天响应缺少文本或文本过长')
    return {'answer': answer, 'model': model, 'eval_count': count}


def embeddings(texts):
    provider = _provider()
    model = _model(provider, 'embed')
    if not isinstance(texts, list) or not 1 <= len(texts) <= 4096:
        raise ValueError('向量输入必须为1至4096条文本')
    for value in texts:
        _text(value)
    if sum(len(value.encode('utf-8')) for value in texts) > 2 * 1024 * 1024:
        raise ValueError('向量输入超过2MiB限制')
    vectors, width = [], None
    deadline = _deadline()
    for i in range(0, len(texts), 32):
        batch_texts = texts[i:i + 32]
        data = _call('/embeddings' if provider == 'openai' else '/api/embed',
                     {'model': model, 'input': batch_texts}, provider=provider, deadline=deadline)
        if provider == 'openai':
            items = data.get('data')
            if not isinstance(items, list) or len(items) != len(batch_texts):
                raise ValueError('向量数量与输入不匹配')
            indexed = {}
            for item in items:
                if not isinstance(item, dict) or type(item.get('index')) is not int:
                    raise ValueError('向量响应索引无效')
                index = item['index']
                if index in indexed or not 0 <= index < len(batch_texts):
                    raise ValueError('向量响应索引重复或越界')
                indexed[index] = item.get('embedding')
            batch = [indexed[index] for index in range(len(batch_texts))]
        else:
            batch = data.get('embeddings')
        if not isinstance(batch, list) or len(batch) != len(batch_texts):
            raise ValueError('向量数量与输入不匹配')
        for vector in batch:
            if not isinstance(vector, list) or not 1 <= len(vector) <= _MAX_DIMENSIONS:
                raise ValueError('向量维度必须为1至8192')
            width = len(vector) if width is None else width
            if len(vector) != width or width * len(texts) > _MAX_VECTOR_VALUES:
                raise ValueError('向量维度不一致或总规模过大')
            if any(not _finite_component(value) for value in vector):
                raise ValueError('模型服务返回了无效向量')
            vectors.append(vector)
        _remaining(deadline)
    return np.asarray(vectors, dtype=float)
