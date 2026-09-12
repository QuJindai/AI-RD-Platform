import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ard.api import create_app
from ard.features.sources import install, validate_query


@pytest.fixture
def source(tmp_path, monkeypatch):
    database = tmp_path / "external.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE inspections (id INTEGER, value REAL, label TEXT)")
        db.executemany("INSERT INTO inspections VALUES (?,?,?)",
                       [(1, 2.5, "normal"), (2, 5.0, "review"), (3, 3.5, "normal")])
    config = {"quality": {"driver": "sqlite", "path": str(database), "projects": ["*"],
                         "queries": {"recent": {"sql": "SELECT * FROM inspections WHERE id >= :start ORDER BY id",
                                                "parameters": {"start": "integer"}, "max_rows": 100,
                                                "cache_ttl_seconds": 3600}}}}
    monkeypatch.setenv("ARD_DATA_SOURCES", json.dumps(config))
    app = create_app(tmp_path / "runtime")
    install(app)
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Source test"}).json()
        yield client, project["id"], database, config, app.state.service


def test_read_only_snapshot_cache_and_explicit_refresh(source, monkeypatch):
    client, pid, database, config, service = source
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                         json={"query_id": "recent", "parameters": {"start": 2}})
    assert result.status_code == 201, result.text
    created = result.json()
    assert created["cached"] is False
    assert service.rows(created["dataset"]["id"]) == [
        {"id": 2, "value": 5.0, "label": "review"}, {"id": 3, "value": 3.5, "label": "normal"}]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO inspections VALUES (4, 7, 'review')")
    second = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                         json={"query_id": "recent", "parameters": {"start": 2}}).json()
    assert second["cached"] is True
    assert second["dataset"]["id"] == created["dataset"]["id"]
    refreshed = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                           json={"query_id": "recent", "parameters": {"start": 2}, "refresh": True}).json()
    assert refreshed["dataset"]["row_count"] == 3
    assert refreshed["dataset"]["id"] != created["dataset"]["id"]
    assert refreshed["dataset"]["parents"] == [created["dataset"]["id"]]
    assert service.rows(created["dataset"]["id"])[-1]["id"] == 3


def test_source_catalog_does_not_reveal_connection_or_sql(source):
    client, pid, database, config, _ = source
    result = client.get(f"/api/projects/{pid}/sources")
    assert result.status_code == 200
    text = result.text
    assert str(database) not in text and "SELECT" not in text
    assert result.json()[0]["queries"][0]["parameters"] == {"start": "integer"}


def test_source_scope_and_parameter_validation(source, monkeypatch):
    client, pid, _, config, _ = source
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    config["quality"]["projects"] = [pid]
    monkeypatch.setenv("ARD_DATA_SOURCES", json.dumps(config))
    assert client.get(f"/api/projects/{other}/sources").json() == []
    assert client.post(f"/api/projects/{other}/sources/quality/snapshot",
                       json={"query_id": "recent", "parameters": {"start": 1}}).status_code == 403
    for params in [{"start": "1 OR 1=1"}, {"start": 1, "other": 1}, {}]:
        result = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                             json={"query_id": "recent", "parameters": params})
        assert result.status_code == 400, result.text
    assert client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                       json={"query_id": "recent", "parameters": {"start": 1}, "path": "/etc/passwd"}).status_code == 422


@pytest.mark.parametrize("query", [
    "DELETE FROM inspections", "SELECT 1; DELETE FROM inspections",
    "WITH gone AS (DELETE FROM inspections RETURNING *) SELECT * FROM gone",
    "SELECT load_extension('/tmp/code')", "SELECT pg_sleep(10)",
    "SELECT * INTO copied FROM inspections", "PRAGMA writable_schema=ON",
    "ATTACH DATABASE '/tmp/other.db' AS other",
])
def test_rejects_non_read_queries_and_side_effect_functions(query):
    with pytest.raises(ValueError):
        validate_query(query, "sqlite", 100)


def test_select_literals_are_not_mistaken_for_write_statements():
    query = validate_query("SELECT 'delete from table' AS label, COUNT(*) AS n FROM inspections", "sqlite", 10)
    assert "delete from table" in query


def test_read_limits_fail_without_persisting_partial_dataset(source, monkeypatch):
    client, pid, _, config, service = source
    config["quality"]["queries"]["recent"]["max_rows"] = 1
    monkeypatch.setenv("ARD_DATA_SOURCES", json.dumps(config))
    result = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                         json={"query_id": "recent", "parameters": {"start": 1}})
    assert result.status_code == 400, result.text
    assert service.store.list("dataset", pid) == []
    assert service.store.list("source_snapshot", pid) == []


def test_changed_query_configuration_invalidates_cache(source, monkeypatch):
    client, pid, _, config, _ = source
    body = {"query_id": "recent", "parameters": {"start": 1}}
    first = client.post(f"/api/projects/{pid}/sources/quality/snapshot", json=body).json()
    config["quality"]["queries"]["recent"]["sql"] = "SELECT id,label FROM inspections WHERE id >= :start ORDER BY id"
    monkeypatch.setenv("ARD_DATA_SOURCES", json.dumps(config))
    second = client.post(f"/api/projects/{pid}/sources/quality/snapshot", json=body).json()
    assert second["cached"] is False
    assert second["dataset"]["id"] != first["dataset"]["id"]
    assert second["dataset"]["columns"] == ["id", "label"]


def test_read_only_role_cannot_materialize_snapshot(tmp_path, monkeypatch):
    app = create_app(tmp_path, identities={"a" * 20: {"user": "auditor", "role": "auditor", "projects": ["*"]}})
    install(app)
    with TestClient(app) as client:
        response = client.post("/api/projects/missing/sources/quality/snapshot",
                               json={"query_id": "recent", "parameters": {}},
                               headers={"Authorization": "Bearer " + "a" * 20})
        assert response.status_code == 403


def test_connection_errors_do_not_expose_configured_secrets(source, monkeypatch):
    client, pid, _, config, _ = source
    config["quality"].update(driver="postgresql", url="postgresql://private-user:Secret-Value@127.0.0.1:1/private-db")
    monkeypatch.setenv("ARD_DATA_SOURCES", json.dumps(config))
    response = client.post(f"/api/projects/{pid}/sources/quality/snapshot",
                           json={"query_id": "recent", "parameters": {"start": 1}})
    assert response.status_code == 400
    assert "Secret-Value" not in response.text and "private-user" not in response.text
