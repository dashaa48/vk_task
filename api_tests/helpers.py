import datetime as dt

from mock_github import make_issue

REPO_FIELDS = {"repo", "error", "count", "issues"}
ISSUE_FIELDS = {"number", "title", "author", "labels", "created_at", "url"}


def day(n, hour=0):
    """RFC3339 timestamp: 2026-01-01 plus n days."""
    t = dt.datetime(2026, 1, 1, hour, 0, 0) + dt.timedelta(days=n)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def issues(count, repo="stub/alpha", first=1):
    """`count` issues numbered from `first`, created one day apart (ascending)."""
    return [make_issue(first + i, day(i), repo=repo) for i in range(count)]


def numbers(repo_report):
    return [i["number"] for i in repo_report["issues"]]


def parse_rfc3339(text):
    assert text.endswith("Z") or "+" in text[10:] or "-" in text[10:], text
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def check_issue_order(issue_list):
    keys = [(parse_rfc3339(i["created_at"]), i["number"]) for i in issue_list]
    assert keys == sorted(keys), "issues must be sorted by created_at, then number"


def strip_volatile(report):
    copy = dict(report)
    copy.pop("generated_at", None)
    return copy
