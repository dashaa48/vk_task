"""Rate limits: Retry-After, X-RateLimit-Reset, exhaustion."""
import time

from conftest import report_of
from helpers import issues, numbers
from mock_github import Reply


def test_429_with_retry_after_waits_and_succeeds(env):
    env.fake.repos["stub/alpha"] = issues(2)
    env.fake.script("stub/alpha", Reply.json(429, {"message": "slow down"}, {"Retry-After": "2"}))
    report = report_of(env.run("stub/alpha"))
    assert report["repos"][0]["error"] is None and numbers(report["repos"][0]) == [1, 2]
    reqs = env.fake.requests_for("stub/alpha")
    assert reqs[1]["time"] - reqs[0]["time"] >= 1.9, "the pause must honour Retry-After"
    assert reqs[1]["time"] - reqs[0]["time"] < 20
    trace = env.trace()
    assert [(r["status"], r["attempt"]) for r in trace][:2] == [(429, 1), (200, 2)]


def test_403_secondary_limit_with_retry_after(env):
    env.fake.repos["stub/alpha"] = issues(1)
    env.fake.script("stub/alpha", Reply.json(403, {"message": "You have exceeded a secondary rate limit"},
                                             {"Retry-After": "1"}))
    report = report_of(env.run("stub/alpha"))
    assert report["repos"][0]["count"] == 1
    reqs = env.fake.requests_for("stub/alpha")
    assert reqs[1]["time"] - reqs[0]["time"] >= 0.9


def test_403_with_remaining_zero_waits_until_reset(env):
    env.fake.repos["stub/alpha"] = issues(1)
    reset = int(time.time()) + 3
    env.fake.script("stub/alpha", Reply.json(403, {"message": "API rate limit exceeded"},
                                             {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}))
    report = report_of(env.run("stub/alpha"))
    assert report["repos"][0]["error"] is None
    reqs = env.fake.requests_for("stub/alpha")
    assert reqs[1]["time"] >= reset - 1.0, "must not retry long before the reset time"
    assert reqs[1]["time"] - reqs[0]["time"] < 20


def test_plain_403_is_not_a_rate_limit(env):
    env.fake.repos["stub/ok"] = issues(1, repo="stub/ok")
    env.fake.script("stub/denied", Reply.json(403, {"message": "Repository access blocked"}))
    started = time.time()
    report = report_of(env.run("stub/denied,stub/ok"), code=1)
    assert report["repos"][0]["error"] == "request_failed"
    assert len(env.fake.requests_for("stub/denied")) == 1
    assert time.time() - started < 20, "a plain 403 must not trigger a long pause"


def test_exhausted_rate_limit_gives_rate_limited_and_continues(env):
    env.fake.repos["stub/ok"] = issues(2, repo="stub/ok")
    env.fake.script("stub/busy", *[Reply.json(429, {"message": "limit"}, {"Retry-After": "0"}) for _ in range(10)])
    report = report_of(env.run("stub/busy,stub/ok"), code=1)
    assert report["repos"][0] == {"repo": "stub/busy", "error": "rate_limited", "count": 0, "issues": []}
    assert report["repos"][1]["error"] is None and report["repos"][1]["count"] == 2
    assert 2 <= len(env.fake.requests_for("stub/busy")) <= 5, "retries must be bounded"


def test_rate_limit_on_second_page_keeps_earlier_pages_out_of_the_report(env):
    env.fake.repos["stub/alpha"] = issues(4)
    calls = {"n": 0}

    def limit_second_request(rec):
        calls["n"] += 1
        if calls["n"] == 2:
            return Reply.json(429, {"message": "limit"}, {"Retry-After": "1"})
        return None

    env.fake.script("stub/alpha", limit_second_request, limit_second_request, limit_second_request)
    report = report_of(env.run("stub/alpha"))
    assert numbers(report["repos"][0]) == [1, 2, 3, 4], "no item lost or duplicated across the pause"
