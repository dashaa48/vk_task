"""--trace format, token hygiene and command line validation."""
import json
import os

from conftest import TRACE_FIELDS, report_of, run_ghreport
from helpers import issues, parse_rfc3339
from mock_github import Reply


def test_trace_has_one_valid_record_per_attempt(env):
    env.fake.repos["stub/alpha"] = issues(5)
    env.fake.script("stub/alpha", Reply(500))
    report_of(env.run("stub/alpha", token="tok"))
    with open(str(env.trace_path), encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert len(lines) == len(env.fake.requests) == 4        # 500, then 3 pages
    records = [json.loads(line) for line in lines]
    for rec in records:
        assert set(rec) == TRACE_FIELDS
        parse_rfc3339(rec["ts"])
        assert rec["ts"].endswith("Z")
        assert rec["method"] == "GET"
        assert rec["url"].startswith(env.fake.url + "/repos/stub/alpha/issues")
        assert isinstance(rec["status"], int) and not isinstance(rec["status"], bool)
        assert isinstance(rec["attempt"], int) and rec["attempt"] >= 1
        assert isinstance(rec["from_cache"], bool)
        assert rec["error"] is None or isinstance(rec["error"], str)
    assert [r["status"] for r in records] == [500, 200, 200, 200]
    assert [r["attempt"] for r in records] == [1, 2, 1, 1]
    assert [r["url"] for r in records][1:] == [env.fake.url + r["raw"] for r in env.fake.requests[1:]]
    stamps = [r["ts"] for r in records]
    assert stamps == sorted(stamps)


def test_trace_never_contains_authorization_or_token(env):
    env.fake.repos["stub/alpha"] = issues(3)
    token = "ghp_VERY_SECRET_123"
    report_of(env.run("stub/alpha", token=token))
    with open(str(env.trace_path), encoding="utf-8") as f:
        text = f.read()
    assert token not in text
    assert "authorization" not in text.lower() and "bearer" not in text.lower()


def test_token_does_not_leak_anywhere(env):
    env.fake.repos["stub/ok"] = issues(2, repo="stub/ok")
    token = "ghp_VERY_SECRET_456"
    out = env.tmp / "out.json"
    proc = run_ghreport("stub/ok,stub/missing", env.cache_dir, "--out", str(out), "--trace", str(env.trace_path),
                        base=env.fake.url, token=token)
    assert proc.returncode == 1
    assert token not in proc.stdout and token not in proc.stderr
    for d, _, files in os.walk(str(env.tmp)):
        for name in files:
            with open(os.path.join(d, name), "rb") as f:
                assert token.encode() not in f.read(), name


def test_only_the_report_goes_to_stdout(env):
    env.fake.repos["stub/alpha"] = issues(2)
    proc = env.run("stub/alpha")
    assert proc.returncode == 0
    json.loads(proc.stdout)                                  # nothing but one JSON document
    assert proc.stdout.strip().startswith("{") and proc.stdout.strip().endswith("}")


def test_no_trace_file_without_the_flag(env):
    env.fake.repos["stub/alpha"] = issues(1)
    report_of(env.run("stub/alpha", trace=False))
    assert not env.trace_path.exists()


def bad_args(env, *args, repos="stub/alpha"):
    env.fake.repos["stub/alpha"] = issues(1)
    proc = run_ghreport(repos, env.cache_dir, *args, base=env.fake.url)
    assert proc.returncode == 2, (args, proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout == "", "stdout must stay empty on argument errors"
    assert proc.stderr.strip() != ""
    assert env.fake.requests == [], "no HTTP traffic on invalid arguments"


def test_invalid_limit(env):
    for value in ("0", "-1", "abc", "1.5", ""):
        bad_args(env, "--limit", value)


def test_invalid_since(env):
    for value in ("2026-13-01", "2026-02-30", "01.02.2026", "yesterday", "2026-1-1", ""):
        bad_args(env, "--since", value)


def test_invalid_repos(env):
    for value in ("alpha", "a/b/c", "/b", "a/", "a b/c", "a/b,", ",a/b"):
        bad_args(env, repos=value)


def test_missing_repos_and_unknown_flags(env):
    env.fake.repos["stub/alpha"] = issues(1)
    proc = run_ghreport("", env.cache_dir, base=env.fake.url)
    assert proc.returncode == 2 and proc.stdout == ""
    bad_args(env, "--no-such-flag")
    bad_args(env, "unexpected-positional")
