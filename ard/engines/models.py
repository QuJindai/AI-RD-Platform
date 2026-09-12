"""CPU-only numeric linear model training with inspectable JSON artifacts.

``train_model`` splits rows before fitting preprocessing and returns five
top-level fields: ``artifact``, ``metrics``, integer ``train_rows`` and
``test_rows`` counts, and ``features``.  Feature names refer to dictionary keys
in prediction rows, in the exact stored order.  ``None``, blank strings, and
omitted keys are missing numeric features; finite numeric strings are accepted.

Artifact protocol ``ard.linear-model`` version 1 is JSON data, never pickle::

    {
      "format": "ard.linear-model", "version": 1,
      "task": "classification" | "regression", "features": [name, ...],
      "preprocessing": {
        "imputer": {"strategy": "median", "statistics": [number, ...]},
        "scaler": {"mean": [number, ...], "scale": [positive_number, ...]}
      },
      "estimator": {
        "type": "logistic_regression" | "linear_regression",
        "coefficients": [[number, ...], ...], "intercepts": [number, ...],
        "classes": [JSON_scalar, ...]  # classification only
      }
    }

Imputation medians and scaling statistics are fit only on the training
partition.  Prediction reconstructs the linear decision rule solely from this
protocol, so artifacts survive a strict ``json.dumps(..., allow_nan=False)``
round trip and do not execute serialized code.
"""

from __future__ import annotations

from collections import Counter
import json
import math
from typing import Any

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import train_test_split


_FORMAT = "ard.linear-model"
_VERSION = 1


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _numeric(value: Any, *, missing_ok: bool) -> float:
    if _missing(value):
        if missing_ok:
            return float("nan")
        raise ValueError("target values may not be missing")
    if isinstance(value, bool):
        raise ValueError("boolean values are not numeric features")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("numeric features and regression targets must be finite numbers") from exc
    if not math.isfinite(number):
        raise ValueError("numeric features and regression targets must be finite numbers")
    return number


def _json_label(value: Any) -> str | int | float | bool:
    if value is None or isinstance(value, (dict, list, tuple)):
        raise ValueError("classification targets must be non-null JSON scalars")
    if isinstance(value, np.generic):
        value = value.item()
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("classification targets must be JSON scalars")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("classification targets must be finite")
    return value


def _validate_label_types(labels: list[str | int | float | bool]) -> None:
    if len({type(label) for label in labels}) > 1:
        raise ValueError("classification targets must all use the same JSON scalar type")


def _feature_names(rows: list[dict], target: str, requested: list[str] | None) -> list[str]:
    if requested is None:
        names: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for name in row:
                if name != target and name not in seen:
                    seen.add(name)
                    names.append(name)
    else:
        if not isinstance(requested, list):
            raise ValueError("features must be a list of column names")
        names = list(requested)
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("at least one named feature is required")
    if len(set(names)) != len(names):
        raise ValueError("features must not contain duplicates")
    if target in names:
        raise ValueError("target column may not also be a feature")
    available = {name for row in rows for name in row}
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown feature: {unknown[0]}")
    return names


def _matrix(rows: list[dict], features: list[str]) -> np.ndarray:
    values = np.asarray(
        [[_numeric(row.get(feature), missing_ok=True) for feature in features] for row in rows],
        dtype=float,
    )
    return values.reshape((len(rows), len(features)))


def _fit_preprocessing(train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    statistics = []
    for column in range(train.shape[1]):
        values = train[:, column]
        present = values[~np.isnan(values)]
        if not len(present):
            raise ValueError("every feature needs a non-missing value in the training partition")
        with np.errstate(over="ignore", invalid="ignore"):
            statistics.append(float(np.median(present)))
    medians = np.asarray(statistics, dtype=float)
    imputed = np.where(np.isnan(train), medians, train)
    with np.errstate(over="ignore", invalid="ignore"):
        means = np.mean(imputed, axis=0)
        scales = np.std(imputed, axis=0)
    _require_finite_array(medians, "imputation statistics")
    _require_finite_array(means, "scaling means")
    _require_finite_array(scales, "scaling scales")
    scales[scales == 0] = 1.0
    return medians, means, scales


def _apply_preprocessing(matrix: np.ndarray, medians: np.ndarray, means: np.ndarray, scales: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        prepared = (np.where(np.isnan(matrix), medians, matrix) - means) / scales
    _require_finite_array(prepared, "preprocessed features")
    return prepared


def _require_finite_array(values: np.ndarray, field: str) -> None:
    if not bool(np.all(np.isfinite(values))):
        raise ValueError(f"{field} produced a non-finite numeric result")


def _derived_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} produced a non-finite numeric result")
    return result


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"artifact {field} must contain finite numbers")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"artifact {field} must contain finite numbers") from exc
    if not math.isfinite(result):
        raise ValueError(f"artifact {field} must contain finite numbers")
    return result


def train_model(
    rows: list[dict],
    target: str,
    task: str = "classification",
    features: list[str] | None = None,
    test_fraction: float = 0.25,
    seed: int = 42,
) -> dict:
    """Train and evaluate a real CPU linear classifier or regressor."""
    if not isinstance(rows, list) or len(rows) < 12 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("training requires at least 12 object rows")
    if not isinstance(target, str) or not target or any(target not in row for row in rows):
        raise ValueError("target must name a column present in every row")
    if task not in {"classification", "regression"}:
        raise ValueError("task must be classification or regression")
    if isinstance(test_fraction, bool) or not isinstance(test_fraction, (int, float)):
        raise ValueError("test_fraction must be a number between zero and one")
    if not math.isfinite(float(test_fraction)) or not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be a number between zero and one")

    feature_names = _feature_names(rows, target, features)
    matrix = _matrix(rows, feature_names)
    indexes = list(range(len(rows)))
    if task == "classification":
        labels = [_json_label(row[target]) for row in rows]
        _validate_label_types(labels)
        try:
            counts = Counter(labels)
        except TypeError as exc:
            raise ValueError("classification targets must be hashable JSON scalars") from exc
        if len(counts) < 2 or min(counts.values()) < 2:
            raise ValueError("classification requires at least two classes with two rows each")
        test_count = math.ceil(len(rows) * test_fraction)
        train_count = len(rows) - test_count
        if test_count < len(counts) or train_count < len(counts):
            raise ValueError("test_fraction cannot represent every class in both partitions")
        try:
            train_indexes, test_indexes = train_test_split(
                indexes, test_size=test_fraction, random_state=seed, stratify=labels
            )
        except ValueError as exc:
            raise ValueError("unable to create a stratified train/test split") from exc
        train_targets = np.asarray([labels[index] for index in train_indexes])
        test_targets = np.asarray([labels[index] for index in test_indexes])
    else:
        targets = [_numeric(row[target], missing_ok=False) for row in rows]
        if math.ceil(len(rows) * test_fraction) < 2:
            raise ValueError("regression evaluation requires at least two test rows")
        train_indexes, test_indexes = train_test_split(
            indexes, test_size=test_fraction, random_state=seed
        )
        train_targets = np.asarray([targets[index] for index in train_indexes], dtype=float)
        test_targets = np.asarray([targets[index] for index in test_indexes], dtype=float)

    train_matrix = matrix[train_indexes]
    test_matrix = matrix[test_indexes]
    medians, means, scales = _fit_preprocessing(train_matrix)
    prepared_train = _apply_preprocessing(train_matrix, medians, means, scales)
    prepared_test = _apply_preprocessing(test_matrix, medians, means, scales)

    if task == "classification":
        estimator = LogisticRegression(max_iter=1000, random_state=seed)
        try:
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                estimator.fit(prepared_train, train_targets)
        except (FloatingPointError, ValueError) as exc:
            raise ValueError("classification samples are not trainable") from exc
        coefficients = np.asarray(estimator.coef_, dtype=float)
        intercepts = np.asarray(estimator.intercept_, dtype=float)
        _require_finite_array(coefficients, "estimator coefficients")
        _require_finite_array(intercepts, "estimator intercepts")
        with np.errstate(over="ignore", invalid="ignore"):
            evaluation_scores = prepared_test @ coefficients.T + intercepts
        _require_finite_array(evaluation_scores, "evaluation scores")
        predictions = estimator.predict(prepared_test)
        classes = [_json_label(value) for value in estimator.classes_]
        metrics = {
            "accuracy": float(accuracy_score(test_targets, predictions)),
            "precision": float(precision_score(test_targets, predictions, average="weighted", zero_division=0)),
            "recall": float(recall_score(test_targets, predictions, average="weighted", zero_division=0)),
            "f1": float(f1_score(test_targets, predictions, average="weighted", zero_division=0)),
            "confusion_matrix": confusion_matrix(test_targets, predictions, labels=estimator.classes_).astype(int).tolist(),
        }
        estimator_data = {
            "type": "logistic_regression",
            "coefficients": coefficients.tolist(),
            "intercepts": intercepts.tolist(),
            "classes": classes,
        }
    else:
        estimator = LinearRegression()
        try:
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                estimator.fit(prepared_train, train_targets)
        except (FloatingPointError, ValueError) as exc:
            raise ValueError("regression samples are not trainable") from exc
        coefficients = np.asarray(estimator.coef_, dtype=float).reshape(1, -1)
        intercepts = np.asarray([estimator.intercept_], dtype=float)
        _require_finite_array(coefficients, "estimator coefficients")
        _require_finite_array(intercepts, "estimator intercepts")
        with np.errstate(over="ignore", invalid="ignore"):
            evaluation_scores = prepared_test @ coefficients.T + intercepts
        _require_finite_array(evaluation_scores, "evaluation scores")
        predictions = evaluation_scores[:, 0]
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            mse = _derived_float(mean_squared_error(test_targets, predictions), "mse")
            mae = _derived_float(mean_absolute_error(test_targets, predictions), "mae")
            r2 = _derived_float(r2_score(test_targets, predictions), "r2")
        metrics = {
            "mse": mse,
            "rmse": _derived_float(math.sqrt(mse), "rmse"),
            "mae": mae,
            "r2": r2,
        }
        estimator_data = {
            "type": "linear_regression",
            "coefficients": coefficients.tolist(),
            "intercepts": intercepts.tolist(),
        }

    artifact = {
        "format": _FORMAT,
        "version": _VERSION,
        "task": task,
        "features": feature_names,
        "preprocessing": {
            "imputer": {"strategy": "median", "statistics": medians.tolist()},
            "scaler": {"mean": means.tolist(), "scale": scales.tolist()},
        },
        "estimator": estimator_data,
    }
    result = {
        "artifact": artifact,
        "metrics": metrics,
        "train_rows": len(train_indexes),
        "test_rows": len(test_indexes),
        "features": feature_names,
    }
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("training result is not strict JSON data") from exc
    return result


def predict_model(artifact: dict, rows: list[dict]) -> list:
    """Predict using a validated version-1 JSON artifact without deserialization."""
    if not isinstance(artifact, dict) or artifact.get("format") != _FORMAT or artifact.get("version") != _VERSION:
        raise ValueError("unsupported model artifact")
    task = artifact.get("task")
    features = artifact.get("features")
    if task not in {"classification", "regression"} or not isinstance(features, list) or not features:
        raise ValueError("malformed model artifact")
    if any(not isinstance(name, str) or not name for name in features) or len(set(features)) != len(features):
        raise ValueError("malformed artifact features")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("prediction rows must be a list of objects")
    if any(not any(feature in row for feature in features) for row in rows):
        raise ValueError("each prediction row must contain at least one artifact feature")

    try:
        preprocessing = artifact["preprocessing"]
        estimator = artifact["estimator"]
        statistics = preprocessing["imputer"]["statistics"]
        means_data = preprocessing["scaler"]["mean"]
        scales_data = preprocessing["scaler"]["scale"]
        coefficients_data = estimator["coefficients"]
        intercepts_data = estimator["intercepts"]
    except (KeyError, TypeError) as exc:
        raise ValueError("malformed model artifact") from exc

    width = len(features)
    if not all(isinstance(values, list) and len(values) == width for values in [statistics, means_data, scales_data]):
        raise ValueError("artifact preprocessing dimensions do not match features")
    medians = np.asarray([_finite_float(value, "statistics") for value in statistics])
    means = np.asarray([_finite_float(value, "mean") for value in means_data])
    scales = np.asarray([_finite_float(value, "scale") for value in scales_data])
    if np.any(scales <= 0):
        raise ValueError("artifact scales must be positive")
    matrix = _matrix(rows, features)
    prepared = _apply_preprocessing(matrix, medians, means, scales)

    if not isinstance(coefficients_data, list) or not coefficients_data or not isinstance(intercepts_data, list):
        raise ValueError("malformed estimator parameters")
    if any(not isinstance(row, list) or len(row) != width for row in coefficients_data):
        raise ValueError("artifact coefficient dimensions do not match features")
    coefficients = np.asarray(
        [[_finite_float(value, "coefficients") for value in row] for row in coefficients_data]
    )
    intercepts = np.asarray([_finite_float(value, "intercepts") for value in intercepts_data])
    if len(intercepts) != coefficients.shape[0]:
        raise ValueError("artifact coefficient and intercept dimensions differ")
    with np.errstate(over="ignore", invalid="ignore"):
        scores = prepared @ coefficients.T + intercepts
    _require_finite_array(scores, "prediction scores")

    if task == "regression":
        if estimator.get("type") != "linear_regression" or scores.shape[1] != 1:
            raise ValueError("malformed regression estimator")
        return [_derived_float(value, "prediction") for value in scores[:, 0]]

    classes = estimator.get("classes")
    if estimator.get("type") != "logistic_regression" or not isinstance(classes, list) or len(classes) < 2:
        raise ValueError("malformed classification estimator")
    classes = [_json_label(value) for value in classes]
    _validate_label_types(classes)
    if len(classes) == 2 and scores.shape[1] == 1:
        indexes = (scores[:, 0] > 0).astype(int)
    elif scores.shape[1] == len(classes):
        indexes = np.argmax(scores, axis=1)
    else:
        raise ValueError("artifact class and coefficient dimensions differ")
    return [classes[int(index)] for index in indexes]
