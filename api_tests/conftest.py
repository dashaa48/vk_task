"""Shared helpers: how to start ghreport and read its artefacts.

Environment:
  GHREPORT_BIN          path to the ghreport executable (required)
  GITHUB_API_BASE_URL   optional; a real/stub server for test_live_server.py
  GHREPORT_LIVE_REPOS   repos (comma separated) on that server, default stub/alpha
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_github import FakeGitHub  # noqa: E402

TRACE_FIELDS = {"ts", "method", "url", "status", "attempt", "from_cache", "error"}


def ghreport_bin():
    path = os.environ.get("GHREPORT_BIN")
    if not path:
        pytest.fail("GHREPORT_BIN is not set (path to the ghreport executable)")
    return path


def run_ghreport(repos, cache_dir, *args, base, token=None, timeout=120, extra_env=None):
    """Runs the service as a black box and returns the CompletedProcess."""
    cmd = [ghreport_bin(), "--repos", repos, "--cache-dir", str(cache_dir), *args]
    if "--out" not in args:
        cmd += ["--out", "-"]
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITHUB_API_BASE_URL")}
    env["GITHUB_API_BASE_URL"] = base
    if token is not None:
        env["GITHUB_TOKEN"] = token
    env.update(extra_env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def report_of(proc, code=0):
    assert proc.returncode == code, "exit %s, stderr: %s" % (proc.returncode, proc.stderr)
    return json.loads(proc.stdout)


def read_trace(path):
    with open(str(path), encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines() if line.strip()]


class Env:
    """One test's world: a fake GitHub, a cache dir and a trace file."""

    def __init__(self, fake, tmp_path):
        self.fake = fake
        self.tmp = tmp_path
        self.cache_dir = tmp_path / "cache"
        self.trace_path = tmp_path / "trace.jsonl"

    def run(self, repos, *args, token=None, trace=True, cache_dir=None, timeout=120):
        args = list(args)
        if trace:
            args += ["--trace", str(self.trace_path)]
        return run_ghreport(repos, cache_dir or self.cache_dir, *args,
                            base=self.fake.url, token=token, timeout=timeout)

    def trace(self):
        return read_trace(self.trace_path)


@pytest.fixture
def env(tmp_path):
    fake = FakeGitHub()
    fake.start()
    try:
        yield Env(fake, tmp_path)
    finally:
        fake.stop()
