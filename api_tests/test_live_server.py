"""Server-agnostic checks against any GITHUB_API_BASE_URL (stub, GitHub, ...).

The expected report is computed by the test itself from the same API, so no
knowledge about the data is needed. Set GITHUB_API_BASE_URL (and optionally
GHREPORT_LIVE_REPOS, GITHUB_TOKEN) to enable; skipped otherwise.
Read-only: only GET requests are issued.
"""
import json
import os
import re
import urllib.request

import pytest

from conftest import read_trace, report_of, run_ghreport
from helpers import check_issue_order, parse_rfc3339, strip_volatile

BASE = os.environ.get("GITHUB_API_BASE_URL", "").rstrip("/")
REPOS = [r for r in os.environ.get("GHREPORT_LIVE_REPOS", "stub/alpha").split(",") if r]


def skip_unless_live():
    if not BASE:
        pytest.skip("GITHUB_API_BASE_URL is not set")


def api_get(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ghreport-api-tests",
               "X-GitHub-Api-Version": "2026-03-10"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), resp.headers.get("Link", "")


def oracle(repo):
    """All open non-PR issues of `repo` in API order, de-duplicated, with the page count."""
    url = "%s/repos/%s/issues?state=open&sort=created&direction=asc&per_page=100" % (BASE, repo)
    seen, out, pages = set(), [], 0
    while url:
        data, link = api_get(url)
        pages += 1
        for item in data:
            if "pull_request" in item or item["number"] in seen:
                continue
            seen.add(item["number"])
            out.append(item)
        m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
        url = m.group(1) if m else None
    out.sort(key=lambda i: (parse_rfc3339(i["created_at"]), i["number"]))
    return out, pages


def expected(items):
    return [{"number": i["number"], "title": i["title"], "author": i["user"]["login"],
             "labels": [l["name"] for l in i["labels"]], "created_at": i["created_at"],
             "url": i["html_url"]} for i in items]


def run(repo, tmp_path, *args, token=None):
    proc = run_ghreport(repo, tmp_path / "cache", *args, base=BASE,
                        token=token if token is not None else os.environ.get("GITHUB_TOKEN"))
    return report_of(proc)["repos"][0]


@pytest.mark.parametrize("repo", REPOS)
def test_report_matches_the_api(tmp_path, repo):
    skip_unless_live()
    items, _ = oracle(repo)
    got = run(repo, tmp_path, "--limit", "100000")
    assert got["error"] is None and got["count"] == len(got["issues"])
    assert got["issues"] == expected(items)
    check_issue_order(got["issues"])
    assert len({i["number"] for i in got["issues"]}) == len(got["issues"])


@pytest.mark.parametrize("repo", REPOS)
def test_limit_across_pages(tmp_path, repo):
    skip_unless_live()
    items, pages = oracle(repo)
    if len(items) < 2:
        pytest.skip("not enough issues in %s" % repo)
    limit = len(items) - 1
    got = run(repo, tmp_path, "--limit", str(limit))
    assert got["count"] == limit
    assert got["issues"] == expected(items)[:limit]


@pytest.mark.parametrize("repo", REPOS)
def test_since_filter(tmp_path, repo):
    skip_unless_live()
    items, _ = oracle(repo)
    if not items:
        pytest.skip("no issues in %s" % repo)
    border = items[len(items) // 2]["created_at"][:10]
    start = parse_rfc3339(border + "T00:00:00Z")
    want = [i for i in items if parse_rfc3339(i["created_at"]) >= start]
    got = run(repo, tmp_path, "--since", border, "--limit", "100000")
    assert [i["number"] for i in got["issues"]] == [i["number"] for i in want]


@pytest.mark.parametrize("repo", REPOS)
def test_cache_round_trip(tmp_path, repo):
    skip_unless_live()
    trace = tmp_path / "t1.jsonl"
    cmd = ["--limit", "100000", "--trace", str(trace)]
    first = report_of(run_ghreport(repo, tmp_path / "cache", *cmd, base=BASE,
                                   token=os.environ.get("GITHUB_TOKEN")))
    assert not any(r["from_cache"] for r in read_trace(trace))
    trace2 = tmp_path / "t2.jsonl"
    second = report_of(run_ghreport(repo, tmp_path / "cache", "--limit", "100000", "--trace", str(trace2),
                                    base=BASE, token=os.environ.get("GITHUB_TOKEN")))
    records = read_trace(trace2)
    assert records and all(r["from_cache"] and r["status"] == 304 for r in records)
    assert strip_volatile(first) == strip_volatile(second)
