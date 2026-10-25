#!/usr/bin/env python3
"""Reference implementation of the ghreport contract, used ONLY to validate the
test-suite (see README). GHREPORT_REF_MUTANT=<name[,name]> injects a bug."""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MUT = set(filter(None, os.environ.get("GHREPORT_REF_MUTANT", "").split(",")))
API_VERSION = "2026-03-10"
UA = "ghreport-ref/1.0"


def die_usage(msg):
    sys.stderr.write("ghreport: %s\n" % msg)
    sys.exit(2)


def parse_args(argv):
    opts = {"limit": 100, "out": "-", "trace": None, "cache": "./.ghreport-cache", "since": None, "repos": None}
    names = {"--repos": "repos", "--limit": "limit", "--since": "since", "--out": "out",
             "--trace": "trace", "--cache-dir": "cache"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a not in names or i + 1 >= len(argv):
            die_usage("неизвестный или неполный аргумент %r" % a)
        opts[names[a]] = argv[i + 1]
        i += 2
    if not opts["repos"]:
        die_usage("--repos обязателен")
    repos = []
    for r in opts["repos"].split(","):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r):
            die_usage("некорректный репозиторий %r" % r)
        if r not in repos:
            repos.append(r)
    opts["repos"] = repos
    if isinstance(opts["limit"], str):
        if not re.fullmatch(r"\d+", opts["limit"]) or int(opts["limit"]) < 1:
            die_usage("--limit должен быть целым числом, не меньшим единицы")
        opts["limit"] = int(opts["limit"])
    if opts["since"] is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", opts["since"]):
            die_usage("--since: YYYY-MM-DD")
        try:
            opts["since"] = dt.datetime.strptime(opts["since"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            die_usage("--since: YYYY-MM-DD")
    return opts


def ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Fatal(Exception):
    pass


class RepoError(Exception):
    def __init__(self, kind):
        self.kind = kind


class Svc:
    def __init__(self, o):
        self.o = o
        base = os.environ.get("GITHUB_API_BASE_URL") or "https://api.github.com"
        self.base = base.rstrip("/")
        self.host = urllib.parse.urlparse(self.base).netloc
        self.token = os.environ.get("GITHUB_TOKEN", "")
        os.makedirs(o["cache"], exist_ok=True)
        self.trace = open(o["trace"], "w", encoding="utf-8") if o["trace"] else None

    def log(self, url, status, attempt, from_cache, error):
        if self.trace:
            rec = {"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "method": "GET",
                   "url": url, "status": status, "attempt": attempt, "from_cache": from_cache, "error": error}
            if "trace_auth" in MUT:
                rec["authorization"] = "Bearer " + self.token
            self.trace.write(json.dumps(rec) + "\n")
            self.trace.flush()

    def once(self, url, etag, attempt):
        cur = url
        for _ in range(6):
            headers = {"Accept": "application/vnd.github+json", "User-Agent": UA,
                       "X-GitHub-Api-Version": API_VERSION}
            if self.token and urllib.parse.urlparse(cur).netloc == self.host:
                headers["Authorization"] = "Bearer " + self.token
            if etag and "no_etag" not in MUT:
                headers["If-None-Match"] = etag
            if "bogus_inm" in MUT and not etag:
                headers["If-None-Match"] = '"zzz"'
            try:
                try:
                    r = OPENER.open(urllib.request.Request(cur, headers=headers), timeout=30)
                    status, hdrs, body = r.status, r.headers, r.read()
                except urllib.error.HTTPError as e:
                    status, hdrs, body = e.code, e.headers, e.read()
            except Exception as e:  # network error
                self.log(cur, 0, attempt, False, "timeout" if "timed out" in str(e) else "network")
                raise
            fc = status == 304 and bool(etag) and "cache_not_reported" not in MUT
            self.log(cur, status, attempt, fc, None)
            if status in (301, 302, 307, 308) and hdrs.get("Location"):
                cur = urllib.parse.urljoin(cur, hdrs["Location"])
                continue
            return status, hdrs, body, cur
        raise RepoError("request_failed")

    def cpath(self, url):
        return os.path.join(self.o["cache"], hashlib.sha256(url.encode()).hexdigest() + ".json")

    def fetch(self, url):
        entry = None
        try:
            with open(self.cpath(url), encoding="utf-8") as f:
                entry = json.load(f)
            if entry.get("url") != url or not entry.get("etag"):
                entry = None
        except (OSError, ValueError):
            entry = None
        etag = entry["etag"] if entry else ""
        kind, wait = "request_failed", 0
        for attempt in range(1, 5):
            try:
                status, hdrs, body, final = self.once(url, etag, attempt)
            except RepoError:
                raise
            except Exception:
                kind, wait = "request_failed", 2 ** (attempt - 1)
                status = None
            if status is not None:
                if status == 200:
                    try:
                        data = json.loads(body.decode("utf-8"))
                        if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                            raise ValueError
                    except ValueError:
                        raise RepoError("invalid_response")
                    link = hdrs.get("Link")
                    if hdrs.get("ETag") and "token_in_cache" not in MUT or hdrs.get("ETag"):
                        entry_out = {"url": url, "etag": hdrs.get("ETag"), "link": link, "body": data}
                        if "token_in_cache" in MUT:
                            entry_out["auth"] = "Bearer " + self.token
                        with open(self.cpath(url), "w", encoding="utf-8") as f:
                            json.dump(entry_out, f)
                    return data, link, final
                if status == 304:
                    if not entry:
                        raise RepoError("invalid_response")
                    return entry["body"], hdrs.get("Link") or entry.get("link"), final
                if status == 401:
                    if "401_partial" in MUT:
                        raise RepoError("request_failed")
                    raise Fatal("unauthorized")
                if status == 404:
                    if "404_fatal" in MUT:
                        raise Fatal("all_failed")
                    raise RepoError("not_found")
                limited = status == 429 or (status == 403 and (hdrs.get("Retry-After") or hdrs.get("X-RateLimit-Remaining") == "0"
                                                               or "rate limit" in body.decode("utf-8", "replace").lower()))
                if limited:
                    kind = "rate_limited"
                    if hdrs.get("Retry-After") and "no_retry_after" not in MUT:
                        wait = float(hdrs["Retry-After"])
                    elif hdrs.get("X-RateLimit-Reset"):
                        wait = max(0.0, float(hdrs["X-RateLimit-Reset"]) - time.time())
                    elif "no_retry_after" in MUT:
                        wait = 0
                    else:
                        wait = 60
                elif status >= 500:
                    kind, wait = "request_failed", 2 ** (attempt - 1)
                    if "no_backoff" in MUT:
                        wait = 0
                else:
                    raise RepoError("request_failed")
            if attempt == 4 or ("no_retry_5xx" in MUT and kind == "request_failed"):
                break
            time.sleep(wait)
        raise RepoError(kind)

    def issues(self, repo):
        o = self.o
        url = "%s/repos/%s/issues?state=open&sort=created&direction=asc&per_page=100" % (self.base, repo)
        got, seen, visited, page_no = [], set(), set(), 1
        limit = o["limit"] + (1 if "limit_plus_one" in MUT else 0)
        while url and url not in visited:
            visited.add(url)
            data, link, final = self.fetch(url)
            for it in data:
                if "pull_request" in it and "keep_pr" not in MUT:
                    continue
                if "pull_request" in it and "keep_pr" in MUT and it.get("pull_request") is None:
                    continue
                if o["since"] is not None:
                    c = ts(it["created_at"])
                    if (c < o["since"] and "since_inclusive" not in MUT) or (c <= o["since"] and "since_inclusive" in MUT):
                        continue
                if it["number"] in seen and "no_dedup" not in MUT:
                    continue
                seen.add(it["number"])
                got.append(it)
                if len(got) >= limit and "no_early_stop" not in MUT:
                    break
            if len(got) >= limit and "no_early_stop" not in MUT:
                break
            m = re.search(r'<([^>]*)>\s*;\s*rel="next"', link or "")
            page_no += 1
            if "manual_pages" in MUT:
                q = urllib.parse.urlparse(url)
                if "page=" in url and (len(data) > 0):
                    url = re.sub(r"page=\d+", "page=%d" % page_no, url)
                elif len(data) > 0:
                    url = url + "&page=%d" % page_no
                else:
                    url = None
            else:
                url = urllib.parse.urljoin(final, m.group(1)) if m else None
        if "limit_plus_one" in MUT or "no_early_stop" in MUT:
            got = got[:o["limit"]] if "limit_plus_one" not in MUT else got
        if "reverse_sort" in MUT:
            got.sort(key=lambda i: (ts(i["created_at"]), i["number"]), reverse=True)
        elif "no_sort" not in MUT:
            key = (lambda i: ts(i["created_at"])) if "tie_by_title" in MUT else (lambda i: (ts(i["created_at"]), i["number"]))
            got.sort(key=key)
        res = []
        for it in got:
            labels = [l["name"] for l in it.get("labels", [])]
            res.append({"number": it["number"], "title": it["title"],
                        "author": (it.get("user") or {}).get("login", ""), "labels": labels,
                        "created_at": it["created_at"], "url": it["html_url"]})
        return res


def main():
    o = parse_args(sys.argv[1:])
    svc = Svc(o)
    out, failed = [], 0
    for repo in o["repos"]:
        entry = {"repo": repo, "error": None, "count": 0, "issues": []}
        try:
            entry["issues"] = svc.issues(repo)
            entry["count"] = len(entry["issues"])
        except RepoError as e:
            entry["error"] = e.kind
            failed += 1
            sys.stderr.write("ghreport: %s: %s\n" % (repo, e.kind))
        except Fatal as e:
            print(json.dumps({"error": str(e)}))
            return 3
        out.append(entry)
    if failed == len(o["repos"]):
        print(json.dumps({"error": "all_failed"}))
        return 3
    report = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "repos": out}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if o["out"] == "-":
        sys.stdout.write(text + "\n")
    else:
        with open(o["out"], "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
