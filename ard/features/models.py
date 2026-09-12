"""Immutable model packages, experiment baselines and measured evaluations."""
from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import math
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
import numpy as np
from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

from ard import connectors
from ard.engines.models import predict_model
from ard.features.common import Input, Svc, User, install_once
from ard.store import encode

router = APIRouter()
_NAME = Annotated[str, StringConstraints(min_length=1, max_length=150, pattern=r'.*\S.*')]
_PACKAGE_NAME = Annotated[str, StringConstraints(min_length=1, max_length=400, pattern=r'.*\S.*')]
_COLUMN = Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r'.*\S.*')]
_NUMBER = Annotated[float, Field(strict=True)]
_MAX_PACKAGE = 8 * 1024 * 1024


class StrictInput(Input):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)


class Imputer(StrictInput):
    strategy: Literal['median']
    statistics: list[_NUMBER] = Field(min_length=1, max_length=1024)


class Scaler(StrictInput):
    mean: list[_NUMBER] = Field(min_length=1, max_length=1024)
    scale: list[_NUMBER] = Field(min_length=1, max_length=1024)


class Preprocessing(StrictInput):
    imputer: Imputer
    scaler: Scaler


def _label(value):
    if type(value) not in (str, int, float, bool):
        raise ValueError('分类标签必须为非空JSON标量')
    if isinstance(value, str) and len(value) > 2000:
        raise ValueError('分类标签超过2000字符')
    try:
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError('分类标签必须为有限数值')
    except OverflowError as exc:
        raise ValueError('分类标签数值过大') from exc
    return value


class Estimator(StrictInput):
    type: Literal['logistic_regression', 'linear_regression']
    coefficients: list[list[_NUMBER]] = Field(min_length=1, max_length=256)
    intercepts: list[_NUMBER] = Field(min_length=1, max_length=256)
    classes: list[Any] | None = Field(default=None, min_length=2, max_length=256)

    @field_validator('coefficients')
    @classmethod
    def bounded_coefficients(cls, value):
        if any(not 1 <= len(row) <= 1024 for row in value):
            raise ValueError('系数矩阵维度超过限制')
        return value

    @field_validator('classes')
    @classmethod
    def valid_classes(cls, value):
        if value is not None:
            for item in value:
                _label(item)
            if len({type(item) for item in value}) != 1 or len(set(value)) != len(value):
                raise ValueError('分类标签必须使用同一类型且不可重复')
        return value


class Artifact(StrictInput):
    format: Literal['ard.linear-model']
    version: Literal[1]
    task: Literal['classification', 'regression']
    features: list[_COLUMN] = Field(min_length=1, max_length=1024)
    preprocessing: Preprocessing
    estimator: Estimator

    @field_validator('version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('协议版本必须为整数')
        return value

    @model_validator(mode='after')
    def valid_shapes(self):
        width = len(self.features)
        if len(set(self.features)) != width:
            raise ValueError('模型特征名称不能重复')
        pre, est = self.preprocessing, self.estimator
        if any(len(values) != width for values in
               (pre.imputer.statistics, pre.scaler.mean, pre.scaler.scale, *est.coefficients)):
            raise ValueError('模型预处理或系数维度与特征不一致')
        if any(value <= 0 for value in pre.scaler.scale):
            raise ValueError('模型缩放比例必须为正数')
        if len(est.intercepts) != len(est.coefficients):
            raise ValueError('模型截距与系数数量不一致')
        if self.task == 'regression':
            if est.type != 'linear_regression' or len(est.coefficients) != 1 or est.classes is not None:
                raise ValueError('回归模型参数结构无效')
        elif (est.type != 'logistic_regression' or est.classes is None
              or len(est.coefficients) not in ({1, 2} if len(est.classes) == 2 else {len(est.classes)})):
            raise ValueError('分类模型参数结构无效')
        return self


class ModelPackage(StrictInput):
    format: Literal['ard.model-package']
    version: Literal[1]
    name: _PACKAGE_NAME
    target: _COLUMN
    artifact: Artifact

    @field_validator('version', mode='before')
    @classmethod
    def integer_version(cls, value):
        return Artifact.integer_version(value)

    @model_validator(mode='after')
    def no_target_leakage(self):
        if self.target in self.artifact.features:
            raise ValueError('目标列不能同时作为模型特征')
        if len(encode(self.model_dump(exclude_none=True))) > _MAX_PACKAGE:
            raise ValueError('模型包超过8MiB限制')
        return self


class ImportInput(Input):
    package: ModelPackage
    parent_model_id: str | None = Field(default=None, max_length=100)


class VersionInput(Input):
    name: _NAME


class TrainingConfig(StrictInput):
    target: _COLUMN
    task: Literal['classification', 'regression'] = 'classification'
    features: list[_COLUMN] | None = Field(default=None, min_length=1, max_length=1024)
    test_fraction: _NUMBER = Field(default=.25, ge=.1, le=.5)
    seed: int = Field(default=42, ge=0, le=4294967295)

    @model_validator(mode='after')
    def no_target_leakage(self):
        if self.features and (self.target in self.features or len(set(self.features)) != len(self.features)):
            raise ValueError('目标列不能同时作为特征，特征名称不能重复')
        return self


class BaselineInput(Input):
    name: _NAME
    dataset_id: str = Field(min_length=1, max_length=100)
    config: TrainingConfig
    parent_id: str | None = Field(default=None, max_length=100)


class EvaluationInput(Input):
    dataset_id: str = Field(min_length=1, max_length=100)
    name: _NAME | None = None


class ComparisonInput(EvaluationInput):
    model_ids: list[str] = Field(min_length=2, max_length=10)


class ChatInput(Input):
    prompt: str = Field(min_length=1, max_length=200000)


class EmbeddingInput(Input):
    texts: list[str] = Field(min_length=1, max_length=32)


def _same_project(record, pid):
    if record['project_id'] != pid:
        raise ValueError('不能跨项目引用模型或数据版本')
    if record.get('archived'):
        raise ValueError('归档版本不能用于新实验')
    return record


def _dataset(s, dataset_id, user, pid):
    from ard.features.data import require_active
    return require_active(s, _same_project(s.record(dataset_id, 'dataset', user), pid))


def _artifact(s, model):
    data = json.loads(s.store.read_blob(model['sha256']))
    return Artifact.model_validate(data).model_dump(exclude_none=True)


def _configuration(s, user, model):
    """Find the recorded split, never guess missing seeds from default values."""
    if model.get('training_config'):
        return TrainingConfig.model_validate(model['training_config']).model_dump()
    for job in s.list('job', model['project_id'], user):
        if (job.get('result') or {}).get('model_id') == model['id'] and job.get('payload', {}).get('kind') == 'train':
            payload = job['payload']
            return TrainingConfig.model_validate({key: payload[key] for key in TrainingConfig.model_fields if key in payload}).model_dump()
    return None


def _feature_key(row, artifact):
    # Numeric strings and omitted/blank features have the same prediction meaning.
    values = []
    for index, feature in enumerate(artifact['features']):
        value = row.get(feature)
        if value is None or isinstance(value, str) and not value.strip():
            values.append(_numeric(artifact['preprocessing']['imputer']['statistics'][index]))
        else:
            values.append(_numeric(value))
    return encode(values)


def _numeric(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('评估数据必须包含有限数值')
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('评估数据必须包含有限数值') from exc
    if not math.isfinite(value):
        raise ValueError('评估数据必须包含有限数值')
    return 0.0 if value == 0 else value


def _plan(s, user, model, dataset, rows=None):
    artifact = _artifact(s, model)
    if model['target'] in artifact['features']:
        raise ValueError('目标列不能同时作为模型特征')
    rows = s.rows(dataset['id']) if rows is None else rows
    if not rows or len(rows) > 50000:
        raise ValueError('评估数据需要1至50000条样本')
    if len(rows) * len(artifact['features']) > 5_000_000:
        raise ValueError('评估样本与特征乘积超过500万限制')
    if any(model['target'] not in row for row in rows):
        raise ValueError('评估数据缺少模型目标列')
    if any(feature not in dataset['columns'] for feature in artifact['features']):
        raise ValueError('评估数据缺少模型特征列')
    indices = list(range(len(rows)))
    scope, independence = 'selected_dataset', 'unknown_training_provenance'
    notes = ['导入模型未提供可验证的训练来源；本次结果不证明测试数据独立。']
    excluded = 0
    if model.get('dataset_id'):
        source = _same_project(s.record(model['dataset_id'], 'dataset', user), model['project_id'])
        training_rows = s.rows(source['id'])
        config = _configuration(s, user, model)
        if config:
            train_indices, holdout_indices = train_test_split(
                list(range(len(training_rows))), test_size=config['test_fraction'], random_state=config['seed'],
                stratify=[row[config['target']] for row in training_rows] if config['task'] == 'classification' else None)
            if dataset['sha256'] == source['sha256']:
                indices = sorted(holdout_indices)
                scope, independence = 'original_holdout', 'recorded_split'
            else:
                independence = 'training_feature_overlap_removed'
            seen_rows = [training_rows[index] for index in train_indices]
        else:
            if dataset['sha256'] == source['sha256']:
                raise ValueError('原模型未保存可验证的划分配置，请选择其他评估数据版本')
            seen_rows = training_rows
            independence = 'source_feature_overlap_removed'
        seen = {_feature_key(row, artifact) for row in seen_rows}
        filtered = [index for index in indices if _feature_key(rows[index], artifact) not in seen]
        excluded = len(indices) - len(filtered)
        indices = filtered
        notes = ['评估使用封存的预处理和模型参数；已排除与已知训练样本特征相同的行。',
                 '此检查不能证明来源之间完全独立，也不能排除特征设计中的间接目标泄漏。']
    return {'artifact': artifact, 'rows': rows, 'indices': indices, 'scope': scope,
            'independence': independence, 'notes': notes, 'overlap_rows_excluded': excluded}


def _score(model, plan, indices=None):
    indices = plan['indices'] if indices is None else indices
    minimum = 2 if model['task'] == 'regression' else 1
    if len(indices) < minimum:
        raise ValueError(f'排除训练重叠后，评估至少需要{minimum}条有效样本')
    rows = [plan['rows'][index] for index in indices]
    features = plan['artifact']['features']
    predictions = predict_model(plan['artifact'], [{feature: row.get(feature) for feature in features} for row in rows])
    if model['task'] == 'classification':
        labels = list(plan['artifact']['estimator']['classes'])
        actual = [_label(row[model['target']]) for row in rows]
        if any(type(value) is not type(labels[0]) for value in actual):
            raise ValueError('评估目标标签的JSON类型与模型类别不一致')
        known_labels = set(labels)
        for value in actual:
            if value not in known_labels:
                if len(labels) >= 256:
                    raise ValueError('评估类别总数超过256个限制')
                labels.append(value)
                known_labels.add(value)
        positions = {label: index for index, label in enumerate(labels)}
        matrix = [[0 for _ in labels] for _ in labels]
        for truth, prediction in zip(actual, predictions):
            matrix[positions[truth]][positions[prediction]] += 1
        precision = recall = f1 = 0.
        for i in range(len(labels)):
            support = sum(matrix[i])
            guessed = sum(row[i] for row in matrix)
            p = matrix[i][i] / guessed if guessed else 0.
            r = matrix[i][i] / support if support else 0.
            precision += support * p / len(rows)
            recall += support * r / len(rows)
            f1 += support * (2 * p * r / (p + r) if p + r else 0.) / len(rows)
        metrics = {'accuracy': sum(matrix[i][i] for i in range(len(labels))) / len(rows),
                   'precision': precision, 'recall': recall, 'f1': f1, 'confusion_matrix': matrix}
    else:
        labels = []
        actual = [_numeric(row[model['target']]) for row in rows]
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            mse = _numeric(mean_squared_error(actual, predictions))
            metrics = {'mse': mse, 'rmse': _numeric(math.sqrt(mse)),
                       'mae': _numeric(mean_absolute_error(actual, predictions)), 'r2': _numeric(r2_score(actual, predictions))}
    return metrics, {'labels': labels, 'rows': [{'source_row': index, 'actual': truth, 'prediction': prediction}
                                              for index, truth, prediction in zip(indices, actual, predictions)]}


def _evaluation_data(model, dataset, plan, metrics, raw, name, user, *, common=False):
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError('评估结果超过20MiB限制')
    return {'name': name or (model['name'] + ' · 评估')[:150], 'model_id': model['id'], 'model_name': model['name'],
            'dataset_id': dataset['id'], 'dataset_name': dataset['name'], 'dataset_sha256': dataset['sha256'],
            'model_sha256': model['sha256'], 'task': model['task'], 'target': model['target'],
            'features': model['features'], 'metrics': metrics, 'row_count': len(json.loads(raw)['rows']),
            'dataset_rows': dataset['row_count'], 'scope': 'common_dataset_rows' if common else plan['scope'],
            'independence': plan['independence'], 'overlap_rows_excluded': plan['overlap_rows_excluded'],
            'notes': plan['notes'], 'size_bytes': len(raw), 'creator': user.user, 'sealed': True}


@router.post('/api/projects/{pid}/models/import', status_code=201)
def import_model(pid: str, body: ImportInput, s: Svc, user: User):
    user.allow_write(); s.project(pid, user)
    parent = None
    if body.parent_model_id:
        parent = _same_project(s.record(body.parent_model_id, 'model', user), pid)
    package = body.package.model_dump(exclude_none=True)
    artifact = package['artifact']
    raw = encode(artifact)
    # Run the data-only predictor as an additional arithmetic validation, with no user code.
    predict_model(artifact, [{feature: None for feature in artifact['features']}])
    with s.store.transaction():
        s._quota(pid, len(raw))
        return s.store.create('model', pid, {'name': package['name'], 'target': package['target'],
            'task': artifact['task'], 'features': artifact['features'], 'metrics': {}, 'train_rows': 0, 'test_rows': 0,
            'dataset_id': None, 'parent_id': parent['id'] if parent else None,
            'version_root_id': (parent.get('version_root_id') or parent['id']) if parent else None,
            'version_number': (parent.get('version_number', 1) + 1) if parent else 1,
            'sha256': s.store.blob(raw), 'size_bytes': len(raw), 'creator': user.user,
            'engine': 'platform-json', 'artifact_format': 'json', 'sealed': True,
            'provenance': 'imported_unverified_training'}, user.user)


@router.get('/api/models/{model_id}/package')
def export_model(model_id: str, s: Svc, user: User):
    model = s.record(model_id, 'model', user)
    package = ModelPackage.model_validate({'format': 'ard.model-package', 'version': 1, 'name': model['name'],
                                         'target': model['target'], 'artifact': _artifact(s, model)})
    return Response(encode(package.model_dump(exclude_none=True)), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="model-package-{model_id}.json"',
                             'X-Content-Type-Options': 'nosniff'})


@router.post('/api/models/{model_id}/versions', status_code=201)
def new_version(model_id: str, body: VersionInput, s: Svc, user: User):
    user.allow_write(); model = s.record(model_id, 'model', user)
    raw = encode(_artifact(s, model))
    clone = {key: value for key, value in model.items() if key not in
             ('id', 'kind', 'project_id', 'revision', 'created_at', 'updated_at')}
    clone.update(name=body.name, parent_id=model_id, version_root_id=model.get('version_root_id') or model_id,
                 version_number=model.get('version_number', 1) + 1, creator=user.user, sealed=True)
    config = _configuration(s, user, model)
    if config:
        clone['training_config'] = config
    with s.store.transaction():
        s._quota(model['project_id'], len(raw))
        return s.store.create('model', model['project_id'], clone, user.user)


@router.get('/api/models/{model_id}/versions')
def versions(model_id: str, s: Svc, user: User):
    model = s.record(model_id, 'model', user)
    root = model.get('version_root_id') or model_id
    return [item for item in s.list('model', model['project_id'], user)
            if item['id'] == root or (item.get('version_root_id') or item['id']) == root]


@router.post('/api/projects/{pid}/model-baselines', status_code=201)
def create_baseline(pid: str, body: BaselineInput, s: Svc, user: User):
    user.allow_write(); s.project(pid, user)
    dataset = _dataset(s, body.dataset_id, user, pid)
    parent = _same_project(s.record(body.parent_id, 'model_baseline', user), pid) if body.parent_id else None
    config = body.config.model_dump()
    if config['target'] not in dataset['columns'] or dataset['row_count'] < 12:
        raise ValueError('基线数据需要目标列和至少12条样本')
    if not config['features']:
        config['features'] = [name for name in dataset['columns'] if name != config['target']]
    config = TrainingConfig.model_validate(config).model_dump()
    if not config['features'] or any(feature not in dataset['columns'] for feature in config['features']):
        raise ValueError('基线特征列不存在或为空')
    data = {'name': body.name, 'dataset_id': dataset['id'], 'dataset_sha256': dataset['sha256'], 'config': config,
            'parent_id': parent['id'] if parent else None, 'creator': user.user, 'sealed': True,
            'version_root_id': (parent.get('version_root_id') or parent['id']) if parent else None,
            'version_number': parent.get('version_number', 1) + 1 if parent else 1}
    data['size_bytes'] = len(encode(data))
    with s.store.transaction():
        s._quota(pid, data['size_bytes'])
        return s.store.create('model_baseline', pid, data, user.user)


@router.get('/api/projects/{pid}/model-baselines')
def baselines(pid: str, s: Svc, user: User):
    return s.list('model_baseline', pid, user)


@router.get('/api/model-baselines/{baseline_id}')
def baseline(baseline_id: str, s: Svc, user: User):
    return s.record(baseline_id, 'model_baseline', user)


@router.post('/api/model-baselines/{baseline_id}/run', status_code=202)
def run_baseline(baseline_id: str, s: Svc, user: User):
    user.allow_write(); baseline = s.record(baseline_id, 'model_baseline', user)
    dataset = _dataset(s, baseline['dataset_id'], user, baseline['project_id'])
    if dataset['sha256'] != baseline['dataset_sha256']:
        raise ValueError('基线数据摘要不一致')
    return s.train(dataset['id'], {**baseline['config'], 'baseline_id': baseline_id}, user)


@router.get('/api/model-baselines/{baseline_id}/runs')
def baseline_runs(baseline_id: str, s: Svc, user: User):
    baseline = s.record(baseline_id, 'model_baseline', user)
    return [job for job in s.list('job', baseline['project_id'], user) if job['payload'].get('baseline_id') == baseline_id]


@router.post('/api/models/{model_id}/evaluate', status_code=201)
def evaluate(model_id: str, body: EvaluationInput, s: Svc, user: User):
    user.allow_write(); model = s.record(model_id, 'model', user)
    dataset = _dataset(s, body.dataset_id, user, model['project_id'])
    plan = _plan(s, user, model, dataset)
    metrics, results = _score(model, plan)
    raw = encode(results)
    data = _evaluation_data(model, dataset, plan, metrics, raw, body.name, user)
    with s.store.transaction():
        s._quota(model['project_id'], len(raw))
        data['sha256'] = s.store.blob(raw)
        return s.store.create('model_evaluation', model['project_id'], data, user.user)


@router.get('/api/projects/{pid}/model-evaluations')
def evaluations(pid: str, s: Svc, user: User):
    return s.list('model_evaluation', pid, user)


@router.get('/api/model-evaluations/{evaluation_id}')
def evaluation(evaluation_id: str, s: Svc, user: User):
    return s.record(evaluation_id, 'model_evaluation', user)


@router.get('/api/model-evaluations/{evaluation_id}/rows')
def evaluation_rows(evaluation_id: str, s: Svc, user: User,
                    limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
    record = s.record(evaluation_id, 'model_evaluation', user)
    results = json.loads(s.store.read_blob(record['sha256']))
    return {'labels': results['labels'], 'rows': results['rows'][offset:offset + limit], 'total': record['row_count']}


@router.post('/api/projects/{pid}/model-comparisons', status_code=201)
def compare(pid: str, body: ComparisonInput, s: Svc, user: User):
    user.allow_write(); s.project(pid, user)
    if len(set(body.model_ids)) != len(body.model_ids):
        raise ValueError('对比模型不能重复')
    dataset = _dataset(s, body.dataset_id, user, pid)
    models = [_same_project(s.record(mid, 'model', user), pid) for mid in body.model_ids]
    if len({(model['task'], model['target']) for model in models}) != 1:
        raise ValueError('对比模型必须使用相同任务和目标列')
    rows = s.rows(dataset['id'])
    plans = [_plan(s, user, model, dataset, rows=rows) for model in models]
    indices = sorted(set.intersection(*(set(plan['indices']) for plan in plans)))
    scored = [_score(model, plan, indices) for model, plan in zip(models, plans)]
    raws = [encode(results) for _, results in scored]
    with s.store.transaction():
        s._quota(pid, sum(len(raw) for raw in raws))
        entries = []
        for model, plan, (metrics, _), raw in zip(models, plans, scored, raws):
            data = _evaluation_data(model, dataset, plan, metrics, raw, body.name, user, common=True)
            data['sha256'] = s.store.blob(raw)
            record = s.store.create('model_evaluation', pid, data, user.user)
            entries.append({'model_id': model['id'], 'model_name': model['name'], 'evaluation_id': record['id'], 'metrics': metrics})
        comparison = {'name': body.name or '模型对比', 'dataset_id': dataset['id'],
            'dataset_sha256': dataset['sha256'], 'task': models[0]['task'], 'target': models[0]['target'],
            'row_count': len(indices), 'entries': entries, 'creator': user.user, 'sealed': True,
            'notes': ['所有模型在同一组行上评估；可验证的训练特征重叠已排除。',
                      '导入模型的训练来源未知，结果不能证明测试数据独立。']}
        comparison['size_bytes'] = len(encode(comparison))
        s._quota(pid, comparison['size_bytes'])
        return s.store.create('model_comparison', pid, comparison, user.user)


@router.get('/api/projects/{pid}/model-comparisons')
def comparisons(pid: str, s: Svc, user: User):
    return s.list('model_comparison', pid, user)


@router.get('/api/model-evaluations/{evaluation_id}/report')
def report(evaluation_id: str, s: Svc, user: User):
    record = s.record(evaluation_id, 'model_evaluation', user)
    results = json.loads(s.store.read_blob(record['sha256']))
    esc = lambda value: html.escape(str(value), quote=True)
    facts = [('模型', record['model_name']), ('模型版本', record['model_id']), ('模型摘要', record['model_sha256']),
             ('评估数据', record['dataset_name']), ('数据摘要', record['dataset_sha256']), ('任务', record['task']),
             ('目标列', record['target']), ('特征', '、'.join(record['features'])), ('实际评估行数', record['row_count']),
             ('评估范围', record['scope']), ('独立性检查', record['independence']), ('生成时间', record['created_at'])]
    facts_markup = ''.join(f'<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>' for key, value in facts)
    metric_markup = ''.join(f'<tr><th>{esc(key)}</th><td>{esc(json.dumps(value, ensure_ascii=False))}</td></tr>'
                            for key, value in record['metrics'].items())
    rows_markup = ''.join(f'<tr><td>{row["source_row"] + 1}</td><td>{esc(row["actual"])}</td><td>{esc(row["prediction"])}</td></tr>'
                          for row in results['rows'][:1000])
    notes_markup = ''.join(f'<li>{esc(note)}</li>' for note in record['notes'])
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{esc(record['name'])}</title><style>body{{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px;color:#152638}}table{{border-collapse:collapse;width:100%;margin:18px 0}}th,td{{border:1px solid #b7c8d5;padding:8px;text-align:left;overflow-wrap:anywhere}}th{{background:#eef4f7}}li{{margin:8px 0}}</style>
<h1>{esc(record['name'])}</h1><p>使用封存模型实际预测后计算的评估报告。</p><table>{facts_markup}</table><h2>评估指标</h2><table>{metric_markup}</table>
<p>混淆矩阵类别顺序：{esc(json.dumps(results['labels'], ensure_ascii=False))}</p><h2>数据范围说明</h2><ul>{notes_markup}</ul>
<h2>逐行结果</h2><p>展示前 {min(1000, record['row_count'])} 行，共 {record['row_count']} 行；完整结果保存在平台评估记录中。</p>
<table><thead><tr><th>源行号（从1开始）</th><th>真实目标</th><th>模型预测</th></tr></thead><tbody>{rows_markup}</tbody></table></html>'''
    return Response(document, media_type='text/html', headers={
        'Content-Disposition': f'attachment; filename="evaluation-{evaluation_id}.html"',
        'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        'X-Content-Type-Options': 'nosniff'})


@router.get('/api/projects/{pid}/deployment-statistics')
def deployment_statistics(pid: str, s: Svc, user: User):
    deployments = s.list('deployment', pid, user)
    current_time = datetime.now(timezone.utc)
    entries = []
    for deployment in deployments:
        status = deployment['status']
        if status == 'ACTIVE' and datetime.fromisoformat(deployment['expires_at']) <= current_time:
            status = 'EXPIRED'
        requests, failures = deployment['requests'], deployment['failures']
        entries.append({'deployment_id': deployment['id'], 'model_id': deployment['model_id'], 'name': deployment['name'],
                        'status': status, 'expires_at': deployment['expires_at'], 'requests': requests,
                        'failures': failures, 'successes': requests - failures,
                        'failure_rate': failures / requests if requests else None,
                        'total_latency_ms': deployment['total_latency_ms'],
                        'mean_latency_ms': deployment['total_latency_ms'] / requests if requests else None})
    total = sum(item['requests'] for item in entries)
    failed = sum(item['failures'] for item in entries)
    return {'source': 'recorded_prediction_requests', 'entries': entries, 'requests': total, 'failures': failed,
            'active': sum(item['status'] == 'ACTIVE' for item in entries),
            'failure_rate': failed / total if total else None,
            'mean_latency_ms': sum(item['total_latency_ms'] for item in entries) / total if total else None,
            'notes': ['计数包含进入实际预测函数的成功和失败请求；鉴权拒绝、请求格式错误和过期服务不计入。']}


@router.get('/api/model-services/configuration')
def model_configuration(user: User):
    return connectors.configuration()


@router.post('/api/model-services/probe')
def probe_models(user: User):
    user.allow_write()
    return connectors.model_probe()


@router.post('/api/model-services/chat')
def use_chat(body: ChatInput, user: User):
    user.allow_write()
    return connectors.chat(body.prompt)


@router.post('/api/model-services/embeddings')
def use_embeddings(body: EmbeddingInput, user: User):
    user.allow_write()
    identity = connectors.embedding_identity()
    vectors = connectors.embeddings(body.texts)
    if identity != connectors.embedding_identity():
        raise ValueError('模型服务配置在请求期间发生变化，请重试')
    return {'identity': identity, 'count': len(vectors), 'dimensions': vectors.shape[1], 'embeddings': vectors.tolist()}


def install(app):
    install_once(app, router, 'models')
