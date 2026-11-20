import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def backend(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("geonamescache")
    pytest.importorskip("tldextract")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from app import main, registration

    return main, registration


def test_add_portal_persists_rdap_city_and_country(backend, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    main, registration = backend
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "jimix.db")
    requests = []
    response_sent = False

    async def app(scope, receive, send):
        async def track_response(message):
            nonlocal response_sent
            await send(message)
            if scope.get("method") == "POST" and message["type"] == "http.response.body":
                response_sent = True

        await main.app(scope, receive, track_response)

    async def fetch_json(client, url):
        assert response_sent
        with closing(main.db.connect()) as conn:
            assert conn.execute("SELECT url FROM portal").fetchone()["url"] == body["url"]
            assert conn.execute("SELECT COUNT(*) FROM portal_location").fetchone()[0] == 0
        requests.append(str(url))
        if str(url) == registration.IANA_RDAP:
            return {"services": [[["co.uk"], ["https://registry.example/rdap/"]]]}
        if url.host == "registry.example":
            return {"links": [{
                "rel": "related",
                "type": registration.RDAP_TYPE,
                "href": "https://registrar.example/domain/example.co.uk",
            }]}
        return {"entities": [{
            "roles": ["registrant"],
            "vcardArray": ["vcard", [["adr", {"cc": "FR"}, "text", ["", "", "", "Paris", "", "", ""]]]],
        }]}

    monkeypatch.setattr(registration, "fetch_json", fetch_json)
    body = {"url": "https://tenders.example.co.uk/notices?status=open"}
    with TestClient(app) as client:
        created = client.post("/portals", json=body)
        assert created.status_code == 201
        portal_id = created.json()["id"]
        assert client.post("/portals", json=body).json() == {"id": portal_id}
        detail = client.get(f"/portals/{portal_id}").json()
        assert client.get("/portals").json() == [detail]
        assert detail["location"] == {
            "city": "Paris",
            "country": "France",
            "countryCode": "FR",
            "latitude": pytest.approx(48.85, abs=0.02),
            "longitude": pytest.approx(2.35, abs=0.02),
        }
    assert requests == [
        registration.IANA_RDAP,
        "https://registry.example/rdap/domain/example.co.uk",
        "https://registrar.example/domain/example.co.uk",
    ]


def test_delete_portal_cascades_only_its_records(backend, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    main, _ = backend
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "jimix.db")
    with TestClient(main.app) as client:
        with closing(main.db.connect()) as conn, conn:
            for portal_id in ("delete-me", "keep-me"):
                stamp = main.db.now()
                conn.execute("INSERT INTO portal (id, url, created_at) VALUES (?, ?, ?)",
                             (portal_id, f"https://{portal_id}.example", stamp))
                conn.execute("INSERT INTO portal_location (portal_id, city) VALUES (?, 'Paris')", (portal_id,))
                conn.execute("INSERT INTO tender (portal_id, id, last_changed_at) VALUES (?, 'T1', ?)",
                             (portal_id, stamp))
                conn.execute("INSERT INTO run (id, portal_id, kind, started_at) VALUES (?, ?, 'read', ?)",
                             (portal_id, portal_id, stamp))
        assert client.get("/openapi.json").json()["paths"]["/portals/{portal_id}"]["delete"]
        result = client.delete("/portals/delete-me")
        assert result.status_code == 204
        assert result.content == b""
        assert client.delete("/portals/delete-me").status_code == 404
        for suffix in ("", "/tenders", "/runs"):
            assert client.get(f"/portals/delete-me{suffix}").status_code == 404
        assert [portal["id"] for portal in client.get("/portals").json()] == ["keep-me"]
        with closing(main.db.connect()) as conn:
            for table in ("portal_location", "tender", "run"):
                assert [row[0] for row in conn.execute(f"SELECT portal_id FROM {table}")] == ["keep-me"]


@pytest.mark.asyncio
@pytest.mark.parametrize("masked", [False, True])
async def test_whois_fallback_uses_only_published_registrant_fields(backend, monkeypatch, masked):
    _, registration = backend

    async def fetch_json(client, url):
        return {"services": []}

    async def whois_query(server, query):
        if server == "whois.iana.org":
            assert query == "eu"
            return "whois: whois.example\r\n"
        assert (server, query) == ("whois.example", "example.eu")
        if masked:
            return "Registrant Organization: Domains By Proxy, LLC\r\nRegistrant City: Paris\r\nRegistrant Country: FR\r\n"
        return "Registrar City: Paris\r\nRegistrant City: REDACTED FOR PRIVACY\r\nRegistrant Country: FR\r\n"

    monkeypatch.setattr(registration, "fetch_json", fetch_json)
    monkeypatch.setattr(registration, "whois_query", whois_query)
    location = await registration.lookup_registration("https://www.example.eu")
    assert location == (None if masked else registration.RegistrationLocation(None, "France", "FR"))


@pytest.mark.parametrize("roles, name", [(["registrant", "proxy"], "Example"), (["registrant"], "Contact Privacy")])
def test_rdap_ignores_recognizable_privacy_proxy(backend, roles, name):
    _, registration = backend
    record = {"entities": [{
        "roles": roles,
        "vcardArray": ["vcard", [
            ["fn", {}, "text", name],
            ["adr", {"cc": "FR"}, "text", ["", "", "", "Paris", "", "", ""]],
        ]],
    }]}
    assert registration.rdap_address(record) == ("", "")


@pytest.mark.parametrize("failure", ["lookup", "write"])
def test_add_portal_survives_location_failure(backend, monkeypatch, tmp_path, failure):
    from fastapi.testclient import TestClient

    main, registration = backend
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "jimix.db")

    async def fetch_json(client, url):
        raise httpx.ConnectError("Registry unavailable")

    async def lookup_registration(url):
        return registration.RegistrationLocation(None, "France", "FR")

    def save_portal_location(portal_id, location):
        raise sqlite3.OperationalError("database is locked")

    if failure == "lookup":
        monkeypatch.setattr(registration, "fetch_json", fetch_json)
    else:
        monkeypatch.setattr(main, "lookup_registration", lookup_registration)
        monkeypatch.setattr(main, "save_portal_location", save_portal_location)
    with TestClient(main.app) as client:
        created = client.post("/portals", json={"url": "https://example.com"})
        assert created.status_code == 201
        assert "location" not in client.get(f"/portals/{created.json()['id']}").json()


def test_delete_portal_removes_its_records_and_leaves_other_portals(backend, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    main, _ = backend
    monkeypatch.setattr(main.db, "DB_PATH", tmp_path / "jimix.db")
    with TestClient(main.app) as client:
        with closing(main.db.connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO portal (id, url, created_at) VALUES (?, ?, ?)",
                [
                    ("removed", "https://remove.example.com/", main.db.now()),
                    ("retained", "https://retain.example.com/", main.db.now()),
                ],
            )
            conn.execute("INSERT INTO portal_location (portal_id, country) VALUES ('removed', 'France')")
            conn.execute(
                "INSERT INTO tender (portal_id, id, last_changed_at) VALUES ('removed', 'tender', ?)",
                (main.db.now(),),
            )
            conn.execute(
                "INSERT INTO run (id, portal_id, kind, started_at) VALUES ('read', 'removed', 'read', ?)",
                (main.db.now(),),
            )

        response = client.delete("/portals/removed")
        assert response.status_code == 204
        assert response.content == b""
        assert client.delete("/portals/removed").status_code == 404
        assert [portal["id"] for portal in client.get("/portals").json()] == ["retained"]

        with closing(main.db.connect()) as conn:
            assert not conn.execute("SELECT 1 FROM portal_location").fetchone()
            assert not conn.execute("SELECT 1 FROM tender").fetchone()
            assert not conn.execute("SELECT 1 FROM run").fetchone()
