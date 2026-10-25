package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	apiVersion       = "2026-03-10"
	userAgent        = "ghreport/1.0 (+https://git.miem.hse.ru/; GitHub open issues summary tool)"
	requestTimeout   = 30 * time.Second
	maxAttempts      = 4 // the first try + up to three retries
	maxRedirects     = 5
	minRateLimitWait = 60 * time.Second
	maxWait          = 24 * time.Hour
)

// Repository error codes (report field "error").
const (
	kindNotFound        = "not_found"
	kindRateLimited     = "rate_limited"
	kindRequestFailed   = "request_failed"
	kindInvalidResponse = "invalid_response"
)

// fetchError is a per-repository failure.
type fetchError struct {
	Kind   string
	Detail string
}

func (e *fetchError) Error() string { return e.Kind + ": " + e.Detail }

var (
	// errUnauthorized is fatal: the whole run stops with exit code 3.
	errUnauthorized = errors.New("unauthorized")
	// errPermanent marks request errors that must not be retried.
	errPermanent = errors.New("permanent request error")
)

// Client performs sequential GET requests with retries, rate limit
// handling, conditional requests and tracing.
type Client struct {
	baseURL  string
	baseHost string
	token    string
	http     *http.Client
	cache    *Cache
	trace    *Tracer
	sleep    func(time.Duration)
	now      func() time.Time
	logf     func(format string, args ...any)
}

func newClient(baseURL, token string, cache *Cache, trace *Tracer) (*Client, error) {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	u, err := url.Parse(baseURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("некорректный GITHUB_API_BASE_URL %q", baseURL)
	}
	return &Client{
		baseURL:  baseURL,
		baseHost: u.Host,
		token:    token,
		http: &http.Client{
			Timeout: requestTimeout,
			// Redirects are followed by hand so that every hop is traced
			// and the token is never sent to another host.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		cache: cache,
		trace: trace,
		sleep: time.Sleep,
		now:   time.Now,
		logf:  func(string, ...any) {},
	}, nil
}

type response struct {
	status   int
	header   http.Header
	body     []byte
	finalURL string
}

type page struct {
	items     []parsedItem
	next      string
	fromCache bool
}

func classify(err error) string {
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return "timeout"
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "timeout"
	}
	return "network"
}

func (c *Client) logTrace(rawURL string, status, attempt int, fromCache bool, errText string) {
	rec := traceRecord{
		TS:        c.now().UTC().Format(time.RFC3339),
		Method:    http.MethodGet,
		URL:       rawURL,
		Status:    status,
		Attempt:   attempt,
		FromCache: fromCache,
	}
	if errText != "" {
		rec.Error = &errText
	}
	c.trace.Log(rec)
}

// roundTrip performs one attempt (following 301/302/307/308 redirects; every
// hop is a separate trace record carrying the same attempt number).
func (c *Client) roundTrip(rawURL, etag string, attempt int) (*response, error) {
	cur := rawURL
	for hop := 0; hop <= maxRedirects; hop++ {
		req, err := http.NewRequest(http.MethodGet, cur, nil)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", errPermanent, err)
		}
		req.Header.Set("Accept", "application/vnd.github+json")
		req.Header.Set("User-Agent", userAgent)
		req.Header.Set("X-GitHub-Api-Version", apiVersion)
		if c.token != "" && strings.EqualFold(req.URL.Host, c.baseHost) {
			req.Header.Set("Authorization", "Bearer "+c.token)
		}
		if etag != "" {
			req.Header.Set("If-None-Match", etag)
		}

		resp, err := c.http.Do(req)
		if err != nil {
			c.logTrace(cur, 0, attempt, false, classify(err))
			return nil, err
		}
		body, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		if readErr != nil {
			c.logTrace(cur, resp.StatusCode, attempt, false, classify(readErr))
			return nil, readErr
		}
		fromCache := resp.StatusCode == http.StatusNotModified && etag != ""
		c.logTrace(cur, resp.StatusCode, attempt, fromCache, "")

		switch resp.StatusCode {
		case http.StatusMovedPermanently, http.StatusFound,
			http.StatusTemporaryRedirect, http.StatusPermanentRedirect:
			loc := resp.Header.Get("Location")
			if loc != "" {
				next, err := resolveURL(cur, loc)
				if err != nil {
					return nil, fmt.Errorf("%w: %v", errPermanent, err)
				}
				cur = next
				continue
			}
		}
		return &response{status: resp.StatusCode, header: resp.Header, body: body, finalURL: cur}, nil
	}
	return nil, fmt.Errorf("%w: слишком много перенаправлений", errPermanent)
}

func resolveURL(base, ref string) (string, error) {
	b, err := url.Parse(base)
	if err != nil {
		return "", err
	}
	r, err := url.Parse(strings.TrimSpace(ref))
	if err != nil {
		return "", err
	}
	return b.ResolveReference(r).String(), nil
}

// fetchPage fetches one page of the issue list with retries.
func (c *Client) fetchPage(pageURL string) (*page, error) {
	entry := c.cache.Load(pageURL)
	etag := ""
	if entry != nil {
		etag = entry.ETag
	}

	lastKind, lastDetail := kindRequestFailed, "нет ответа"
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		resp, err := c.roundTrip(pageURL, etag, attempt)
		var wait time.Duration
		switch {
		case err != nil:
			if errors.Is(err, errPermanent) {
				return nil, &fetchError{Kind: kindRequestFailed, Detail: err.Error()}
			}
			lastKind, lastDetail = kindRequestFailed, err.Error()
			wait = backoff(attempt)
		case resp.status == http.StatusOK:
			return c.handleOK(pageURL, resp)
		case resp.status == http.StatusNotModified:
			return c.handleNotModified(entry, resp)
		case resp.status == http.StatusUnauthorized:
			return nil, errUnauthorized
		case resp.status == http.StatusNotFound:
			return nil, &fetchError{Kind: kindNotFound, Detail: "HTTP 404"}
		case isRateLimited(resp):
			lastKind, lastDetail = kindRateLimited, fmt.Sprintf("HTTP %d", resp.status)
			wait = c.rateLimitWait(resp)
		case resp.status >= 500:
			lastKind, lastDetail = kindRequestFailed, fmt.Sprintf("HTTP %d", resp.status)
			wait = backoff(attempt)
		default:
			return nil, &fetchError{Kind: kindRequestFailed, Detail: fmt.Sprintf("HTTP %d", resp.status)}
		}
		if attempt == maxAttempts {
			break
		}
		c.logf("%s (%s), повтор через %s", pageURL, lastDetail, wait)
		c.sleep(wait)
	}
	return nil, &fetchError{Kind: lastKind, Detail: lastDetail}
}

func (c *Client) handleOK(pageURL string, resp *response) (*page, error) {
	items, err := parseItems(resp.body)
	if err != nil {
		return nil, &fetchError{Kind: kindInvalidResponse, Detail: err.Error()}
	}
	links := resp.header.Values("Link")
	if etag := resp.header.Get("ETag"); etag != "" {
		e := &cacheEntry{URL: pageURL, ETag: etag, Link: links, Body: json.RawMessage(resp.body)}
		if err := c.cache.Store(e); err != nil {
			c.logf("кэш не обновлён: %v", err)
		}
	}
	return &page{items: items, next: nextLink(links, resp.finalURL)}, nil
}

func (c *Client) handleNotModified(entry *cacheEntry, resp *response) (*page, error) {
	if entry == nil {
		return nil, &fetchError{Kind: kindInvalidResponse, Detail: "HTTP 304 без заголовка If-None-Match"}
	}
	items, err := parseItems(entry.Body)
	if err != nil {
		return nil, &fetchError{Kind: kindInvalidResponse, Detail: "повреждённый кэш: " + err.Error()}
	}
	links := resp.header.Values("Link")
	if len(links) == 0 {
		links = entry.Link
	}
	return &page{items: items, next: nextLink(links, resp.finalURL), fromCache: true}, nil
}

// backoff returns 1s, 2s, 4s for attempts 1, 2, 3.
func backoff(attempt int) time.Duration {
	return time.Second << uint(attempt-1)
}

// isRateLimited reports whether a 403/429 response means an exhausted limit.
func isRateLimited(r *response) bool {
	if r.status == http.StatusTooManyRequests {
		return true
	}
	if r.status != http.StatusForbidden {
		return false
	}
	if r.header.Get("Retry-After") != "" {
		return true
	}
	if strings.TrimSpace(r.header.Get("X-RateLimit-Remaining")) == "0" {
		return true
	}
	body := strings.ToLower(string(r.body))
	return strings.Contains(body, "rate limit") || strings.Contains(body, "abuse detection")
}

// rateLimitWait: Retry-After, else X-RateLimit-Reset, else 60 seconds.
func (c *Client) rateLimitWait(r *response) time.Duration {
	if v := strings.TrimSpace(r.header.Get("Retry-After")); v != "" {
		if secs, err := strconv.ParseInt(v, 10, 64); err == nil {
			return clampWait(time.Duration(secs) * time.Second)
		}
		if t, err := http.ParseTime(v); err == nil {
			return clampWait(t.Sub(c.now()))
		}
	}
	if v := strings.TrimSpace(r.header.Get("X-RateLimit-Reset")); v != "" {
		if epoch, err := strconv.ParseInt(v, 10, 64); err == nil {
			return clampWait(time.Unix(epoch, 0).Sub(c.now()))
		}
	}
	return minRateLimitWait
}

func clampWait(d time.Duration) time.Duration {
	if d < 0 {
		return 0
	}
	if d > maxWait {
		return maxWait
	}
	return d
}

var linkPartRe = regexp.MustCompile(`<([^>]*)>([^<]*)`)

// nextLink returns the absolute URL of rel="next" from the Link headers.
func nextLink(headers []string, base string) string {
	for _, h := range headers {
		for _, m := range linkPartRe.FindAllStringSubmatch(h, -1) {
			if !relIsNext(m[2]) {
				continue
			}
			next, err := resolveURL(base, m[1])
			if err != nil {
				return ""
			}
			return next
		}
	}
	return ""
}

func relIsNext(params string) bool {
	for _, p := range strings.Split(params, ";") {
		p = strings.Trim(strings.TrimSpace(p), ",")
		k, v, ok := strings.Cut(p, "=")
		if !ok || strings.ToLower(strings.TrimSpace(k)) != "rel" {
			continue
		}
		v = strings.Trim(strings.TrimSpace(v), `"`)
		for _, f := range strings.Fields(strings.ToLower(v)) {
			if f == "next" {
				return true
			}
		}
	}
	return false
}
