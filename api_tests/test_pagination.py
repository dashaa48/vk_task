"""Pagination via the Link header, --limit, --since, filtering and de-duplication."""
from conftest import report_of
from helpers import check_issue_order, day, issues, numbers
from mock_github import Reply, make_issue
import json


def test_all_pages_are_collected_sequentially(env):
    env.fake.repos["stub/alpha"] = issues(9)
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == list(range(1, 10))
    assert len(env.fake.requests) == 5                      # 9 items / 2 per page
    assert env.fake.max_active == 1, "requests must be sequential"


def test_next_page_url_is_taken_from_link_not_constructed(env):
    env.fake.link_style = "cursor"                          # opaque cursors: ?after=tokN
    env.fake.repos["stub/alpha"] = issues(5)
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [1, 2, 3, 4, 5]
    assert all("page" not in r["query"] for r in env.fake.requests[1:])
    assert [r["query"].get("after") for r in env.fake.requests] == [None, "tok2", "tok3"]


def test_missing_link_means_last_page(env):
    env.fake.send_link = False                              # more data exists, but no Link header
    env.fake.repos["stub/alpha"] = issues(6)
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [1, 2]
    assert len(env.fake.requests) == 1, "the service must not invent page=2 itself"


def test_limit_selects_exactly_limit_items_and_stops_early(env):
    env.fake.repos["stub/alpha"] = issues(9)
    repo = report_of(env.run("stub/alpha", "--limit", "3"))["repos"][0]
    assert numbers(repo) == [1, 2, 3] and repo["count"] == 3
    assert len(env.fake.requests) == 2, "page 3 must not be requested once the limit is reached"


def test_limit_larger_than_available(env):
    env.fake.repos["stub/alpha"] = issues(3)
    repo = report_of(env.run("stub/alpha", "--limit", "50"))["repos"][0]
    assert numbers(repo) == [1, 2, 3]


def test_limit_one(env):
    env.fake.repos["stub/alpha"] = issues(5)
    repo = report_of(env.run("stub/alpha", "--limit", "1"))["repos"][0]
    assert numbers(repo) == [1] and len(env.fake.requests) == 1


def test_default_limit_is_100(env):
    env.fake.page_size = 100
    env.fake.repos["stub/alpha"] = issues(130)
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert repo["count"] == 100 and numbers(repo) == list(range(1, 101))


def test_pull_requests_are_excluded(env):
    items = issues(6)
    for i in (0, 3, 5):                                      # make #1, #4, #6 pull requests
        items[i] = make_issue(items[i]["number"], items[i]["created_at"], pr=True)
    items[1]["pull_request"] = None                          # the key alone is the marker
    env.fake.repos["stub/alpha"] = items
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [3, 5]
    assert repo["count"] == 2


def test_pull_requests_do_not_count_towards_limit(env):
    items = [make_issue(1, day(0), pr=True), make_issue(2, day(1), pr=True)] + issues(5, first=3)
    for k, item in enumerate(items[2:]):
        item["created_at"] = day(k + 2)
    env.fake.repos["stub/alpha"] = items
    repo = report_of(env.run("stub/alpha", "--limit", "2"))["repos"][0]
    assert numbers(repo) == [3, 4]


def test_duplicates_across_pages_are_reported_once(env):
    items = issues(4)
    env.fake.page_size = 2
    env.fake.repos["stub/alpha"] = [items[0], items[1], items[1], items[2], items[2], items[3]]
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [1, 2, 3, 4] and repo["count"] == 4


def test_duplicates_do_not_count_towards_limit(env):
    items = issues(4)
    env.fake.repos["stub/alpha"] = [items[0], items[1], items[1], items[2], items[3]]
    repo = report_of(env.run("stub/alpha", "--limit", "3"))["repos"][0]
    assert numbers(repo) == [1, 2, 3]


def test_since_keeps_issues_created_on_or_after_the_date(env):
    env.fake.page_size = 100
    env.fake.repos["stub/alpha"] = [
        make_issue(1, "2026-01-02T23:59:59Z"),
        make_issue(2, "2026-01-03T00:00:00Z"),               # exactly at the border: kept
        make_issue(3, "2026-01-03T01:00:00+03:00"),          # = 2026-01-02T22:00:00Z: dropped
        make_issue(4, "2026-01-05T12:00:00Z"),
    ]
    report = report_of(env.run("stub/alpha", "--since", "2026-01-03"))
    repo = report["repos"][0]
    assert numbers(repo) == [2, 4]
    assert "since" not in env.fake.requests[0]["query"]
    assert repo["issues"][0]["created_at"] == "2026-01-03T00:00:00Z"


def test_since_far_in_the_future_gives_empty_result(env):
    env.fake.repos["stub/alpha"] = issues(5)
    repo = report_of(env.run("stub/alpha", "--since", "2030-01-01"))["repos"][0]
    assert repo["issues"] == [] and repo["count"] == 0 and repo["error"] is None


def test_since_and_limit_combine_over_pages(env):
    env.fake.repos["stub/alpha"] = issues(9)                 # created: Jan 1 .. Jan 9
    repo = report_of(env.run("stub/alpha", "--since", "2026-01-04", "--limit", "3"))["repos"][0]
    assert numbers(repo) == [4, 5, 6]
    check_issue_order(repo["issues"])


def test_unsorted_server_pages_still_give_sorted_report(env):
    env.fake.page_size = 100
    items = issues(5)
    env.fake.repos["stub/alpha"] = list(reversed(items))
    repo = report_of(env.run("stub/alpha"))["repos"][0]
    assert numbers(repo) == [1, 2, 3, 4, 5]


def test_redirect_is_followed(env):
    env.fake.repos["new/name"] = issues(2, repo="new/name")
    for status in (301, 302, 307):
        env.fake.requests.clear()
        env.fake.scripts["old/name"] = [
            Reply(status, {"Location": "/repos/new/name/issues?state=open&sort=created&direction=asc&per_page=100"})]
        proc = env.run("old/name", cache_dir=env.tmp / ("cache%d" % status))
        repo = report_of(proc)["repos"][0]
        assert repo["repo"] == "old/name" and repo["error"] is None, status
        assert numbers(repo) == [1, 2], status
