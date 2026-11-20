from fastapi.testclient import TestClient

from app import database
from app.main import app


def client_for(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "portals.sqlite3"))
    database.initialize_database()
    return TestClient(app)


def test_register_and_duplicate_returns_same_portal(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        first = client.post("/portals", json={"url": "https://example.org/tenders"})
        duplicate = client.post("/portals", json={"url": "https://example.org/tenders"})
        assert first.status_code == duplicate.status_code == 201
        assert first.json()["id"] == duplicate.json()["id"]
        assert first.json()["state"] == "integrating"
        assert client.get("/portals").json() == [first.json()]
        assert client.get(f"/portals/{first.json()['id']}").json()["url"] == "https://example.org/tenders"


def test_delete_portal_and_cascaded_data(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        assert client.delete(f"/portals/{portal_id}").status_code == 204
        assert client.get(f"/portals/{portal_id}").status_code == 404
        assert client.get(f"/portals/{portal_id}/runs").status_code == 404
        assert client.get(f"/portals/{portal_id}/tenders").status_code == 404


def test_refresh_returns_accepted_and_reuses_running_run(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        first = client.post(f"/portals/{portal_id}/refresh")
        second = client.post(f"/portals/{portal_id}/refresh")
        assert first.status_code == second.status_code == 202
        assert first.json()["runId"] == second.json()["runId"]
        assert first.json()["status"] == "running"
        assert len(client.get(f"/portals/{portal_id}/runs").json()) == 1
        assert client.get(f"/portals/{portal_id}").json()["state"] == "healing"


def test_refresh_missing_portal_is_404(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        assert client.post("/portals/404/refresh").status_code == 404
