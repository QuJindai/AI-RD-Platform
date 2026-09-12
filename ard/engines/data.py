"""Safe, pure helpers for small tabular datasets.

``parse_table`` accepts UTF-8 ``.csv``, ``.json`` (an array of objects),
and ``.jsonl``/``.ndjson`` (one object per non-empty line).  Tables are limited
to 50,000 rows and 200 distinct string-named columns.

``profile`` returns ``row_count``, ``column_count``, and ``columns`` in
first-seen order.  Each column entry has ``name``, ``missing``, ``unique``, and
``type`` (``null``, ``boolean``, ``number``, ``string``, or ``mixed``).

``transform`` takes a list of operation objects.  Missing means ``None`` or a
blank string.  Supported operation forms are::

    {"type": "drop_duplicates", "columns": [...] }  # columns optional
    {"type": "drop_empty", "columns": [...] }       # columns optional
    {"type": "strip", "columns": [...] }            # columns optional
    {"type": "fill_missing", "column": name, "value": value}
    {"type": "select", "columns": [...]}
    {"type": "rename", "mapping": {old: new, ...}}
    {"type": "filter", "column": name,
     "op": "eq"|"ne"|"gt"|"ge"|"lt"|"le"|"contains", "value": value}
    {"type": "cast_numeric", "columns": [...]}
    {"type": "lowercase", "columns": [...] }        # columns optional
    {"type": "replace", "column": name, "old": value, "new": value}
    {"type": "clip", "column": name, "min": number, "max": number}

For ``drop_empty``, a row is removed when every selected column is missing.
Keys not shown for an operation are rejected; ``min`` and ``max`` are each
optional for ``clip``, but at least one is required.
Numeric filters and clipping accept finite numbers or numeric strings.  The
input rows, including nested values, are never mutated.

``split_rows`` returns seeded shuffled deep copies; ``ratio`` is the fraction
in the first result.  ``parse_archive`` applies the same parsers to safe table
members of a ZIP while enforcing the documented byte/member limits and a
200:1 expansion-ratio guard for members larger than 1 MiB.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
from pathlib import PurePosixPath
import random
import stat
import zipfile
from typing import Any


MAX_ROWS = 50_000
MAX_COLUMNS = 200
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200
MAX_COMPRESSION_RATIO = 200
_TABLE_SUFFIXES = {".csv", ".json", ".jsonl", ".ndjson"}
_OPERATION_SCHEMAS = {
    "drop_duplicates": ({"type", "columns"}, {"type"}),
    "drop_empty": ({"type", "columns"}, {"type"}),
    "strip": ({"type", "columns"}, {"type"}),
    "fill_missing": ({"type", "column", "value"}, {"type", "column", "value"}),
    "select": ({"type", "columns"}, {"type", "columns"}),
    "rename": ({"type", "mapping"}, {"type", "mapping"}),
    "filter": ({"type", "column", "op", "value"}, {"type", "column", "op", "value"}),
    "cast_numeric": ({"type", "columns"}, {"type", "columns"}),
    "lowercase": ({"type", "columns"}, {"type"}),
    "replace": ({"type", "column", "old", "new"}, {"type", "column", "old", "new"}),
    "clip": ({"type", "column", "min", "max"}, {"type", "column"}),
}


def _invalid_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON value: {value}")


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except (UnicodeDecodeError, AttributeError) as exc:
        raise ValueError("table content must be UTF-8 bytes") from exc


def _columns(rows: list[dict]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for column in row:
            if column not in seen:
                seen.add(column)
                result.append(column)
    return result


def _validate_rows(rows: Any) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("table must be a list of objects")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"table exceeds {MAX_ROWS} rows")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every row must be an object")
        if any(not isinstance(key, str) or not key for key in row):
            raise ValueError("column names must be non-empty strings")
    if len(_columns(rows)) > MAX_COLUMNS:
        raise ValueError(f"table exceeds {MAX_COLUMNS} columns")
    return rows


def parse_table(filename: str, content: bytes) -> list[dict]:
    """Parse one supported UTF-8 table, raising ``ValueError`` on invalid input."""
    if not isinstance(filename, str) or not isinstance(content, bytes):
        raise ValueError("filename must be a string and content must be bytes")
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix not in _TABLE_SUFFIXES:
        raise ValueError(f"unsupported table format: {suffix or 'none'}")
    text = _decode(content)
    try:
        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(text, newline=""))
            if reader.fieldnames is None:
                rows: list[dict] = []
            else:
                if any(name is None or not name.strip() for name in reader.fieldnames):
                    raise ValueError("CSV column names must be non-empty")
                if len(set(reader.fieldnames)) != len(reader.fieldnames):
                    raise ValueError("CSV column names must be unique")
                if len(reader.fieldnames) > MAX_COLUMNS:
                    raise ValueError(f"table exceeds {MAX_COLUMNS} columns")
                rows = []
                for row in reader:
                    if None in row:
                        raise ValueError("CSV row has more values than its header")
                    rows.append(dict(row))
                    if len(rows) > MAX_ROWS:
                        raise ValueError(f"table exceeds {MAX_ROWS} rows")
        elif suffix == ".json":
            rows = json.loads(text, parse_constant=_invalid_json_constant)
        else:
            rows = []
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line, parse_constant=_invalid_json_constant))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"invalid JSONL at line {line_number}") from exc
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"table exceeds {MAX_ROWS} rows")
    except (csv.Error, json.JSONDecodeError) as exc:
        raise ValueError("malformed table") from exc
    return copy.deepcopy(_validate_rows(rows))


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "mixed"


def _fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


def profile(rows: list[dict]) -> dict:
    """Return deterministic shape and basic per-column statistics."""
    _validate_rows(rows)
    columns = _columns(rows)
    summaries = []
    for column in columns:
        values = [row.get(column) for row in rows]
        present = [value for value in values if not _missing(value)]
        types = {_value_type(value) for value in present}
        inferred = "null" if not types else next(iter(types)) if len(types) == 1 else "mixed"
        summaries.append(
            {
                "name": column,
                "missing": len(values) - len(present),
                "unique": len({_fingerprint(value) for value in present}),
                "type": inferred,
            }
        )
    return {"row_count": len(rows), "column_count": len(columns), "columns": summaries}


def _require_columns(rows: list[dict], requested: Any, *, optional: bool = False) -> list[str]:
    available = _columns(rows)
    if requested is None and optional:
        return available
    if not isinstance(requested, list) or not requested or any(not isinstance(item, str) for item in requested):
        raise ValueError("columns must be a non-empty list of names")
    if len(set(requested)) != len(requested):
        raise ValueError("columns must not contain duplicates")
    unknown = [column for column in requested if column not in available]
    if unknown:
        raise ValueError(f"unknown column: {unknown[0]}")
    return list(requested)


def _require_column(rows: list[dict], operation: dict) -> str:
    column = operation.get("column")
    if not isinstance(column, str) or column not in _columns(rows):
        raise ValueError(f"unknown column: {column}")
    return column


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or _missing(value):
        raise ValueError("expected a finite numeric value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("expected a finite numeric value") from exc
    if not math.isfinite(number):
        raise ValueError("expected a finite numeric value")
    return int(number) if number.is_integer() else number


def _compare(left: Any, op: str, right: Any) -> bool:
    if not isinstance(op, str):
        raise ValueError("filter op must be a string")
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        if not isinstance(left, str) or not isinstance(right, str):
            raise ValueError("contains requires string operands")
        return right in left
    if op not in {"gt", "ge", "lt", "le"}:
        raise ValueError(f"unsupported filter operator: {op}")
    left_number, right_number = _number(left), _number(right)
    return {
        "gt": left_number > right_number,
        "ge": left_number >= right_number,
        "lt": left_number < right_number,
        "le": left_number <= right_number,
    }[op]


def _validate_operation(operation: Any) -> str:
    if not isinstance(operation, dict) or not isinstance(operation.get("type"), str):
        raise ValueError("each operation must have a string type")
    kind = operation["type"]
    schema = _OPERATION_SCHEMAS.get(kind)
    if schema is None:
        raise ValueError(f"unsupported operation: {kind}")
    allowed, required = schema
    unexpected = set(operation) - allowed
    missing = required - set(operation)
    if unexpected:
        raise ValueError(f"unexpected field for {kind}: {sorted(map(str, unexpected))[0]}")
    if missing:
        raise ValueError(f"missing field for {kind}: {sorted(missing)[0]}")
    if "columns" in operation and not isinstance(operation["columns"], list):
        raise ValueError("columns must be a list of names")
    if kind == "filter" and not isinstance(operation["op"], str):
        raise ValueError("filter op must be a string")
    if kind == "clip" and "min" not in operation and "max" not in operation:
        raise ValueError("clip requires min or max")
    return kind


def transform(rows: list[dict], operations: list[dict]) -> list[dict]:
    """Apply validated catalog operations to a deep copy of ``rows``."""
    _validate_rows(rows)
    if not isinstance(operations, list):
        raise ValueError("operations must be a list")
    result = copy.deepcopy(rows)
    for operation in operations:
        kind = _validate_operation(operation)
        if kind == "drop_duplicates":
            columns = _require_columns(result, operation.get("columns"), optional=True)
            seen: set[tuple[str, ...]] = set()
            output = []
            for row in result:
                key = tuple(_fingerprint(row.get(column)) for column in columns)
                if key not in seen:
                    seen.add(key)
                    output.append(row)
            result = output
        elif kind == "drop_empty":
            columns = _require_columns(result, operation.get("columns"), optional=True)
            result = [row for row in result if not all(_missing(row.get(column)) for column in columns)]
        elif kind in {"strip", "lowercase"}:
            columns = _require_columns(result, operation.get("columns"), optional=True)
            for row in result:
                for column in columns:
                    value = row.get(column)
                    if isinstance(value, str):
                        row[column] = value.strip() if kind == "strip" else value.lower()
        elif kind == "fill_missing":
            column = _require_column(result, operation)
            if "value" not in operation:
                raise ValueError("fill_missing requires value")
            for row in result:
                if _missing(row.get(column)):
                    row[column] = copy.deepcopy(operation["value"])
        elif kind == "select":
            columns = _require_columns(result, operation.get("columns"))
            result = [{column: row.get(column) for column in columns} for row in result]
        elif kind == "rename":
            mapping = operation.get("mapping")
            available = _columns(result)
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("rename requires a non-empty mapping")
            if any(old not in available for old in mapping):
                raise ValueError("rename references an unknown column")
            if any(not isinstance(new, str) or not new for new in mapping.values()):
                raise ValueError("renamed columns must be non-empty strings")
            renamed = [mapping.get(column, column) for column in available]
            if len(set(renamed)) != len(renamed):
                raise ValueError("rename would create duplicate columns")
            result = [
                {mapping.get(column, column): value for column, value in row.items()}
                for row in result
            ]
        elif kind == "filter":
            column = _require_column(result, operation)
            if "op" not in operation or "value" not in operation:
                raise ValueError("filter requires op and value")
            result = [row for row in result if _compare(row.get(column), operation["op"], operation["value"])]
        elif kind == "cast_numeric":
            columns = _require_columns(result, operation.get("columns"))
            for row in result:
                for column in columns:
                    if not _missing(row.get(column)):
                        row[column] = _number(row[column])
        elif kind == "replace":
            column = _require_column(result, operation)
            if "old" not in operation or "new" not in operation:
                raise ValueError("replace requires old and new")
            for row in result:
                if row.get(column) == operation["old"]:
                    row[column] = copy.deepcopy(operation["new"])
        elif kind == "clip":
            column = _require_column(result, operation)
            if "min" not in operation and "max" not in operation:
                raise ValueError("clip requires min or max")
            lower = _number(operation["min"]) if "min" in operation else None
            upper = _number(operation["max"]) if "max" in operation else None
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("clip min must not exceed max")
            for row in result:
                if _missing(row.get(column)):
                    continue
                value = _number(row[column])
                if lower is not None:
                    value = max(value, lower)
                if upper is not None:
                    value = min(value, upper)
                row[column] = value
        else:
            raise ValueError(f"unsupported operation: {kind}")
        _validate_rows(result)
    return result


def split_rows(rows: list[dict], ratio: float, seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Return deterministic shuffled partitions, with ``ratio`` in the first."""
    _validate_rows(rows)
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(float(ratio)):
        raise ValueError("ratio must be a finite number between zero and one")
    if not 0 < ratio < 1 or len(rows) < 2:
        raise ValueError("ratio must create two non-empty partitions")
    first_size = int(len(rows) * ratio)
    if first_size < 1 or first_size >= len(rows):
        raise ValueError("ratio must create two non-empty partitions")
    indexes = list(range(len(rows)))
    random.Random(seed).shuffle(indexes)
    first = [copy.deepcopy(rows[index]) for index in indexes[:first_size]]
    second = [copy.deepcopy(rows[index]) for index in indexes[first_size:]]
    return first, second


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError("archive contains an unsafe path")
    mode = info.external_attr >> 16
    if mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ValueError("archive contains a link or special file")
    if info.flag_bits & 1:
        raise ValueError("encrypted archive members are unsupported")
    if info.file_size < 0 or info.compress_size < 0:
        raise ValueError("archive member has invalid size metadata")
    if info.file_size > 1024 * 1024 and (
        info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
    ):
        raise ValueError("archive member has a dangerous compression ratio")


def parse_archive(content: bytes) -> list[dict]:
    """Merge supported table members from a bounded, safe ZIP archive."""
    if not isinstance(content, bytes):
        raise ValueError("archive content must be bytes")
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds the compressed size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive has too many members")
            total_declared = 0
            for info in members:
                _safe_member(info)
                total_declared += info.file_size
                if total_declared > MAX_EXPANDED_BYTES:
                    raise ValueError("archive exceeds the expanded size limit")

            rows: list[dict] = []
            total_read = 0
            for info in members:
                if info.is_dir() or PurePosixPath(info.filename.lower()).suffix not in _TABLE_SUFFIXES:
                    continue
                chunks = []
                with archive.open(info, "r") as member:
                    while chunk := member.read(1024 * 1024):
                        total_read += len(chunk)
                        if total_read > MAX_EXPANDED_BYTES:
                            raise ValueError("archive exceeds the expanded size limit")
                        chunks.append(chunk)
                rows.extend(parse_table(info.filename, b"".join(chunks)))
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"table exceeds {MAX_ROWS} rows")
            return _validate_rows(rows)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise ValueError("invalid ZIP archive") from exc
