import asyncio
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from app import agent, browser, fetch
from app.fetch import Blocked, Fetcher
from app.recipe import Recipe, apply

PAGE = """
<table>
  <tr><td>1</td><td><a class="zipFileContents" data-oid="older" data-token="token1">Version 1</a></td></tr>
  <tr><td>2</td><td><a class="zipFileContents" data-oid="newest" data-token="token2">Version 2</a></td></tr>
</table>
<a class="filenameEllipsis" href="/files/stale.pdf">Previous files elsewhere on the page</a>
<div id="detailModal"></div>
<script>
const post = (path, body) => fetch(path, {
  method: "POST", body,
  headers: {"Content-Type": "application/x-www-form-urlencoded"}
});
post("/NetServer/DataProvider", "param=FileTree&oid=newest&VALIDATION_TOKEN=token2").catch(() => {});
for (const control of document.querySelectorAll(".zipFileContents")) control.onclick = async () => {
  post("/submit", "bid=1").catch(() => {});
  post("/NetServer/DataProvider", "param=Submit&oid=newest&VALIDATION_TOKEN=token2").catch(() => {});
  post("/NetServer/DataProvider", "param=FileTree&oid=other&VALIDATION_TOKEN=token2").catch(() => {});
  fetch("http://localhost:" + location.port + "/outside").catch(() => {});
  fetch("/forbidden").catch(() => {});
  fetch("/jump").catch(() => {});
  const form = `param=FileTree&oid=${control.dataset.oid}&VALIDATION_TOKEN=${control.dataset.token}`;
  const result = await post("/NetServer/DataProvider", form);
  await result.text();
  document.getElementById("detailModal").innerHTML =
    `<a class="filenameEllipsis" href="/files/${control.dataset.oid}.pdf">Specification</a>`;
  post("/NetServer/DataProvider", form).catch(() => {});
};
</script>
"""


@pytest.mark.parametrize("explicit_modal", [True, False], ids=["explicit", "automatic"])
def test_netserver_modal_reads_links_without_other_actions(monkeypatch, explicit_modal):
    calls = []

    class Site(BaseHTTPRequestHandler):
        def respond(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            calls.append(("GET", self.path))
            if self.path == "/robots.txt":
                return self.respond("User-agent: *\nDisallow: /forbidden")
            if self.path == "/list":
                return self.respond('<article><a href="/notice">Test procurement</a></article>')
            if self.path == "/jump":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/redirect-target")
                self.end_headers()
                return
            return self.respond(PAGE)

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"])).decode()
            calls.append(("POST", self.path, data))
            self.respond('["Specification"]')

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site:
        base = f"http://127.0.0.1:{site.server_port}"

        async def allow_fixture(url):
            assert url.startswith(base + "/")

        monkeypatch.setattr(browser, "check_public", allow_fixture)
        monkeypatch.setattr(fetch, "INTERVAL", 0)
        worker = Thread(target=site.serve_forever, daemon=True)
        worker.start()
        try:
            documents = {"documents": {"path": "a.direct-download[href]"}}
            if explicit_modal:
                documents = {"documents_by_host": {
                    "127.0.0.1": {"modal": "netserver_file_tree", "files": {"path": "a.filenameEllipsis[href]"}},
                }}
            recipe = Recipe.model_validate({
                "method": "html",
                "listing": {
                    "url": f"{base}/list",
                    "records": "article",
                    "fields": {
                        "id": {"path": "a", "attribute": "href"},
                        "url": {"path": "a", "attribute": "href"},
                        "title": {"path": "a"},
                    },
                },
                "detail": {},
                **documents,
            })

            async def read():
                fetcher = Fetcher(transport=httpx.AsyncHTTPTransport())
                try:
                    return await apply(recipe, fetcher)
                finally:
                    await fetcher.close()

            tenders, complete = asyncio.run(read())
        finally:
            site.shutdown()
            worker.join()
    assert complete
    assert tenders[0]["_documentUrls"] == [f"{base}/files/newest.pdf"]
    assert [call for call in calls if call[0] == "POST"] == [
        ("POST", "/NetServer/DataProvider", "param=FileTree&oid=newest&VALIDATION_TOKEN=token2"),
    ]
    assert ("GET", "/jump") in calls
    assert not any(call[1] in ("/outside", "/forbidden", "/redirect-target") for call in calls)


@pytest.mark.parametrize("stalled", [False, True])
def test_browser_waits_for_delayed_files_within_the_render_budget(monkeypatch, stalled):
    release = Event()
    requested = Event()
    resolving = Event()
    stopped = Event()
    if stalled:
        page = (
            '<main><a href="/notice">Published notice</a></main>'
            "<script>fetch('/files.json'); fetch('/queued.json');</script>"
        )
    else:
        page = """
        <app-root></app-root>
        <script>
        setTimeout(async () => {
            const {file} = await (await fetch("/files.json")).json();
            setTimeout(() => {
                const link = document.createElement("a");
                link.href = file;
                link.textContent = "Specification";
                document.querySelector("app-root").append(link);
            }, 200);
        }, 2300);
        </script>
        """

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/files.json":
                requested.set()
                if stalled:
                    release.wait(5)
                    return
                body, content_type = '{"file": "/specification.pdf"}', "application/json"
            elif self.path == "/robots.txt":
                body, content_type = "User-agent: *\nDisallow:", "text/plain"
            elif self.path == "/challenge":
                body = '<div class="g-recaptcha"></div><script>fetch("/queued.json");</script>'
                content_type = "text/html"
            else:
                body, content_type = page, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site:
        base = f"http://127.0.0.1:{site.server_port}"

        async def allow_fixture(url):
            assert url.startswith(base + "/")
            if url.endswith("/queued.json"):
                resolving.set()
                try:
                    await asyncio.sleep(300)
                finally:
                    stopped.set()

        monkeypatch.setattr(browser, "check_public", allow_fixture)
        monkeypatch.setattr(fetch, "INTERVAL", 0)
        monkeypatch.setattr(browser, "QUIET_SECONDS", 0.35)
        monkeypatch.setattr(browser, "RENDER_TIMEOUT_MS", 1200 if stalled else 5000)
        worker = Thread(target=site.serve_forever, daemon=True)
        worker.start()

        async def read(*, listing=False, path="/"):
            fetcher = Fetcher(transport=httpx.AsyncHTTPTransport())
            try:
                if listing:
                    recipe = Recipe.model_validate({
                        "method": "browser",
                        "listing": {
                            "kind": "browser", "url": base + "/", "records": "main a[href]",
                            "fields": {"id": {"path": "$", "attribute": "href"}, "title": {"path": "$"}},
                        },
                    })
                    return await apply(recipe, fetcher)
                return await browser.render(base + path, fetcher, require_stable=path == "/challenge")
            finally:
                await fetcher.close()

        started = time.monotonic()
        try:
            html, calls, final = asyncio.run(read())
            elapsed = time.monotonic() - started
            if stalled:
                assert resolving.is_set() and stopped.is_set()
                with pytest.raises(PlaywrightError, match="did not settle"):
                    asyncio.run(read(listing=True))
                with pytest.raises(Blocked, match="captcha"):
                    asyncio.run(read(path="/challenge"))
        finally:
            release.set()
            site.shutdown()
            worker.join()
    assert requested.is_set()
    assert final == base + "/"
    if stalled:
        assert elapsed < 5
        assert 'href="/notice">Published notice' in html
        assert calls == []
    else:
        assert '<a href="/specification.pdf">Specification</a>' in html
        assert calls == [{"url": base + "/files.json", "method": "GET", "sample": '{"file": "/specification.pdf"}'}]


def test_browser_respects_robots_for_same_origin_data_requests(monkeypatch):
    requests = []

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/robots.txt":
                body, content_type = "User-agent: *\nDisallow: /forbidden.json", "text/plain"
            elif self.path == "/allowed.json":
                body, content_type = '{"file": "/specification.pdf"}', "application/json"
            else:
                body, content_type = """
                <main>Public listing</main>
                <script>
                  fetch("/forbidden.json").catch(() => {});
                  fetch("/allowed.json").then(r => r.json()).then(({file}) => {
                    const link = document.createElement("a");
                    link.href = file;
                    link.textContent = "Specification";
                    document.querySelector("main").append(link);
                  });
                </script>
                """, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site:
        base = f"http://127.0.0.1:{site.server_port}"

        async def allow_fixture(url):
            assert url.startswith(base + "/")

        monkeypatch.setattr(browser, "check_public", allow_fixture)
        monkeypatch.setattr(fetch, "INTERVAL", 0)
        monkeypatch.setattr(browser, "QUIET_SECONDS", 0.1)
        worker = Thread(target=site.serve_forever, daemon=True)
        worker.start()

        async def read():
            fetcher = Fetcher(transport=httpx.AsyncHTTPTransport())
            try:
                return await browser.render(f"{base}/list", fetcher, require_stable=True)
            finally:
                await fetcher.close()

        try:
            html, calls, _ = asyncio.run(read())
        finally:
            site.shutdown()
            worker.join()
    assert "/specification.pdf" in html
    assert [call["url"] for call in calls] == [f"{base}/allowed.json"]
    assert requests == ["/robots.txt", "/list", "/allowed.json"]


TABS = """
<nav><a href="/help">Documents and help</a></nav>
<div role="tab">Details</div><div role="tab" id="docs">Documenten</div><section></section>
<script>
document.getElementById("docs").onclick = async () => {
    const {file} = await (await fetch("/tender/7/documents.json")).json();
    document.querySelector("section").innerHTML = `<a class="file" href="${file}">Bestek</a>`;
};
</script>
"""


def test_render_clicks_a_documents_tab_and_reports_what_it_loaded(monkeypatch):
    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/tender/7/documents.json":
                body, content_type = '{"file": "/files/bestek.pdf"}', "application/json"
            elif self.path == "/robots.txt":
                body, content_type = "User-agent: *\nDisallow:", "text/plain"
            else:
                body, content_type = TABS, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site:
        base = f"http://127.0.0.1:{site.server_port}"

        async def allow_fixture(url):
            assert url.startswith(base + "/")

        monkeypatch.setattr(browser, "check_public", allow_fixture)
        monkeypatch.setattr(fetch, "INTERVAL", 0)
        monkeypatch.setattr(browser, "QUIET_SECONDS", 0.35)
        worker = Thread(target=site.serve_forever, daemon=True)
        worker.start()

        async def read(click):
            fetcher = Fetcher(transport=httpx.AsyncHTTPTransport())
            try:
                return await browser.render(base + "/tender/7", fetcher, click=click)
            finally:
                await fetcher.close()

        try:
            html, calls, _ = asyncio.run(read(agent.DOCUMENT_PAGE))
            plain_html, plain_calls, _ = asyncio.run(read(None))
        finally:
            site.shutdown()
            worker.join()
    assert '<a class="file" href="/files/bestek.pdf">Bestek</a>' in html
    assert calls == [
        {"clicked": "Documenten"},
        {"url": base + "/tender/7/documents.json", "method": "GET", "sample": '{"file": "/files/bestek.pdf"}',
         "afterClicking": "Documenten"},
    ]
    assert "bestek.pdf" not in plain_html and plain_calls == []
