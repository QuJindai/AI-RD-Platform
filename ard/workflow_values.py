"""Small JSON-only expressions; no attribute lookup, evaluation or host execution."""
import copy
import json
import math
import re


PATH = re.compile(r'[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*(?:\.(?:[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*|[0-9]+))*\Z')
TOKEN = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
MISSING = object()


def validate_path(path):
    if not isinstance(path, str) or len(path) > 200 or not PATH.fullmatch(path):
        raise ValueError('路径只能使用对象字段和点分隔的数组索引，不能包含表达式')
    return path


def lookup(value, path, default=MISSING):
    validate_path(path)
    current = value
    for part in path.split('.'):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        elif default is not MISSING:
            return default
        else:
            raise ValueError('输入缺少路径: ' + path)
    return copy.deepcopy(current)


def validate_template(template):
    if not isinstance(template, str) or not 1 <= len(template) <= 20000:
        raise ValueError('模板必须为1到20000字符')
    for match in TOKEN.finditer(template):
        validate_path(match.group(1))
    if '{{' in TOKEN.sub('', template) or '}}' in TOKEN.sub('', template):
        raise ValueError('模板占位符格式应为 {{字段路径}}')


def render_template(template, value):
    validate_template(template)
    pieces, size, position = [], 0, 0
    def append(text):
        nonlocal size
        size += len(text.encode('utf-8'))
        if size > 1024 * 1024:
            raise ValueError('模板输出超过1MiB限制')
        pieces.append(text)
    for match in TOKEN.finditer(template):
        append(template[position:match.start()])
        item = lookup(value, match.group(1))
        append(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, allow_nan=False))
        position = match.end()
    append(template[position:])
    return ''.join(pieces)


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def condition(value, params):
    missing = object()
    actual = lookup(value, params['path'], missing)
    op = params['op']
    if op == 'exists':
        return actual is not missing
    if actual is missing:
        raise ValueError('条件路径不存在: ' + params['path'])
    if op == 'truthy':
        return bool(actual)
    expected = params['value']
    if op in ('eq', 'ne'):
        equal = type(actual) is type(expected) and actual == expected
        return equal if op == 'eq' else not equal
    if op == 'contains':
        if not isinstance(actual, str) or not isinstance(expected, str):
            raise ValueError('contains条件需要两个字符串')
        return expected in actual
    if not finite_number(actual) or not finite_number(expected):
        raise ValueError('大小条件需要有限数值')
    return {'gt': actual > expected, 'ge': actual >= expected, 'lt': actual < expected, 'le': actual <= expected}[op]
