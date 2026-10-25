"""Exit codes, per-repository errors, retries and fatal errors."""
import json
import time

from conftest import report_of
from helpers import issues, numbers
from mock_github import Reply


def gaps(requests):
    times = [r["time"] for r in requests]
    return [b - a for a, b in zip(times, times[1:])]


def test_404_for_one_repository_gives_exit_1_and_error_field(env):
    env.fake.repos["stub/ok1"] = issues(3, repo="stub/ok1")
    env.fake.repos["stub/ok2"] = issues(1, repo="stub/ok2")
    proc = env.run("stub/ok1,stub/missing,stub/ok2")
    report = report_of(proc, code=1)
    r1, r2, r3 = report["repos"]
    assert [r["repo"] for r in report["repos"]] == ["stub/ok1", "stub/missing", "stub/ok2"]
    assert r1["error"] is None and r1["count"] == 3
    assert r2 == {"repo": "stub/missing", "error": "not_found", "count": 0, "issues": []}
    assert r3["error"] is None and r3["count"] == 1, "processing continues after a failed repository"
    assert len(env.fake.requests_for("stub/missing")) == 1, "404 is not retried"


def test_all_repositories_failing_is_fatal_exit_3(env):
    proc = env.run("stub/m1,stub/m2")
    assert proc.returncode == 3
    assert json.loads(proc.stdout) == {"error": "all_failed"}


def test_single_failing_repository_is_all_failed(env):
    proc = env.run("stub/missing")
    assert proc.returncode == 3 and json.loads(proc.stdout) == {"error": "all_failed"}


def test_401_is_fatal(env):
    env.fake.repos["stub/ok"] = issues(2, repo="stub/ok")
    env.fake.script("stub/first", Reply.json(401, {"message": "Bad credentials"}))
    proc = env.run("stub/first,stub/ok", token="bad-token")
    assert proc.returncode == 3
    assert json.loads(proc.stdout) == {"error": "unauthorized"}
    assert "bad-token" not in proc.stdout + proc.stderr
    assert env.fake.requests_for("stub/ok") == [], "the run stops at the first 401"


def test_401_on_a_later_repository_is_also_fatal(env):
    env.fake.repos["stub/ok"] = issues(2, repo="stub/ok")
    env.fake.script("stub/second", Reply.json(401, {"message": "Bad credentials"}))
    proc = env.run("stub/ok,stub/second")
    assert proc.returncode == 3 and json.loads(proc.stdout) == {"error": "unauthorized"}


def test_invalid_json_body(env):
    env.fake.repos["stub/ok"] = issues(1, repo="stub/ok")
    env.fake.script("stub/broken", Reply(200, {}, b'{"not": "closed"'))
    env.fake.script("stub/obj", Reply.json(200, {"message": "not a list"}))
    env.fake.script("stub/html", Reply(200, {"Content-Type": "text/html"}, b"<html>oops</html>"))
    report = report_of(env.run("stub/ok,stub/broken,stub/obj,stub/html"), code=1)
    assert [r["error"] for r in report["repos"]] == [None, "invalid_response", "invalid_response", "invalid_response"]
    assert all(r["issues"] == [] and r["count"] == 0 for r in report["repos"][1:])


def test_failure_on_a_later_page_marks_the_repository(env):
    env.fake.repos["stub/alpha"] = issues(6)
    env.fake.repos["stub/ok"] = issues(1, repo="stub/ok")
    # the first request is served normally (None), the second one returns garbage
    env.fake.script("stub/alpha", lambda rec: None, lambda rec: Reply(200, {}, b"[{"))
    report = report_of(env.run("stub/alpha,stub/ok"), code=1)
    assert report["repos"][0]["error"] == "invalid_response"
    assert report["repos"][0]["issues"] == [] and report["repos"][0]["count"] == 0
    assert report["repos"][1]["error"] is None


def test_server_error_is_retried_with_exponential_backoff(env):
    env.fake.repos["stub/alpha"] = issues(1)
    env.fake.script("stub/alpha", Reply(500), Reply(502), Reply(503))
    started = time.time()
    report = report_of(env.run("stub/alpha"))
    assert report["repos"][0]["error"] is None and report["repos"][0]["count"] == 1
    trace = env.trace()
    assert [(r["status"], r["attempt"]) for r in trace] == [(500, 1), (502, 2), (503, 3), (200, 4)]
    g = gaps(env.fake.requests_for("stub/alpha"))
    assert 0.8 <= g[0] < 2.0 and 1.8 <= g[1] < 3.5 and 3.8 <= g[2] < 6.0, g
    assert time.time() - started < 30


def test_persistent_server_error_gives_request_failed(env):
    env.fake.repos["stub/ok"] = issues(1, repo="stub/ok")
    env.fake.script("stub/down", Reply(500), Reply(500), Reply(500), Reply(500), Reply(500))
    report = report_of(env.run("stub/down,stub/ok"), code=1)
    assert report["repos"][0]["error"] == "request_failed"
    assert report["repos"][1]["error"] is None
    assert len(env.fake.requests_for("stub/down")) == 4, "one attempt + three retries"
    assert [r["attempt"] for r in env.trace() if "down" in r["url"]] == [1, 2, 3, 4]


def test_dropped_connection_is_retried(env):
    env.fake.repos["stub/alpha"] = issues(2)
    env.fake.script("stub/alpha", Reply.dropped())
    report = report_of(env.run("stub/alpha"))
    assert numbers(report["repos"][0]) == [1, 2]
    trace = env.trace()
    assert trace[0]["status"] == 0 and trace[0]["error"] not in (None, "")
    assert trace[1]["status"] == 200 and trace[1]["attempt"] == 2


def test_unreachable_server_gives_request_failed(env):
    env.fake.stop()
    started = time.time()
    proc = env.run("stub/alpha", timeout=60)
    assert proc.returncode == 3 and json.loads(proc.stdout) == {"error": "all_failed"}
    trace = env.trace()
    assert [r["attempt"] for r in trace] == [1, 2, 3, 4]
    assert all(r["status"] == 0 and r["error"] for r in trace)
    assert 6.5 <= time.time() - started < 40


def test_4xx_other_than_special_ones_is_not_retried(env):
    env.fake.repos["stub/ok"] = issues(1, repo="stub/ok")
    env.fake.script("stub/gone", Reply.json(410, {"message": "Gone"}))
    report = report_of(env.run("stub/ok,stub/gone"), code=1)
    assert report["repos"][1]["error"] == "request_failed"
    assert len(env.fake.requests_for("stub/gone")) == 1
