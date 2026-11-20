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
        assert set(first.json()) == {"id"}
        assert first.json()["id"] == duplicate.json()["id"]
        listed = client.get("/portals").json()
        assert listed[0]["id"] == first.json()["id"]
        assert listed[0]["state"] == "integrating"
        assert client.get(f"/portals/{first.json()['id']}").json()["url"] == "https://example.org/tenders"
        detail = client.get(f"/portals/{first.json()['id']}").json()
        assert set(detail) == {"id", "url", "state", "reason", "tenderCount", "lastSuccessAt"}


def test_delete_portal_and_cascaded_data(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        assert client.delete(f"/portals/{portal_id}").status_code == 204
        assert client.get(f"/portals/{portal_id}").status_code == 404
        assert client.get(f"/portals/{portal_id}/runs").status_code == 404
        assert client.get(f"/portals/{portal_id}/tenders").status_code == 404


def test_delete_cancels_queued_portal_work(tmp_path, monkeypatch):
    class Queue:
        def __init__(self):
            self.cancelled = []

        def enqueue(self, portal_id, run_id):
            pass

        def cancel_portal(self, portal_id):
            self.cancelled.append(portal_id)

    queue = Queue()
    monkeypatch.setattr("app.routers.portals.refresh_jobs", queue)
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        assert client.delete(f"/portals/{portal_id}").status_code == 204
    assert queue.cancelled == [portal_id]


def test_refresh_returns_accepted_and_reuses_running_run(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        first = client.post(f"/portals/{portal_id}/refresh")
        second = client.post(f"/portals/{portal_id}/refresh")
        assert first.status_code == second.status_code == 202
        assert first.json()["runId"] == second.json()["runId"]
        assert set(first.json()) == {"runId"}
        assert len(client.get(f"/portals/{portal_id}/runs").json()) == 1
        assert client.get(f"/portals/{portal_id}").json()["state"] == "healing"


def test_tenders_changed_since_and_documents_persist(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        portal_id = client.post("/portals", json={"url": "https://example.org"}).json()["id"]
        with database.connect() as db:
            old = db.execute("INSERT INTO tenders(portal_id,title,changed_at) VALUES(?,?,?)",
                             (portal_id, "Old", "2025-01-01T00:00:00+00:00")).lastrowid
            recent = db.execute("INSERT INTO tenders(portal_id,title,changed_at) VALUES(?,?,?)",
                                (portal_id, "Recent", "2025-02-01T00:00:00+00:00")).lastrowid
            db.execute("INSERT INTO documents(portal_id,tender_id,title,created_at) VALUES(?,?,?,?)",
                       (portal_id, recent, "Notice", "2025-02-01T00:00:00+00:00"))
        response = client.get(f"/portals/{portal_id}/tenders", params={"changedSince": "2025-01-15T00:00:00+00:00"})
        assert [t["title"] for t in response.json()] == ["Recent"]
        with database.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_refresh_missing_portal_is_404(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        assert client.post("/portals/404/refresh").status_code == 404


def test_items_are_persisted_in_sqlite(tmp_path, monkeypatch):
    with client_for(tmp_path, monkeypatch) as client:
        created = client.post("/api/items", json={"name": "Bid notice", "description": "Initial record"})
        assert created.status_code == 201
        item = created.json()
        assert item == {"id": item["id"], "name": "Bid notice", "description": "Initial record"}
        assert client.get("/api/items").json() == [item]
        assert client.get(f"/api/items/{item['id']}").json() == item

    # A fresh application client sees the same SQLite row, demonstrating that the
    # endpoint no longer depends on process-local state.
    with client_for(tmp_path, monkeypatch) as client:
        assert client.get("/api/items").json() == [item]
        assert client.delete(f"/api/items/{item['id']}").status_code == 204
        assert client.get(f"/api/items/{item['id']}").status_code == 404
