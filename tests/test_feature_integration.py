"""Cross-module boundaries through the installed application factory."""
from fastapi.testclient import TestClient

from ard.api import create_app
from ard.features import install


def test_all_routers_install_exactly_once(tmp_path):
    app = create_app(tmp_path)
    count = len(app.routes)
    install(app)
    assert len(app.routes) == count
    paths = app.openapi()["paths"].keys()
    assert {
        "/api/projects/{pid}/sources",
        "/api/projects/{pid}/data-catalog",
        "/api/knowledge/projects/{pid}/index/build",
        "/api/projects/{pid}/model-baselines",
        "/api/projects/{pid}/workflows/validate",
        "/api/skills/builtins",
        "/api/projects/{pid}/settings",
        "/api/projects/{pid}/mcp",
    } <= paths


def test_archive_blocks_core_new_work_but_keeps_historical_reads(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        pid = client.post("/api/projects", json={"name": "Synthetic boundary"}).json()["id"]
        asset = client.post(f"/api/projects/{pid}/datasets", json={
            "name": "Historical data", "rows": [{"x": 1}, {"x": 2}, {"x": 3}, {"x": 4}]
        }).json()
        other = client.post(f"/api/projects/{pid}/datasets", json={
            "name": "Active data", "rows": [{"x": 5}, {"x": 6}]
        }).json()
        result = client.post(f"/api/assets/{asset['id']}/archive", json={
            "archived": True, "expected_revision": 0, "confirm": asset["id"], "reason": "Synthetic archival check"
        })
        assert result.status_code == 201, result.text
        assert [item["id"] for item in client.get(f"/api/projects/{pid}/datasets").json()] == [other["id"]]
        assert client.get(f"/api/assets/{asset['id']}/rows").json()["total"] == 4
        assert client.get(f"/api/assets/{asset['id']}/export").status_code == 200
        for suffix, body in [
            ("transform", {"operations": [{"type": "drop_duplicates"}]}),
            ("split", {"ratio": .5}), ("transfer", {}), ("train", {"target": "x", "task": "regression"})
        ]:
            response = client.post(f"/api/assets/{asset['id']}/{suffix}", json=body)
            assert response.status_code == 409, (suffix, response.text)
        merged = client.post(f"/api/projects/{pid}/merge",
                             json={"asset_ids": [asset["id"], other["id"]], "name": "Rejected"})
        assert merged.status_code == 409
        assert len(client.app.state.service.store.list("dataset", pid)) == 2
        assert client.get("/api/audit/verify").json()["valid"]
