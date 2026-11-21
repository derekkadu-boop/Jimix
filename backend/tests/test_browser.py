import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from app import browser, fetch
from app.fetch import Fetcher
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
