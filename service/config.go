package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	defaultLimit    = 100
	defaultOut      = "-"
	defaultCacheDir = "./.ghreport-cache"
	defaultBaseURL  = "https://api.github.com"
)

// Config holds the validated command line.
type Config struct {
	Repos    []string
	Limit    int
	Since    time.Time
	HasSince bool
	Out      string
	Trace    string
	CacheDir string
}

var errHelp = errors.New("help requested")

var repoRe = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

// parseArgs parses and validates the command line (without the program name).
func parseArgs(args []string) (*Config, error) {
	fs := flag.NewFlagSet("ghreport", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	reposF := fs.String("repos", "", "")
	limitF := fs.String("limit", "", "")
	sinceF := fs.String("since", "", "")
	outF := fs.String("out", defaultOut, "")
	traceF := fs.String("trace", "", "")
	cacheF := fs.String("cache-dir", defaultCacheDir, "")

	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil, errHelp
		}
		return nil, err
	}
	if fs.NArg() > 0 {
		return nil, fmt.Errorf("неожиданный аргумент %q", fs.Arg(0))
	}
	set := map[string]bool{}
	fs.Visit(func(f *flag.Flag) { set[f.Name] = true })

	cfg := &Config{Limit: defaultLimit}

	if !set["repos"] || strings.TrimSpace(*reposF) == "" {
		return nil, errors.New("--repos обязателен: owner/repo[,owner/repo...]")
	}
	seen := map[string]bool{}
	for _, r := range strings.Split(*reposF, ",") {
		r = strings.TrimSpace(r)
		if !repoRe.MatchString(r) {
			return nil, fmt.Errorf("--repos: некорректный репозиторий %q, ожидается owner/repo", r)
		}
		owner, name, _ := strings.Cut(r, "/")
		if owner == "." || owner == ".." || name == "." || name == ".." {
			return nil, fmt.Errorf("--repos: некорректный репозиторий %q, ожидается owner/repo", r)
		}
		if seen[r] {
			continue
		}
		seen[r] = true
		cfg.Repos = append(cfg.Repos, r)
	}

	if set["limit"] {
		n, err := strconv.Atoi(strings.TrimSpace(*limitF))
		if err != nil || n < 1 {
			return nil, errors.New("--limit должен быть целым числом, не меньшим единицы")
		}
		cfg.Limit = n
	}

	if set["since"] {
		t, err := time.Parse("2006-01-02", *sinceF)
		if err != nil {
			return nil, errors.New("--since должен быть датой в формате YYYY-MM-DD")
		}
		cfg.Since = t.UTC()
		cfg.HasSince = true
	}

	cfg.Out = *outF
	if cfg.Out == "" {
		return nil, errors.New("--out не может быть пустым (используйте - для стандартного вывода)")
	}
	cfg.Trace = *traceF
	if set["trace"] && cfg.Trace == "" {
		return nil, errors.New("--trace не может быть пустым")
	}
	cfg.CacheDir = *cacheF
	if cfg.CacheDir == "" {
		return nil, errors.New("--cache-dir не может быть пустым")
	}
	return cfg, nil
}
