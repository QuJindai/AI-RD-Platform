"""Configured read-only database extraction with immutable cached snapshots."""
from datetime import date, datetime, timezone
from decimal import Decimal
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import Field
import sqlglot
from sqlglot import exp

from ard.features.common import Input, Svc, User, install_once
from ard.store import encode, now

router = APIRouter(prefix="/api", tags=["read-only sources"])
_slots = threading.BoundedSemaphore(2)
_identifier = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_functions = {
    "ABS", "AVG", "COUNT", "SUM", "MIN", "MAX", "ROUND", "COALESCE", "NULLIF",
    "LOWER", "UPPER", "TRIM", "LTRIM", "RTRIM", "SUBSTRING", "LENGTH", "CHAR_LENGTH",
    "REPLACE", "STR_POSITION", "CAST", "TRY_CAST", "DATE", "TIME", "DATETIME",
    "TIME_TO_STR", "STR_TO_TIME", "EXTRACT", "CURRENT_DATE", "CURRENT_TIMESTAMP",
    "CASE", "IF", "CONCAT", "CONCAT_WS", "FLOOR", "CEIL", "CEILING", "MOD",
}


class SnapshotInput(Input):
    query_id: str = Field(min_length=1, max_length=64)
    parameters: dict = Field(default_factory=dict, max_length=20)
    name: str | None = Field(default=None, max_length=150)
    refresh: bool = False


def settings():
    raw = os.environ.get("ARD_DATA_SOURCES", "{}")
    try:
        if len(raw) > 500000:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or len(value) > 32:
            raise ValueError()
        for key, source in value.items():
            if not _identifier.fullmatch(key) or not isinstance(source, dict):
                raise ValueError()
            if source.get("driver") not in ("sqlite", "postgresql", "mysql"):
                raise ValueError()
            queries = source.get("queries")
            projects = source.get("projects", ["*"])
            if not isinstance(queries, dict) or not 1 <= len(queries) <= 64:
                raise ValueError()
            if not isinstance(projects, list) or not all(isinstance(pid, str) for pid in projects):
                raise ValueError()
            for qid, query in queries.items():
                if not _identifier.fullmatch(qid) or not isinstance(query, dict):
                    raise ValueError()
                sql = query.get("sql")
                if not isinstance(sql, str) or not 1 <= len(sql) <= 10000:
                    raise ValueError()
                params = query.get("parameters", {})
                if not isinstance(params, dict) or len(params) > 20 or any(
                    not _identifier.fullmatch(k) or v not in ("text", "integer", "number", "boolean")
                    for k, v in params.items()
                ):
                    raise ValueError()
                for field, default, low, high in [
                    ("max_rows", 5000, 1, 50000), ("cache_ttl_seconds", 3600, 0, 86400)
                ]:
                    number = query.get(field, default)
                    if type(number) is not int or not low <= number <= high:
                        raise ValueError()
                timeout = query.get("timeout_seconds", 5)
                if type(timeout) not in (int, float) or not math.isfinite(timeout) or not .1 <= timeout <= 30:
                    raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError("数据源配置无效，请管理员检查 ARD_DATA_SOURCES") from None


def validate_query(sql, driver, limit):
    dialect = "postgres" if driver == "postgresql" else driver
    try:
        trees = sqlglot.parse(sql, read=dialect)
        if len(trees) != 1 or not isinstance(trees[0], exp.Query):
            raise ValueError()
        for item in trees[0].walk():
            if isinstance(item, (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)):
                raise ValueError()
            if isinstance(item, exp.Func):
                if isinstance(item, exp.Anonymous) or item.sql_name().upper() not in _functions:
                    raise ValueError()
        # Bind names stay in the DB-API/SQLAlchemy :name notation.
        return "SELECT * FROM (" + sql.strip().rstrip(";") + ") AS ard_snapshot LIMIT " + str(limit + 1)
    except (ValueError, sqlglot.errors.SqlglotError):
        raise ValueError("数据源只允许单条只读 SELECT，函数必须在只读白名单内") from None


def _parameters(query, supplied):
    expected = query.get("parameters", {})
    if set(supplied) != set(expected):
        raise ValueError("查询参数必须与预设参数完全一致")
    for key, kind in expected.items():
        value = supplied[key]
        valid = (
            kind == "text" and isinstance(value, str) and len(value) <= 2000 or
            kind == "integer" and type(value) is int and abs(value) <= 2**53 - 1 or
            kind == "number" and (type(value) is int and abs(value) <= 2**53 - 1 or
                                  type(value) is float and math.isfinite(value)) or
            kind == "boolean" and type(value) is bool
        )
        if not valid:
            raise ValueError("查询参数类型或长度无效：" + key)
    return supplied


def _scalar(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bytes):
        value = "base64:" + base64.b64encode(value).decode()
    if isinstance(value, str) and len(value) > 100000:
        raise ValueError("数据库单字段超过100000字符")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("数据库返回非有限数值")
    if value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("数据库返回不支持的字段类型")
    return value


def _collect(cursor, limit, columns=None):
    names = list(columns) if columns is not None else [str(column[0]) for column in cursor.description]
    if not names or len(names) > 200 or len(set(names)) != len(names):
        raise ValueError("数据库列名重复或超过200列")
    rows, size = [], 0
    for row in cursor:
        if len(rows) >= limit:
            raise ValueError("查询结果超过预设行数上限，请收窄查询范围")
        item = {key: _scalar(value) for key, value in zip(names, row)}
        size += len(encode(item))
        if size > 20 * 1024 * 1024:
            raise ValueError("数据库查询结果超过20MiB")
        rows.append(item)
    if not rows:
        raise ValueError("查询未返回数据，不创建空数据版本")
    return rows


def _read_sqlite(source, sql, params, limit, timeout):
    path = Path(source.get("path", "")).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError("已配置的 SQLite 数据源不可用")
    deadline = time.monotonic() + timeout
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}
        safe_functions = {"abs", "avg", "count", "sum", "min", "max", "round", "coalesce", "nullif", "ifnull",
                          "lower", "upper", "trim", "ltrim", "rtrim", "substr", "substring", "length",
                          "replace", "instr", "date", "time", "datetime", "strftime", "julianday", "unixepoch"}

        def authorize(action, first, second, database, trigger):
            if action in allowed:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in safe_functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return _collect(db.execute(sql, params), limit)
    except sqlite3.Error:
        raise ValueError("只读数据源查询失败或超时；请检查预设查询和字段权限") from None
    finally:
        db.close()


def _read_remote(source, sql, params, limit, timeout):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    from sqlalchemy.exc import SQLAlchemyError
    engine = None
    try:
        raw_url = source.get("url") or os.environ.get(source.get("url_env", ""), "")
        url = make_url(raw_url)
        driver = source["driver"]
        if url.get_backend_name() != driver:
            raise ValueError("数据源驱动与连接配置不一致")
        if url.query:
            raise ValueError("请通过固定驱动配置连接，不接受连接URL查询参数")
        drivername = "postgresql+psycopg" if driver == "postgresql" else "mysql+pymysql"
        connect_args = {"connect_timeout": max(1, min(int(timeout), 5))}
        if driver == "mysql":
            connect_args.update(read_timeout=max(1, math.ceil(timeout)), write_timeout=max(1, math.ceil(timeout)))
        engine = create_engine(url.set(drivername=drivername), poolclass=NullPool, connect_args=connect_args)
        with engine.connect() as connection:
            if driver == "postgresql":
                connection = connection.execution_options(postgresql_readonly=True)
                connection.exec_driver_sql("SET LOCAL statement_timeout = " + str(int(timeout * 1000)))
            else:
                connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET SESSION MAX_EXECUTION_TIME = " + str(int(timeout * 1000)))
                connection.commit()
            result = connection.execution_options(stream_results=True).execute(text(sql), params)
            try:
                return _collect(result, limit, result.keys())
            finally:
                result.close()
                connection.rollback()
    except (SQLAlchemyError, ValueError, TypeError, OSError):
        raise ValueError("只读数据库连接或查询失败，请管理员检查连接、权限和超时设置") from None
    finally:
        if engine is not None:
            engine.dispose()


def _allowed(source, pid):
    projects = source.get("projects", ["*"])
    return "*" in projects or pid in projects


@router.get("/projects/{pid}/sources")
def list_sources(pid: str, s: Svc, user: User):
    s.project(pid, user)
    return [{"id": key, "name": source.get("name", key), "driver": source["driver"],
             "queries": [{"id": qid, "name": query.get("name", qid), "parameters": query.get("parameters", {}),
                          "max_rows": query.get("max_rows", 5000),
                          "cache_ttl_seconds": query.get("cache_ttl_seconds", 3600)}
                         for qid, query in source["queries"].items()]}
            for key, source in settings().items() if _allowed(source, pid)]


@router.post("/projects/{pid}/sources/{source_id}/snapshot", status_code=201)
def snapshot(pid: str, source_id: str, body: SnapshotInput, s: Svc, user: User):
    user.allow_write()
    s.project(pid, user)
    source = settings().get(source_id)
    if source is None:
        raise ValueError("未配置此数据源")
    if not _allowed(source, pid):
        raise HTTPException(403, "此数据源未授权给当前项目")
    query = source["queries"].get(body.query_id)
    if query is None:
        raise ValueError("未配置此预设查询")
    params = _parameters(query, body.parameters)
    limit, ttl = query.get("max_rows", 5000), query.get("cache_ttl_seconds", 3600)
    sql = validate_query(query["sql"], source["driver"], limit)
    endpoint = source.get("path") or source.get("url") or os.environ.get(source.get("url_env", ""), "")
    fingerprint = hashlib.sha256(encode({"pid": pid, "source_id": source_id, "driver": source["driver"],
        "query": query, "parameters": params, "endpoint": endpoint})).hexdigest()
    from ard.features.data import is_archived
    prior = next((item for item in s.store.list("source_snapshot", pid)
                  if item["fingerprint"] == fingerprint and
                  not is_archived(s, s.store.get(item["dataset_id"], "dataset"))), None)
    if prior and not body.refresh and (
        datetime.now(timezone.utc) - datetime.fromisoformat(prior["created_at"])
    ).total_seconds() < ttl:
        return {"cached": True, "snapshot": prior, "dataset": s.record(prior["dataset_id"], "dataset", user)}
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "已有两个数据源查询在运行，请稍后重试")
    start = time.perf_counter()
    try:
        reader = _read_sqlite if source["driver"] == "sqlite" else _read_remote
        rows = reader(source, sql, params, limit, query.get("timeout_seconds", 5))
        with s.store.transaction():
            dataset = s.add_dataset(pid, body.name or source.get("name", source_id) + " · " + body.query_id,
                                    rows, ["source-snapshot"], user,
                                    parents=[prior["dataset_id"]] if prior else [])
            record = s.store.create("source_snapshot", pid, {
                "source_id": source_id, "query_id": body.query_id, "driver": source["driver"],
                "dataset_id": dataset["id"], "fingerprint": fingerprint,
                "row_count": len(rows), "queried_at": now(),
                "latency_ms": (time.perf_counter() - start) * 1000,
                "creator": user.user, "size_bytes": 0,
            }, user.user)
        return {"cached": False, "snapshot": record, "dataset": dataset}
    finally:
        _slots.release()


def install(app):
    install_once(app, router, "sources")
