#!/usr/bin/env python3
"""Mutation check: the suite must pass on the reference service and FAIL on every
deliberately broken variant. Usage: python selfcheck/run_mutants.py [pytest command...]"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
REF = os.path.join(HERE, "ghreport_ref.py")
MUTANTS = {
    "keep_pr": "pull requests are not filtered out",
    "limit_plus_one": "limit + 1 items are returned",
    "no_early_stop": "pages are fetched after the limit is reached",
    "no_dedup": "duplicates across pages are kept",
    "since_inclusive": "--since border is excluded (<= instead of <)",
    "no_sort": "report keeps server order",
    "reverse_sort": "descending sort",
    "tie_by_title": "no tie-break by number",
    "manual_pages": "page URLs are constructed instead of taken from Link",
    "no_etag": "If-None-Match is never sent",
    "bogus_inm": "If-None-Match is sent without a cached ETag",
    "cache_not_reported": "from_cache is always false",
    "token_in_cache": "token is written to the cache",
    "trace_auth": "Authorization is written to the trace",
    "no_retry_after": "Retry-After is ignored",
    "no_backoff": "no exponential backoff for 5xx",
    "no_retry_5xx": "5xx is not retried",
    "401_partial": "401 is a per-repo error instead of fatal",
    "404_fatal": "404 is fatal instead of a per-repo error",
}


def run(mutant, pytest_cmd):
    env = dict(os.environ, GHREPORT_BIN=REF, GHREPORT_REF_MUTANT=mutant, FAILFAST="1")
    cmd = pytest_cmd + [TESTS] if "-x" not in pytest_cmd else pytest_cmd + [TESTS]
    return subprocess.run(cmd, env=env, capture_output=True, text=True).returncode


def main():
    pytest_cmd = sys.argv[1:] or [sys.executable, "-m", "pytest", "-x", "-q"]
    bad = []
    base = run("", pytest_cmd)
    print("%-20s %s" % ("(no mutation)", "pass" if base == 0 else "FAIL <- suite must pass"))
    if base != 0:
        sys.exit(1)
    for name, why in MUTANTS.items():
        killed = run(name, pytest_cmd) != 0
        print("%-20s %-8s %s" % (name, "killed" if killed else "SURVIVED", why))
        if not killed:
            bad.append(name)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
