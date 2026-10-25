#!/bin/sh
# CLI tests for msgfmt. Usage: cli_test.sh /path/to/msgfmt
BIN="$1"
[ -x "$BIN" ] || { echo "usage: $0 <msgfmt binary>" >&2; exit 2; }
fail=0

check() { # name expected actual
    if [ "$2" != "$3" ]; then
        echo "FAIL $1" >&2; echo "  expected: $2" >&2; echo "  actual:   $3" >&2; fail=1
    fi
}

out=$(printf 'Привет, **мир**!' | "$BIN"); rc=$?
check "parse rc" 0 "$rc"
check "parse out" '{"text": "Привет, мир!", "ranges": [{"style": "bold", "offset": 8, "length": 3, "url": ""}]}' "$out"

out=$(printf '' | "$BIN"); rc=$?
check "empty rc" 0 "$rc"
check "empty out" '{"text": "", "ranges": []}' "$out"

out=$(printf '2 * 3' | "$BIN" escape); rc=$?
check "escape rc" 0 "$rc"
check "escape out" '2 \* 3' "$out"

out=$(printf 'a\377b' | "$BIN"); rc=$?
check "invalid parse rc" 2 "$rc"
check "invalid parse out" '{"error": "invalid_utf8"}' "$out"

out=$(printf 'a\300\200' | "$BIN" escape); rc=$?
check "invalid escape rc" 2 "$rc"
check "invalid escape out" '{"error": "invalid_utf8"}' "$out"

out=$(printf '_x_' | "$BIN" bogus 2>/dev/null); rc=$?
check "usage rc" 1 "$rc"
check "usage stdout empty" '' "$out"

# determinism
a=$(printf '**a** _b_ `c` [d](e)' | "$BIN"); b=$(printf '**a** _b_ `c` [d](e)' | "$BIN")
check "deterministic" "$a" "$b"

exit $fail
