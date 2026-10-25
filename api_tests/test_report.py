"""Report schema, field mapping and the shape of requests sent to the API."""
import datetime as dt

from conftest import report_of
from helpers import ISSUE_FIELDS, REPO_FIELDS, check_issue_order, day, numbers, parse_rfc3339
from mock_github import make_issue


def test_report_schema_and_field_mapping(env):
    env.fake.repos["stub/alpha"] = [
        make_issue(7, day(0), title="Привет, мир", login="hubot", labels=("bug", "help wanted")),
        make_issue(9, day(1), title='with "quotes" <b>', login="octocat"),
    ]
    before = dt.datetime.now(dt.timezone.utc)
    report = report_of(env.run("stub/alpha"))
    after = dt.datetime.now(dt.timezone.utc)

    assert set(report) == {"generated_at", "repos"}
    generated = parse_rfc3339(report["generated_at"])
    assert before - dt.timedelta(seconds=5) <= generated <= after + dt.timedelta(seconds=5)
    assert isinstance(report["repos"], list) and len(report["repos"]) == 1

    repo = report["repos"][0]
    assert set(repo) == REPO_FIELDS
    assert repo["repo"] == "stub/alpha"
    assert repo["error"] is None
    assert isinstance(repo["count"], int) and repo["count"] == len(repo["issues"]) == 2

    first, second = repo["issues"]
    for issue in repo["issues"]:
        assert set(issue) == ISSUE_FIELDS
        assert isinstance(issue["number"], int) and not isinstance(issue["number"], bool)
        assert isinstance(issue["title"], str) and isinstance(issue["author"], str)
        assert isinstance(issue["labels"], list) and all(isinstance(x, str) for x in issue["labels"])
        assert isinstance(issue["created_at"], str) and isinstance(issue["url"], str)
    assert first == {
        "number": 7, "title": "Привет, мир", "author": "hubot",
        "labels": ["bug", "help wanted"],          # labels[].name, in API order
        "created_at": day(0), "url": "https://github.com/stub/alpha/issues/7",
    }
    assert second["title"] == 'with "quotes" <b>'
    assert second["labels"] == []                     # an empty array, not null
    check_issue_order(repo["issues"])


def test_empty_repository(env):
    env.fake.repos["stub/empty"] = []
    report = report_of(env.run("stub/empty"))
    repo = report["repos"][0]
    assert repo["error"] is None and repo["count"] == 0 and repo["issues"] == []


def test_repos_keep_argument_order_and_duplicates_are_dropped(env):
    for name in ("stub/b", "stub/a", "stub/c"):
        env.fake.repos[name] = [make_issue(1, day(0), repo=name)]
    report = report_of(env.run("stub/b,stub/a,stub/b,stub/c,stub/a"))
    assert [r["repo"] for r in report["repos"]] == ["stub/b", "stub/a", "stub/c"]
    assert len(env.fake.requests_for("stub/b")) == 1


def test_issues_are_sorted_by_created_at_then_number(env):
    env.fake.page_size = 100
    env.fake.repos["stub/alpha"] = [
        make_issue(5, day(2)), make_issue(3, day(2)), make_issue(9, day(0)),
        make_issue(1, day(2)), make_issue(2, day(1)),
    ]
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [9, 2, 1, 3, 5]


def test_request_shape_and_headers_without_token(env):
    env.fake.repos["stub/alpha"] = [make_issue(1, day(0))]
    report_of(env.run("stub/alpha"))
    req = env.fake.requests[0]
    assert req["method"] == "GET" and req["path"] == "/repos/stub/alpha/issues"
    assert req["query"]["state"] == "open"
    assert req["query"]["sort"] == "created"
    assert req["query"]["direction"] == "asc"
    assert req["query"]["per_page"] == "100"
    assert "since" not in req["query"], "the API parameter 'since' filters by update time and must not be used"
    headers = req["headers"]
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2026-03-10"
    assert headers.get("user-agent", "").strip() != ""
    assert "authorization" not in headers


def test_token_is_sent_as_bearer(env):
    env.fake.repos["stub/alpha"] = issues_one()
    report_of(env.run("stub/alpha", token="tok-123"))
    assert env.fake.requests[0]["headers"]["authorization"] == "Bearer tok-123"


def issues_one():
    return [make_issue(1, day(0))]


def test_base_url_with_trailing_slash_is_accepted(env):
    env.fake.repos["stub/alpha"] = issues_one()
    from conftest import run_ghreport
    proc = run_ghreport("stub/alpha", env.tmp / "c2", base=env.fake.url + "/")
    assert report_of(proc)["repos"][0]["count"] == 1
    assert all("//" not in r["path"] for r in env.fake.requests)


def test_out_file_keeps_stdout_empty(env):
    env.fake.repos["stub/alpha"] = issues_one()
    out = env.tmp / "report.json"
    from conftest import run_ghreport
    proc = run_ghreport("stub/alpha", env.cache_dir, "--out", str(out), base=env.fake.url)
    assert proc.returncode == 0 and proc.stdout == ""
    import json
    with open(str(out), encoding="utf-8") as f:
        assert json.load(f)["repos"][0]["count"] == 1
