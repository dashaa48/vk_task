// Command ghreport builds a summary of open issues of GitHub repositories.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
)

// Exit codes.
const (
	exitOK      = 0
	exitPartial = 1
	exitUsage   = 2
	exitFatal   = 3
)

const usageMessage = `usage: ghreport --repos <owner/repo[,owner/repo...]> [--limit <N>] [--since <YYYY-MM-DD>]
                [--out <file|->] [--trace <file>] [--cache-dir <dir>]
environment: GITHUB_TOKEN (optional), GITHUB_API_BASE_URL (default https://api.github.com)
`

func main() {
	os.Exit(run(os.Args[1:], os.Getenv, os.Stdout, os.Stderr))
}

// run is main without global state, so that it can be tested.
func run(args []string, getenv func(string) string, stdout, stderr io.Writer) int {
	cfg, err := parseArgs(args)
	if err != nil {
		if errors.Is(err, errHelp) {
			fmt.Fprint(stderr, usageMessage)
			return exitOK
		}
		fmt.Fprintf(stderr, "ghreport: %v\n", err)
		return exitUsage
	}

	base := strings.TrimSpace(getenv("GITHUB_API_BASE_URL"))
	if base == "" {
		base = defaultBaseURL
	}
	token := strings.TrimSpace(getenv("GITHUB_TOKEN"))

	var cache *Cache
	if c, err := newCache(cfg.CacheDir); err != nil {
		fmt.Fprintf(stderr, "ghreport: кэш отключён: %v\n", err)
	} else {
		cache = c
	}

	var tracer *Tracer
	if cfg.Trace != "" {
		t, err := newTracer(cfg.Trace)
		if err != nil {
			fmt.Fprintf(stderr, "ghreport: --trace: %v\n", err)
			return exitUsage
		}
		tracer = t
	}
	defer tracer.Close()

	client, err := newClient(base, token, cache, tracer)
	if err != nil {
		fmt.Fprintf(stderr, "ghreport: %v\n", err)
		return exitUsage
	}
	client.logf = func(format string, a ...any) {
		fmt.Fprintf(stderr, "ghreport: "+format+"\n", a...)
	}

	report := Report{Repos: make([]RepoReport, 0, len(cfg.Repos))}
	failed := 0
	for _, repo := range cfg.Repos {
		rr := RepoReport{Repo: repo, Issues: []Issue{}}
		issues, err := client.collectIssues(repo, cfg)
		if err != nil {
			if errors.Is(err, errUnauthorized) {
				fmt.Fprintf(stderr, "ghreport: %s: ответ 401, проверьте GITHUB_TOKEN\n", repo)
				printFatal(stdout, "unauthorized")
				return exitFatal
			}
			kind := kindRequestFailed
			var fe *fetchError
			if errors.As(err, &fe) {
				kind = fe.Kind
			}
			fmt.Fprintf(stderr, "ghreport: %s: ошибка %s (%v)\n", repo, kind, err)
			rr.Error = &kind
			failed++
		} else {
			rr.Issues = issues
			rr.Count = len(issues)
			fmt.Fprintf(stderr, "ghreport: %s: %d issue\n", repo, rr.Count)
		}
		report.Repos = append(report.Repos, rr)
	}

	if failed == len(cfg.Repos) {
		printFatal(stdout, "all_failed")
		return exitFatal
	}

	report.GeneratedAt = time.Now().UTC().Format(time.RFC3339)
	if err := writeReport(cfg.Out, stdout, &report); err != nil {
		fmt.Fprintf(stderr, "ghreport: не удалось записать отчёт: %v\n", err)
		printFatal(stdout, "output_failed")
		return exitFatal
	}
	if failed > 0 {
		return exitPartial
	}
	return exitOK
}

func printFatal(stdout io.Writer, code string) {
	fmt.Fprintf(stdout, "{\"error\": %q}\n", code)
}

func writeReport(out string, stdout io.Writer, rep *Report) error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(rep); err != nil {
		return err
	}
	if out == "-" {
		_, err := stdout.Write(buf.Bytes())
		return err
	}
	return os.WriteFile(out, buf.Bytes(), 0o644)
}
