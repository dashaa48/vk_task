"""A small fake of the GitHub "list repository issues" endpoint.

It exists so that the integration tests can inject faults (429, 401, 5xx,
dropped connections, broken JSON, ...) that a real server never produces on
demand. Standard library only.
"""
import hashlib
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ISSUES_PATH = re.compile(r"^/repos/([^/]+/[^/]+)/issues$")


def make_issue(number, created="2026-01-01T00:00:00Z", repo="stub/alpha", title=None,
               login="octocat", labels=(), pr=False):
    """A realistic issue object (with plenty of fields the service must ignore)."""
    issue = {
        "id": 1000 + number,
        "number": number,
        "title": title if title is not None else "Issue %d" % number,
        "state": "open",
        "user": {"login": login, "id": 1, "type": "User"},
        "labels": [{"id": i, "name": name, "color": "ededed"} for i, name in enumerate(labels)],
        "created_at": created,
        "updated_at": created,
        "html_url": "https://github.com/%s/%s/%d" % (repo, "pull" if pr else "issues", number),
        "body": "text",
        "comments": 0,
    }
    if pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/%s/pulls/%d" % (repo, number)}
    return issue


class Reply:
    """A scripted response. `drop=True` closes the connection without answering."""

    def __init__(self, status=200, headers=None, body=b"", drop=False):
        self.status = status
        self.headers = dict(headers or {})
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.drop = drop

    @staticmethod
    def json(status, obj, headers=None):
        return Reply(status, headers, json.dumps(obj))

    @staticmethod
    def dropped():
        return Reply(drop=True)


class FakeGitHub:
    def __init__(self, page_size=2):
        self.repos = {}          # "owner/repo" -> list of issue dicts, in API order
        self.page_size = page_size
        self.scripts = {}        # "owner/repo" -> list of Reply or callable(rec) -> Reply|None
        self.link_style = "page"  # "page": ?page=N, "cursor": ?after=tokN (opaque)
        self.send_link = True
        self.link_on_304 = False
        self.etags = True
        self.requests = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self._httpd = None
        self.url = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                fake._handle(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self.url

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    # -- helpers for tests -------------------------------------------------
    def script(self, repo, *replies):
        self.scripts.setdefault(repo, []).extend(replies)

    def requests_for(self, repo):
        return [r for r in self.requests if r["path"] == "/repos/%s/issues" % repo]

    # -- request handling --------------------------------------------------
    def _handle(self, h):
        parsed = urlparse(h.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        rec = {
            "method": "GET",
            "path": parsed.path,
            "query": query,
            "raw": h.path,
            "headers": {k.lower(): v for k, v in h.headers.items()},
            "time": time.time(),
        }
        with self._lock:
            self.requests.append(rec)
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            reply = self._decide(rec, h)
            self._send(h, reply)
        finally:
            with self._lock:
                self._active -= 1

    def _decide(self, rec, h):
        m = ISSUES_PATH.match(rec["path"])
        if not m:
            return Reply.json(404, {"message": "Not Found"})
        name = m.group(1)
        queue = self.scripts.get(name)
        if queue:
            item = queue.pop(0)          # one scripted entry per request
            if callable(item):
                item = item(rec)         # may return None: serve normally this time
            if item is not None:
                return item
        if name not in self.repos:
            return Reply.json(404, {"message": "Not Found"})
        return self._page(name, rec, h)

    def _page(self, name, rec, h):
        items = self.repos[name]
        q = rec["query"]
        if self.link_style == "page":
            page = int(q.get("page", "1"))
        else:
            page = int(q["after"][3:]) if "after" in q else 1
        start = (page - 1) * self.page_size
        chunk = items[start:start + self.page_size]
        body = json.dumps(chunk).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        more = start + self.page_size < len(items)
        last = max(1, -(-len(items) // self.page_size))
        link = None
        if more and self.send_link:
            base = "http://%s/repos/%s/issues" % (h.headers.get("Host"), name)
            if self.link_style == "page":
                nxt = "%s?state=open&sort=created&direction=asc&per_page=100&page=%d" % (base, page + 1)
                lst = "%s?state=open&sort=created&direction=asc&per_page=100&page=%d" % (base, last)
            else:
                nxt = "%s?after=tok%d" % (base, page + 1)
                lst = "%s?after=tok%d" % (base, last)
            link = '<%s>; rel="next", <%s>; rel="last"' % (nxt, lst)
        if self.etags:
            etag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
            headers["ETag"] = etag
            if rec["headers"].get("if-none-match") == etag:
                if link and self.link_on_304:
                    headers["Link"] = link
                return Reply(304, headers, b"")
        if link:
            headers["Link"] = link
        return Reply(200, headers, body)

    def _send(self, h, reply):
        if reply.drop:
            h.close_connection = True
            try:
                h.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            h.connection.close()
            return
        h.send_response(reply.status)
        sent = {k.lower() for k in reply.headers}
        for k, v in reply.headers.items():
            h.send_header(k, v)
        if "content-type" not in sent:
            h.send_header("Content-Type", "application/json; charset=utf-8")
        if reply.status != 304:
            h.send_header("Content-Length", str(len(reply.body)))
        h.end_headers()
        if reply.status != 304:
            h.wfile.write(reply.body)
