import copy
import io
import json
import stat
import zipfile

import pytest

from ard.engines.data import parse_archive, parse_table, profile, split_rows, transform


def test_parse_table_supports_csv_json_and_jsonl():
    assert parse_table("people.csv", b"name,age\nAda,37\nLin,29\n") == [
        {"name": "Ada", "age": "37"},
        {"name": "Lin", "age": "29"},
    ]
    expected = [{"name": "Ada", "age": 37}, {"name": "Lin", "age": 29}]
    assert parse_table("people.json", json.dumps(expected).encode()) == expected
    content = b'{"name":"Ada","age":37}\n{"name":"Lin","age":29}\n'
    assert parse_table("people.jsonl", content) == expected


def test_parse_table_rejects_unsupported_or_malformed_tables():
    with pytest.raises(ValueError):
        parse_table("people.txt", b"hello")
    with pytest.raises(ValueError):
        parse_table("people.json", b'{"not":"an array"}')
    with pytest.raises(ValueError):
        parse_table("people.jsonl", b'{"ok": 1}\nnot-json\n')


def test_parse_table_rejects_header_only_csv_above_column_limit():
    header = ",".join(f"column_{index}" for index in range(201)) + "\n"
    with pytest.raises(ValueError):
        parse_table("wide.csv", header.encode())


def test_profile_reports_shape_missing_unique_and_inferred_types():
    rows = [
        {"age": 2, "name": " Ada ", "active": True},
        {"age": None, "name": "Ada", "active": False},
        {"age": 4.5, "name": "", "active": None},
    ]
    assert profile(rows) == {
        "row_count": 3,
        "column_count": 3,
        "columns": [
            {"name": "age", "missing": 1, "unique": 2, "type": "number"},
            {"name": "name", "missing": 1, "unique": 2, "type": "string"},
            {"name": "active", "missing": 1, "unique": 2, "type": "boolean"},
        ],
    }


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ({"type": "drop_empty", "columns": ["note"]}, []),
        ({"type": "strip", "columns": ["name"]}, [{"name": "Ada", "score": "2", "note": ""}, {"name": "Ada", "score": "9", "note": None}]),
        ({"type": "fill_missing", "column": "note", "value": "n/a"}, [{"name": " Ada ", "score": "2", "note": "n/a"}, {"name": "Ada", "score": "9", "note": "n/a"}]),
        ({"type": "select", "columns": ["name"]}, [{"name": " Ada "}, {"name": "Ada"}]),
        ({"type": "rename", "mapping": {"score": "points"}}, [{"name": " Ada ", "points": "2", "note": ""}, {"name": "Ada", "points": "9", "note": None}]),
        ({"type": "filter", "column": "score", "op": "gt", "value": 3}, [{"name": "Ada", "score": "9", "note": None}]),
        ({"type": "cast_numeric", "columns": ["score"]}, [{"name": " Ada ", "score": 2, "note": ""}, {"name": "Ada", "score": 9, "note": None}]),
        ({"type": "lowercase", "columns": ["name"]}, [{"name": " ada ", "score": "2", "note": ""}, {"name": "ada", "score": "9", "note": None}]),
        ({"type": "replace", "column": "note", "old": "", "new": "missing"}, [{"name": " Ada ", "score": "2", "note": "missing"}, {"name": "Ada", "score": "9", "note": None}]),
        ({"type": "clip", "column": "score", "min": 3, "max": 7}, [{"name": " Ada ", "score": 3, "note": ""}, {"name": "Ada", "score": 7, "note": None}]),
    ],
)
def test_transform_executes_each_catalog_operation_without_mutating_input(operation, expected):
    rows = [
        {"name": " Ada ", "score": "2", "note": ""},
        {"name": "Ada", "score": "9", "note": None},
    ]
    original = copy.deepcopy(rows)
    assert transform(rows, [operation]) == expected
    assert rows == original


def test_transform_drop_duplicates_keeps_the_first_matching_row():
    rows = [{"name": "Ada", "score": 2}, {"name": "Ada", "score": 9}]
    assert transform(rows, [{"type": "drop_duplicates", "columns": ["name"]}]) == [
        {"name": "Ada", "score": 2}
    ]


def test_transform_rejects_unknown_operations_fields_and_unsafe_comparisons():
    rows = [{"a": "not-a-number"}]
    with pytest.raises(ValueError):
        transform(rows, [{"type": "unknown"}])
    with pytest.raises(ValueError):
        transform(rows, [{"type": "strip", "columns": ["missing"]}])
    with pytest.raises(ValueError):
        transform(rows, [{"type": "filter", "column": "a", "op": "gt", "value": 1}])


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "drop_duplicates"},
        {"type": "drop_empty"},
        {"type": "strip"},
        {"type": "fill_missing", "column": "a", "value": "filled"},
        {"type": "select", "columns": ["a"]},
        {"type": "rename", "mapping": {"a": "renamed"}},
        {"type": "filter", "column": "a", "op": "eq", "value": "value"},
        {"type": "cast_numeric", "columns": ["number"]},
        {"type": "lowercase"},
        {"type": "replace", "column": "a", "old": "value", "new": "new"},
        {"type": "clip", "column": "number", "min": 0},
    ],
)
def test_transform_rejects_unexpected_keys_for_every_operation_schema(operation):
    operation["unexpected"] = True
    with pytest.raises(ValueError):
        transform([{"a": "value", "number": 2}], [operation])


def test_transform_rejects_non_string_filter_operator_with_value_error():
    with pytest.raises(ValueError):
        transform([{"a": "value"}], [{"type": "filter", "column": "a", "op": [], "value": "value"}])


def test_transform_rejects_explicit_null_optional_columns():
    with pytest.raises(ValueError):
        transform([{"a": " value "}], [{"type": "strip", "columns": None}])


def test_split_rows_is_seeded_complete_and_independent():
    rows = [{"id": index, "nested": {"value": index}} for index in range(20)]
    train_a, test_a = split_rows(rows, 0.75, seed=7)
    train_b, test_b = split_rows(rows, 0.75, seed=7)
    assert (train_a, test_a) == (train_b, test_b)
    assert len(train_a) == 15
    assert len(test_a) == 5
    assert {row["id"] for row in train_a}.isdisjoint(row["id"] for row in test_a)
    assert {row["id"] for row in train_a + test_a} == set(range(20))
    train_a[0]["nested"]["value"] = -1
    assert all(row["nested"]["value"] >= 0 for row in rows + train_b + test_b)


def _archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def test_parse_archive_merges_only_supported_safe_tables():
    content = _archive(
        [
            ("folder/a.csv", "id,name\n1,Ada\n"),
            ("b.jsonl", '{"id":2,"name":"Lin"}\n'),
            ("README.txt", "ignored"),
        ]
    )
    assert parse_archive(content) == [
        {"id": "1", "name": "Ada"},
        {"id": 2, "name": "Lin"},
    ]


def test_parse_archive_rejects_traversal_symlinks_and_compression_bombs():
    with pytest.raises(ValueError):
        parse_archive(_archive([("../escape.csv", "id\n1\n")]))

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        link = zipfile.ZipInfo("linked.csv")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "target.csv")
    with pytest.raises(ValueError):
        parse_archive(output.getvalue())

    with pytest.raises(ValueError):
        parse_archive(_archive([("bomb.csv", b"x" * (2 * 1024 * 1024))]))


def test_parse_archive_rejects_aggregate_tables_above_column_limit():
    first = json.dumps([{f"a{index}": index for index in range(101)}])
    second = json.dumps([{f"b{index}": index for index in range(100)}])
    with pytest.raises(ValueError):
        parse_archive(_archive([("first.json", first), ("second.json", second)]))
