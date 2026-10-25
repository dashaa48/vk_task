package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func issueJSON(n int, created string, pr bool) string {
	extra := ""
	if pr {
		extra = `,"pull_request":{"url":"x"}`
	}
	return fmt.Sprintf(`{"number":%d,"title":"t%d","user":{"login":"u"},"labels":[{"name":"bug"}],"created_at":%q,"html_url":"https://github.com/o/r/issues/%d"%s}`,
		n, n, created, n, extra)
}

func numbers(issues []Issue) []int {
	out := []int{}
	for _, i := range issues {
		out = append(out, i.Number)
	}
	return out
}

func testClient(t *testing.T, baseURL string, cache *Cache) (*Client, *[]time.Duration) {
	t.Helper()
	c, err := newClient(baseURL, "", cache, nil)
	if err != nil {
		t.Fatal(err)
	}
	slept := []time.Duration{}
	c.sleep = func(d time.Duration) { slept = append(slept, d) }
	return c, &slept
}

func readTrace(t *testing.T, path string) []traceRecord {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var recs []traceRecord
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		var r traceRecord
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatalf("bad trace line %q: %v", line, err)
		}
		recs = append(recs, r)
	}
	return recs
}

func TestParseArgs(t *testing.T) {
	cfg, err := parseArgs([]string{"--repos", "a/b,c/d,a/b", "--limit", "5", "--since", "2026-01-02", "--out", "x.json"})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cfg.Repos, []string{"a/b", "c/d"}) || cfg.Limit != 5 || !cfg.HasSince || cfg.Out != "x.json" {
		t.Fatalf("unexpected config: %+v", cfg)
	}
	if !cfg.Since.Equal(time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("since = %v", cfg.Since)
	}
	def, err := parseArgs([]string{"--repos", "a/b"})
	if err != nil {
		t.Fatal(err)
	}
	if def.Limit != 100 || def.Out != "-" || def.CacheDir != "./.ghreport-cache" || def.HasSince {
		t.Fatalf("unexpected defaults: %+v", def)
	}
	bad := [][]string{
		{},
		{"--repos", ""},
		{"--repos", "abc"},
		{"--repos", "a/b/c"},
		{"--repos", "a/b,"},
		{"--repos", "a/b", "--limit", "0"},
		{"--repos", "a/b", "--limit", "x"},
		{"--repos", "a/b", "--since", "2026-13-01"},
		{"--repos", "a/b", "--since", "01.01.2026"},
		{"--repos", "a/b", "extra"},
		{"--repos", "a/b", "--nope"},
	}
	for _, args := range bad {
		if _, err := parseArgs(args); err == nil {
			t.Errorf("parseArgs(%q) must fail", args)
		}
	}
}

func TestNextLink(t *testing.T) {
	base := "https://api.example.com/repos/o/r/issues?per_page=100"
	cases := []struct {
		headers []string
		want    string
	}{
		{[]string{`<https://api.example.com/x?page=2>; rel="next", <https://api.example.com/x?page=7>; rel="last"`}, "https://api.example.com/x?page=2"},
		{[]string{`<https://api.example.com/x?page=1>; rel="prev", <https://api.example.com/x?page=3>; rel="next"`}, "https://api.example.com/x?page=3"},
		{[]string{`<https://api.example.com/x?page=7>; rel="last"`}, ""},
		{[]string{`<?page=2>; rel=next`}, "https://api.example.com/repos/o/r/issues?page=2"},
		{[]string{`<https://a/p1>; rel="prev"`, `<https://a/p3>; rel="next"`}, "https://a/p3"},
		{nil, ""},
	}
	for _, c := range cases {
		if got := nextLink(c.headers, base); got != c.want {
			t.Errorf("nextLink(%q) = %q, want %q", c.headers, got, c.want)
		}
	}
}

func TestPaginationLimitDedupAndPullRequests(t *testing.T) {
	var requests []string
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests = append(requests, r.URL.RequestURI())
		switch r.URL.Query().Get("page") {
		case "":
			w.Header().Set("Link", fmt.Sprintf(`<%s/repos/o/r/issues?page=2>; rel="next", <%s/repos/o/r/issues?page=3>; rel="last"`, srv.URL, srv.URL))
			fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+","+issueJSON(3, "2026-01-03T00:00:00Z", true)+","+issueJSON(2, "2026-01-02T00:00:00Z", false)+"]")
		case "2":
			w.Header().Set("Link", fmt.Sprintf(`<%s/repos/o/r/issues?page=3>; rel="next"`, srv.URL))
			fmt.Fprint(w, "["+issueJSON(2, "2026-01-02T00:00:00Z", false)+","+issueJSON(4, "2026-01-04T00:00:00Z", false)+"]")
		default:
			fmt.Fprint(w, "["+issueJSON(5, "2026-01-05T00:00:00Z", false)+","+issueJSON(6, "2026-01-06T00:00:00Z", false)+"]")
		}
	}))
	defer srv.Close()

	c, _ := testClient(t, srv.URL, nil)

	issues, err := c.collectIssues("o/r", &Config{Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	if got := numbers(issues); !reflect.DeepEqual(got, []int{1, 2, 4, 5, 6}) {
		t.Fatalf("numbers = %v", got)
	}
	if !strings.HasPrefix(requests[0], "/repos/o/r/issues?state=open&sort=created&direction=asc&per_page=100") {
		t.Fatalf("first request: %s", requests[0])
	}

	requests = nil
	issues, err = c.collectIssues("o/r", &Config{Limit: 4})
	if err != nil {
		t.Fatal(err)
	}
	if got := numbers(issues); !reflect.DeepEqual(got, []int{1, 2, 4, 5}) || len(requests) != 3 {
		t.Fatalf("limit 4: numbers = %v, requests = %d", got, len(requests))
	}

	requests = nil
	issues, err = c.collectIssues("o/r", &Config{Limit: 3})
	if err != nil {
		t.Fatal(err)
	}
	if got := numbers(issues); !reflect.DeepEqual(got, []int{1, 2, 4}) || len(requests) != 2 {
		t.Fatalf("limit 3: numbers = %v, requests = %d (page 3 must not be fetched)", got, len(requests))
	}
}

func TestSinceAndSorting(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 9 and 8 are before the border (8 is 2026-01-02T22:00Z); 5 and 6 sit exactly on it.
		items := []string{
			issueJSON(9, "2026-01-02T23:59:59Z", false),
			issueJSON(8, "2026-01-03T01:00:00+03:00", false),
			issueJSON(7, "2026-01-04T00:00:00Z", false),
			issueJSON(5, "2026-01-03T00:00:00Z", false),
			issueJSON(6, "2026-01-03T00:00:00Z", false),
		}
		fmt.Fprint(w, "["+strings.Join(items, ",")+"]")
	}))
	defer srv.Close()
	c, _ := testClient(t, srv.URL, nil)
	cfg := &Config{Limit: 100, HasSince: true, Since: time.Date(2026, 1, 3, 0, 0, 0, 0, time.UTC)}
	issues, err := c.collectIssues("o/r", cfg)
	if err != nil {
		t.Fatal(err)
	}
	if got := numbers(issues); !reflect.DeepEqual(got, []int{5, 6, 7}) {
		t.Fatalf("numbers = %v, want [5 6 7]", got)
	}
	if issues[0].CreatedAt != "2026-01-03T00:00:00Z" || issues[0].Author != "u" ||
		!reflect.DeepEqual(issues[0].Labels, []string{"bug"}) || issues[0].URL != "https://github.com/o/r/issues/5" {
		t.Fatalf("field mapping: %+v", issues[0])
	}
}

func TestConditionalRequestsAndTrace(t *testing.T) {
	body := "[" + issueJSON(1, "2026-01-01T00:00:00Z", false) + "]"
	var inm []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		inm = append(inm, r.Header.Get("If-None-Match"))
		if r.Header.Get("If-None-Match") == `"v1"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Header().Set("ETag", `"v1"`)
		fmt.Fprint(w, body)
	}))
	defer srv.Close()

	dir := t.TempDir()
	runOnce := func(name string) ([]Issue, []traceRecord) {
		cache, err := newCache(filepath.Join(dir, "cache"))
		if err != nil {
			t.Fatal(err)
		}
		tr, err := newTracer(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		c, err := newClient(srv.URL, "", cache, tr)
		if err != nil {
			t.Fatal(err)
		}
		issues, err := c.collectIssues("o/r", &Config{Limit: 10})
		if err != nil {
			t.Fatal(err)
		}
		tr.Close()
		return issues, readTrace(t, filepath.Join(dir, name))
	}

	first, tr1 := runOnce("t1.jsonl")
	second, tr2 := runOnce("t2.jsonl")

	if !reflect.DeepEqual(first, second) || len(first) != 1 {
		t.Fatalf("reports differ: %v vs %v", first, second)
	}
	if !reflect.DeepEqual(inm, []string{"", `"v1"`}) {
		t.Fatalf("If-None-Match sequence: %q", inm)
	}
	if len(tr1) != 1 || tr1[0].Status != 200 || tr1[0].FromCache || tr1[0].Attempt != 1 || tr1[0].Error != nil || tr1[0].Method != "GET" {
		t.Fatalf("first trace: %+v", tr1)
	}
	if len(tr2) != 1 || tr2[0].Status != 304 || !tr2[0].FromCache {
		t.Fatalf("second trace: %+v", tr2)
	}
}

func TestNotModifiedWithoutCacheIsInvalid(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotModified)
	}))
	defer srv.Close()
	c, _ := testClient(t, srv.URL, nil)
	_, err := c.collectIssues("o/r", &Config{Limit: 10})
	fe, ok := err.(*fetchError)
	if !ok || fe.Kind != kindInvalidResponse {
		t.Fatalf("err = %v", err)
	}
}

func TestNotModifiedKeepsPagination(t *testing.T) {
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		page := r.URL.Query().Get("page")
		etag := `"p` + page + `"`
		if r.Header.Get("If-None-Match") == etag {
			w.WriteHeader(http.StatusNotModified) // no Link header on 304
			return
		}
		w.Header().Set("ETag", etag)
		if page == "" {
			w.Header().Set("Link", fmt.Sprintf(`<%s/repos/o/r/issues?page=2>; rel="next"`, srv.URL))
			fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+"]")
			return
		}
		fmt.Fprint(w, "["+issueJSON(2, "2026-01-02T00:00:00Z", false)+"]")
	}))
	defer srv.Close()
	cache, err := newCache(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		c, _ := testClient(t, srv.URL, cache)
		issues, err := c.collectIssues("o/r", &Config{Limit: 10})
		if err != nil {
			t.Fatal(err)
		}
		if got := numbers(issues); !reflect.DeepEqual(got, []int{1, 2}) {
			t.Fatalf("run %d: numbers = %v", i, got)
		}
	}
}

func TestRateLimitRetryAfter(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			w.Header().Set("Retry-After", "7")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+"]")
	}))
	defer srv.Close()
	c, slept := testClient(t, srv.URL, nil)
	issues, err := c.collectIssues("o/r", &Config{Limit: 10})
	if err != nil || len(issues) != 1 {
		t.Fatalf("issues = %v, err = %v", issues, err)
	}
	if !reflect.DeepEqual(*slept, []time.Duration{7 * time.Second}) {
		t.Fatalf("slept = %v", *slept)
	}
}

func TestRateLimitWaitSelection(t *testing.T) {
	c, err := newClient("http://x", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	c.now = func() time.Time { return time.Unix(1000, 0) }

	h := http.Header{}
	h.Set("Retry-After", "5")
	h.Set("X-RateLimit-Reset", "1030")
	if d := c.rateLimitWait(&response{status: 429, header: h}); d != 5*time.Second {
		t.Errorf("retry-after wins: %v", d)
	}
	h = http.Header{}
	h.Set("X-RateLimit-Remaining", "0")
	h.Set("X-RateLimit-Reset", "1030")
	if d := c.rateLimitWait(&response{status: 403, header: h}); d != 30*time.Second {
		t.Errorf("reset: %v", d)
	}
	if d := c.rateLimitWait(&response{status: 403, header: http.Header{}}); d != 60*time.Second {
		t.Errorf("default: %v", d)
	}

	if !isRateLimited(&response{status: 403, header: http.Header{}, body: []byte(`{"message":"API rate limit exceeded"}`)}) {
		t.Error("403 with rate limit message must be rate limited")
	}
	if isRateLimited(&response{status: 403, header: http.Header{}, body: []byte(`{"message":"Forbidden"}`)}) {
		t.Error("plain 403 must not be rate limited")
	}
	rem := http.Header{}
	rem.Set("X-RateLimit-Remaining", "0")
	if !isRateLimited(&response{status: 403, header: rem}) {
		t.Error("403 with remaining=0 must be rate limited")
	}
}

func TestServerErrorsAreRetriedWithBackoff(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	c, slept := testClient(t, srv.URL, nil)
	_, err := c.collectIssues("o/r", &Config{Limit: 10})
	fe, ok := err.(*fetchError)
	if !ok || fe.Kind != kindRequestFailed {
		t.Fatalf("err = %v", err)
	}
	if calls != 4 {
		t.Fatalf("calls = %d, want 4", calls)
	}
	want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second}
	if !reflect.DeepEqual(*slept, want) {
		t.Fatalf("slept = %v, want %v", *slept, want)
	}
}

func TestInvalidJSON(t *testing.T) {
	for _, body := range []string{`{"oops":`, `{"a":1}`, `null`, `[1]`} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("ETag", `"x"`)
			fmt.Fprint(w, body)
		}))
		cache, err := newCache(t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		c, _ := testClient(t, srv.URL, cache)
		_, err = c.collectIssues("o/r", &Config{Limit: 10})
		fe, ok := err.(*fetchError)
		if !ok || fe.Kind != kindInvalidResponse {
			t.Errorf("body %q: err = %v", body, err)
		}
		if cache.Load(srv.URL+"/repos/o/r/issues?state=open&sort=created&direction=asc&per_page=100") != nil {
			t.Errorf("body %q: an invalid response must not be cached", body)
		}
		srv.Close()
	}
}

func TestRedirectIsFollowedAndTraced(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/repos/old/") {
			http.Redirect(w, r, "/repos/o/r/issues?state=open", http.StatusMovedPermanently)
			return
		}
		fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+"]")
	}))
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	tr, err := newTracer(path)
	if err != nil {
		t.Fatal(err)
	}
	c, err := newClient(srv.URL, "", nil, tr)
	if err != nil {
		t.Fatal(err)
	}
	issues, err := c.collectIssues("old/name", &Config{Limit: 10})
	tr.Close()
	if err != nil || len(issues) != 1 {
		t.Fatalf("issues = %v, err = %v", issues, err)
	}
	recs := readTrace(t, path)
	if len(recs) != 2 || recs[0].Status != 301 || recs[1].Status != 200 || recs[1].Attempt != 1 {
		t.Fatalf("trace: %+v", recs)
	}
}

func envOf(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestRunUnauthorized(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()
	var out, errOut bytes.Buffer
	code := run([]string{"--repos", "o/r", "--cache-dir", t.TempDir()}, envOf(map[string]string{"GITHUB_API_BASE_URL": srv.URL + "/"}), &out, &errOut)
	if code != 3 || out.String() != "{\"error\": \"unauthorized\"}\n" {
		t.Fatalf("code = %d, stdout = %q", code, out.String())
	}
}

func TestRunAllFailedAndPartial(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/missing") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+"]")
	}))
	defer srv.Close()
	env := envOf(map[string]string{"GITHUB_API_BASE_URL": srv.URL})

	var out, errOut bytes.Buffer
	code := run([]string{"--repos", "o/missing", "--cache-dir", t.TempDir()}, env, &out, &errOut)
	if code != 3 || out.String() != "{\"error\": \"all_failed\"}\n" {
		t.Fatalf("all failed: code = %d, stdout = %q", code, out.String())
	}

	out.Reset()
	errOut.Reset()
	code = run([]string{"--repos", "o/ok,o/missing", "--cache-dir", t.TempDir()}, env, &out, &errOut)
	if code != 1 {
		t.Fatalf("partial: code = %d, stderr = %s", code, errOut.String())
	}
	var rep Report
	if err := json.Unmarshal(out.Bytes(), &rep); err != nil {
		t.Fatalf("stdout is not a report: %v\n%s", err, out.String())
	}
	if len(rep.Repos) != 2 || rep.Repos[0].Repo != "o/ok" || rep.Repos[0].Error != nil || rep.Repos[0].Count != 1 {
		t.Fatalf("repos: %+v", rep.Repos)
	}
	if rep.Repos[1].Repo != "o/missing" || rep.Repos[1].Error == nil || *rep.Repos[1].Error != "not_found" || rep.Repos[1].Count != 0 {
		t.Fatalf("repos: %+v", rep.Repos)
	}
	if !strings.Contains(out.String(), `"issues": []`) || !strings.Contains(out.String(), `"error": null`) {
		t.Fatalf("empty array / null expected:\n%s", out.String())
	}
	if _, err := time.Parse(time.RFC3339, rep.GeneratedAt); err != nil {
		t.Fatalf("generated_at: %v", err)
	}
}

func TestRunBadArgs(t *testing.T) {
	var out, errOut bytes.Buffer
	code := run([]string{"--repos", "o/r", "--limit", "0"}, envOf(nil), &out, &errOut)
	if code != 2 || out.Len() != 0 || !strings.Contains(errOut.String(), "--limit") {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, out.String(), errOut.String())
	}
}

func TestTokenIsSentButNeverLeaked(t *testing.T) {
	const token = "s3cr3t-token-value"
	var auth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		if r.Header.Get("Accept") != "application/vnd.github+json" || r.Header.Get("X-GitHub-Api-Version") != apiVersion || r.Header.Get("User-Agent") == "" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("ETag", `"e"`)
		fmt.Fprint(w, "["+issueJSON(1, "2026-01-01T00:00:00Z", false)+"]")
	}))
	defer srv.Close()

	dir := t.TempDir()
	tracePath := filepath.Join(dir, "trace.jsonl")
	var out, errOut bytes.Buffer
	env := envOf(map[string]string{"GITHUB_API_BASE_URL": srv.URL, "GITHUB_TOKEN": token})
	code := run([]string{"--repos", "o/r", "--cache-dir", filepath.Join(dir, "cache"), "--trace", tracePath, "--out", filepath.Join(dir, "report.json")}, env, &out, &errOut)
	if code != 0 {
		t.Fatalf("code = %d, stderr = %s", code, errOut.String())
	}
	if auth != "Bearer "+token {
		t.Fatalf("Authorization = %q", auth)
	}
	if out.Len() != 0 {
		t.Fatalf("stdout must be empty with --out <file>, got %q", out.String())
	}
	if strings.Contains(out.String(), token) || strings.Contains(errOut.String(), token) {
		t.Fatal("token leaked to stdout/stderr")
	}
	entries, err := os.ReadDir(dir)
	if err != nil || len(entries) == 0 {
		t.Fatal("expected output files")
	}
	err = filepath.Walk(dir, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		data, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		if bytes.Contains(data, []byte(token)) {
			t.Errorf("token found in %s", p)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ToLower(string(mustRead(t, tracePath))), "authorization") {
		t.Error("trace must not mention the Authorization header")
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestNoTokenMeansNoAuthorizationHeader(t *testing.T) {
	var got []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = append(got, r.Header.Get("Authorization"))
		fmt.Fprint(w, "[]")
	}))
	defer srv.Close()
	c, _ := testClient(t, srv.URL, nil)
	if _, err := c.collectIssues("o/r", &Config{Limit: 1}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{""}) {
		t.Fatalf("Authorization headers: %q", got)
	}
}
