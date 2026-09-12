import json
import math

import pytest
from sklearn.model_selection import train_test_split

from ard.engines.models import predict_model, train_model


def _classification_rows():
    return [
        {"x": float(index), "spare": None if index % 4 == 0 else index / 2, "label": "high" if index >= 12 else "low"}
        for index in range(24)
    ]


def _regression_rows():
    return [
        {"x": float(index), "offset": None if index % 5 == 0 else float(index % 3), "y": 3.0 * index + 2.0}
        for index in range(24)
    ]


def test_classification_trains_on_one_partition_and_scores_the_holdout():
    rows = _classification_rows()
    result = train_model(rows, "label", features=["x", "spare"], seed=9)
    assert result["train_rows"] == 18
    assert result["test_rows"] == 6
    assert result["features"] == ["x", "spare"]
    assert set(result["metrics"]) == {"accuracy", "precision", "recall", "f1", "confusion_matrix"}

    _, test_indexes = train_test_split(
        list(range(24)), test_size=0.25, random_state=9,
        stratify=[row["label"] for row in rows],
    )
    heldout = [rows[index] for index in test_indexes]
    predictions = predict_model(result["artifact"], heldout)
    expected_accuracy = sum(prediction == row["label"] for prediction, row in zip(predictions, heldout)) / 6
    assert result["metrics"]["accuracy"] == pytest.approx(expected_accuracy)


def test_regression_artifact_survives_strict_json_round_trip_and_predicts():
    result = train_model(_regression_rows(), "y", task="regression", seed=3)
    encoded = json.dumps(result, allow_nan=False)
    restored = json.loads(encoded)
    predictions = predict_model(restored["artifact"], [{"x": 30, "offset": None}, {"x": 31, "offset": 1}])
    assert len(predictions) == 2
    assert all(isinstance(value, float) and math.isfinite(value) for value in predictions)
    assert predictions[0] == pytest.approx(92, abs=2)
    assert set(result["metrics"]) == {"mse", "rmse", "mae", "r2"}


def test_regression_rejects_nonfinite_metrics_derived_from_finite_inputs():
    rows = [
        {"x": float(index), "y": 1e154 if index % 2 else -1e154}
        for index in range(20)
    ]
    with pytest.raises(ValueError):
        train_model(rows, "y", task="regression", seed=1)


def test_prediction_rejects_nonfinite_scores_derived_from_finite_inputs():
    rows = [{"x": float(index), "y": 2.0 * index} for index in range(20)]
    artifact = train_model(rows, "y", task="regression")["artifact"]
    with pytest.raises(ValueError):
        predict_model(artifact, [{"x": 1.79e308}])


def test_preprocessing_is_fitted_only_on_training_rows():
    rows = [{"x": float(index), "m": None, "y": float(index)} for index in range(12)]
    _, test_indexes = train_test_split(list(range(12)), test_size=0.25, random_state=17)
    for index in test_indexes:
        rows[index]["m"] = 10_000.0 + index
    for index in set(range(12)) - set(test_indexes):
        rows[index]["m"] = float(index)

    result = train_model(rows, "y", task="regression", features=["x", "m"], seed=17)
    training_m = [rows[index]["m"] for index in set(range(12)) - set(test_indexes)]
    assert result["artifact"]["preprocessing"]["imputer"]["statistics"][1] == pytest.approx(sorted(training_m)[4])
    assert result["artifact"]["preprocessing"]["imputer"]["statistics"][1] < 100


def test_missing_numeric_features_are_imputed_for_training_and_prediction():
    rows = _regression_rows()
    result = train_model(rows, "y", task="regression", features=["x", "offset"])
    values = predict_model(result["artifact"], [{"x": None}, {"x": 2, "offset": ""}])
    assert len(values) == 2
    assert all(math.isfinite(value) for value in values)


@pytest.mark.parametrize("bad_value", ["not-a-number", float("inf"), float("nan")])
def test_invalid_numeric_features_are_rejected(bad_value):
    rows = _regression_rows()
    rows[0]["x"] = bad_value
    with pytest.raises(ValueError):
        train_model(rows, "y", task="regression", features=["x"])


def test_target_leakage_and_invalid_training_shapes_are_rejected():
    rows = _classification_rows()
    with pytest.raises(ValueError):
        train_model(rows, "label", features=["x", "label"])
    with pytest.raises(ValueError):
        train_model(rows[:11], "label")
    with pytest.raises(ValueError):
        train_model(rows, "label", test_fraction=1.0)


def test_classification_rejects_mixed_scalar_label_types_without_coercion():
    rows = [
        {"x": float(index), "label": 0 if index % 2 else "one"}
        for index in range(24)
    ]
    with pytest.raises(ValueError):
        train_model(rows, "label")


def test_homogeneous_integer_class_labels_survive_json_round_trip():
    rows = [
        {"x": float(index), "label": 0 if index < 12 else 1}
        for index in range(24)
    ]
    artifact = json.loads(json.dumps(train_model(rows, "label")["artifact"], allow_nan=False))
    assert artifact["estimator"]["classes"] == [0, 1]
    assert all(type(value) is int for value in predict_model(artifact, [{"x": 0}, {"x": 23}]))


def test_prediction_rejects_missing_artifact_features_and_malformed_artifacts():
    artifact = train_model(_classification_rows(), "label", features=["x"])["artifact"]
    with pytest.raises(ValueError):
        predict_model(artifact, [{"other": 1}])
    with pytest.raises(ValueError):
        predict_model({"format": "unknown"}, [{"x": 1}])


def test_prediction_of_no_rows_is_an_empty_result():
    artifact = train_model(_classification_rows(), "label", features=["x"])["artifact"]
    assert predict_model(artifact, []) == []
