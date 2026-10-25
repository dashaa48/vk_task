"""ETag cache: conditional requests, from_cache in the trace, persistence."""
import os

from conftest import read_trace, report_of
from helpers import day, issues, numbers, strip_volatile
from mock_github import Reply, make_issue


def test_second_run_uses_the_cache(env):
    env.fake.repos["stub/alpha"] = issues(5)                 # 3 pages
    first = report_of(env.run("stub/alpha"))
    trace1 = env.trace()
    assert [r["status"] for r in trace1] == [200, 200, 200]
    assert not any(r["from_cache"] for r in trace1)
    assert all("if-none-match" not in r["headers"] for r in env.fake.requests)

    env.fake.requests.clear()
    second = report_of(env.run("stub/alpha"))                # same cache dir, new process
    trace2 = env.trace()
    assert [r["status"] for r in trace2] == [304, 304, 304]
    assert all(r["from_cache"] is True for r in trace2)
    assert all(r["error"] is None for r in trace2)
    assert strip_volatile(second) == strip_volatile(first)

    sent = [r["headers"].get("if-none-match") for r in env.fake.requests]
    assert all(sent), "every repeated request must carry If-None-Match"
    assert len(env.fake.requests) == 3


def test_304_without_link_header_still_paginates(env):
    env.fake.link_on_304 = False
    env.fake.repos["stub/alpha"] = issues(5)
    first = report_of(env.run("stub/alpha"))
    second = report_of(env.run("stub/alpha"))
    assert numbers(second["repos"][0]) == [1, 2, 3, 4, 5]
    assert strip_volatile(first) == strip_volatile(second)


def test_304_with_link_header(env):
    env.fake.link_on_304 = True
    env.fake.repos["stub/alpha"] = issues(5)
    first = report_of(env.run("stub/alpha"))
    second = report_of(env.run("stub/alpha"))
    assert strip_volatile(first) == strip_volatile(second)
    assert all(r["from_cache"] for r in env.trace())


def test_changed_data_replaces_the_cache(env):
    env.fake.repos["stub/alpha"] = issues(3)
    report_of(env.run("stub/alpha"))
    env.fake.repos["stub/alpha"] = issues(3) + [make_issue(4, day(3))]
    report = report_of(env.run("stub/alpha"))
    assert numbers(report["repos"][0]) == [1, 2, 3, 4]
    # page 1 is unchanged (304), page 2 changed (fresh 200)
    assert [(r["status"], r["from_cache"]) for r in env.trace()] == [(304, True), (200, False)]
    third = report_of(env.run("stub/alpha"))
    assert strip_volatile(third) == strip_volatile(report)
    assert all(r["from_cache"] for r in env.trace())


def test_each_cache_dir_is_independent(env):
    env.fake.repos["stub/alpha"] = issues(3)
    report_of(env.run("stub/alpha", cache_dir=env.tmp / "one"))
    report_of(env.run("stub/alpha", cache_dir=env.tmp / "two"))
    assert not any(r["from_cache"] for r in env.trace())


def test_cache_dir_is_created_and_holds_no_token(env):
    env.fake.repos["stub/alpha"] = issues(3)
    token = "ghp_SECRET_TOKEN_VALUE"
    nested = env.tmp / "does" / "not" / "exist"
    report_of(env.run("stub/alpha", token=token, cache_dir=nested))
    assert nested.is_dir()
    files = [os.path.join(d, f) for d, _, fs in os.walk(str(nested)) for f in fs]
    assert files, "a cache must be written"
    for path in files:
        with open(path, "rb") as f:
            assert token.encode() not in f.read(), path


def test_with_token_cache_still_works(env):
    env.fake.repos["stub/alpha"] = issues(3)
    first = report_of(env.run("stub/alpha", token="t"))
    second = report_of(env.run("stub/alpha", token="t"))
    assert strip_volatile(first) == strip_volatile(second)
    assert all(r["from_cache"] for r in env.trace())


def test_damaged_cache_is_not_fatal(env):
    env.fake.repos["stub/alpha"] = issues(3)
    first = report_of(env.run("stub/alpha"))
    for d, _, fs in os.walk(str(env.cache_dir)):
        for f in fs:
            with open(os.path.join(d, f), "wb") as fh:
                fh.write(b"\x00garbage{")
    second = report_of(env.run("stub/alpha"))
    assert strip_volatile(first) == strip_volatile(second)


def test_304_without_if_none_match_is_an_error(env):
    """A 304 nobody asked for must not be turned into a successful report."""
    env.fake.repos["stub/good"] = issues(2, repo="stub/good")
    env.fake.script("stub/bad", Reply(304, {"ETag": '"x"'}))
    proc = env.run("stub/good,stub/bad")
    report = report_of(proc, code=1)
    good, bad = report["repos"]
    assert good["error"] is None and good["count"] == 2
    assert bad["error"] is not None and bad["count"] == 0 and bad["issues"] == []
    assert not any(r["from_cache"] for r in env.trace() if "bad" in r["url"])


def test_corrupted_json_is_not_cached(env):
    env.fake.repos["stub/good"] = issues(2, repo="stub/good")
    env.fake.script("stub/bad", Reply(200, {"ETag": '"bad1"'}, b'[{"number": 1,'))
    report = report_of(env.run("stub/good,stub/bad"), code=1)
    assert report["repos"][1]["error"] == "invalid_response"
    env.fake.repos["stub/bad"] = issues(1, repo="stub/bad")
    env.fake.requests.clear()
    report = report_of(env.run("stub/good,stub/bad"))        # now healthy
    assert report["repos"][1]["error"] is None and report["repos"][1]["count"] == 1
    assert "if-none-match" not in env.fake.requests_for("stub/bad")[0]["headers"]
