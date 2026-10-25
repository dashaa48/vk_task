package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"
)

// Issue is one report entry.
type Issue struct {
	Number    int      `json:"number"`
	Title     string   `json:"title"`
	Author    string   `json:"author"`
	Labels    []string `json:"labels"`
	CreatedAt string   `json:"created_at"`
	URL       string   `json:"url"`
}

// RepoReport is the result for one repository.
type RepoReport struct {
	Repo   string  `json:"repo"`
	Error  *string `json:"error"`
	Count  int     `json:"count"`
	Issues []Issue `json:"issues"`
}

// Report is the whole output document.
type Report struct {
	GeneratedAt string       `json:"generated_at"`
	Repos       []RepoReport `json:"repos"`
}

// parsedItem is an element of an API response page.
type parsedItem struct {
	isPR    bool
	number  int
	title   string
	author  string
	labels  []string
	created time.Time
	rawTime string
	url     string
}

// parseItems validates a response body (a JSON array of objects) and extracts
// the fields we need. Pull requests are only flagged, not parsed.
func parseItems(body []byte) ([]parsedItem, error) {
	var elems []map[string]json.RawMessage
	if err := json.Unmarshal(body, &elems); err != nil {
		return nil, fmt.Errorf("тело ответа не является массивом JSON: %v", err)
	}
	if elems == nil {
		return nil, errors.New("тело ответа: null вместо массива")
	}
	items := make([]parsedItem, 0, len(elems))
	for i, m := range elems {
		if m == nil {
			return nil, fmt.Errorf("элемент %d: ожидается объект", i)
		}
		if _, ok := m["pull_request"]; ok {
			items = append(items, parsedItem{isPR: true})
			continue
		}
		it, err := parseIssue(m)
		if err != nil {
			return nil, fmt.Errorf("элемент %d: %v", i, err)
		}
		items = append(items, it)
	}
	return items, nil
}

func optString(m map[string]json.RawMessage, key string) (string, error) {
	raw, ok := m[key]
	if !ok {
		return "", nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return "", fmt.Errorf("поле %s не строка", key)
	}
	return s, nil
}

func parseIssue(m map[string]json.RawMessage) (parsedItem, error) {
	var it parsedItem
	if err := json.Unmarshal(m["number"], &it.number); err != nil {
		return it, errors.New("поле number отсутствует или не целое")
	}
	var err error
	if it.title, err = optString(m, "title"); err != nil {
		return it, err
	}
	if it.url, err = optString(m, "html_url"); err != nil {
		return it, err
	}
	if it.rawTime, err = optString(m, "created_at"); err != nil {
		return it, err
	}
	it.created, err = time.Parse(time.RFC3339, it.rawTime)
	if err != nil {
		return it, errors.New("поле created_at отсутствует или не RFC3339")
	}
	if raw, ok := m["user"]; ok {
		var u struct {
			Login string `json:"login"`
		}
		if err := json.Unmarshal(raw, &u); err != nil {
			return it, errors.New("поле user некорректно")
		}
		it.author = u.Login
	}
	if raw, ok := m["labels"]; ok {
		var labels []json.RawMessage
		if err := json.Unmarshal(raw, &labels); err != nil {
			return it, errors.New("поле labels не массив")
		}
		for _, l := range labels {
			var s string
			if json.Unmarshal(l, &s) == nil {
				it.labels = append(it.labels, s)
				continue
			}
			var o struct {
				Name string `json:"name"`
			}
			if err := json.Unmarshal(l, &o); err != nil {
				return it, errors.New("метка некорректна")
			}
			it.labels = append(it.labels, o.Name)
		}
	}
	return it, nil
}

// collectIssues walks the pages of one repository (strictly via the Link
// header), filters and de-duplicates, stops at cfg.Limit and sorts the result.
func (c *Client) collectIssues(repo string, cfg *Config) ([]Issue, error) {
	pageURL := fmt.Sprintf("%s/repos/%s/issues?state=open&sort=created&direction=asc&per_page=100", c.baseURL, repo)
	seenNumbers := map[int]bool{}
	visited := map[string]bool{}
	var recs []parsedItem

	for pageURL != "" {
		if visited[pageURL] {
			return nil, &fetchError{Kind: kindInvalidResponse, Detail: "цикл в заголовке Link"}
		}
		visited[pageURL] = true

		pg, err := c.fetchPage(pageURL)
		if err != nil {
			return nil, err
		}
		for _, it := range pg.items {
			if it.isPR {
				continue
			}
			if cfg.HasSince && it.created.Before(cfg.Since) {
				continue
			}
			if seenNumbers[it.number] {
				continue
			}
			seenNumbers[it.number] = true
			recs = append(recs, it)
			if len(recs) >= cfg.Limit {
				break
			}
		}
		if len(recs) >= cfg.Limit {
			break
		}
		pageURL = pg.next
	}

	sort.SliceStable(recs, func(i, j int) bool {
		if !recs[i].created.Equal(recs[j].created) {
			return recs[i].created.Before(recs[j].created)
		}
		return recs[i].number < recs[j].number
	})

	out := make([]Issue, 0, len(recs))
	for _, r := range recs {
		labels := r.labels
		if labels == nil {
			labels = []string{}
		}
		out = append(out, Issue{
			Number:    r.number,
			Title:     r.title,
			Author:    r.author,
			Labels:    labels,
			CreatedAt: r.rawTime,
			URL:       r.url,
		})
	}
	return out, nil
}
