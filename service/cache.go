package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
)

// Cache keeps the ETag, the Link header and the body of successful (200)
// responses on disk, one file per request URL. It never stores request
// headers, so the token cannot end up in the cache.
type Cache struct {
	dir string
}

type cacheEntry struct {
	URL  string          `json:"url"`
	ETag string          `json:"etag"`
	Link []string        `json:"link,omitempty"`
	Body json.RawMessage `json:"body"`
}

func newCache(dir string) (*Cache, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &Cache{dir: dir}, nil
}

func (c *Cache) path(url string) string {
	sum := sha256.Sum256([]byte(url))
	return filepath.Join(c.dir, hex.EncodeToString(sum[:])+".json")
}

// Load returns the stored entry for url, or nil if there is none or the
// file is damaged (a damaged cache is treated as a miss).
func (c *Cache) Load(url string) *cacheEntry {
	if c == nil {
		return nil
	}
	data, err := os.ReadFile(c.path(url))
	if err != nil {
		return nil
	}
	var e cacheEntry
	if err := json.Unmarshal(data, &e); err != nil {
		return nil
	}
	if e.URL != url || e.ETag == "" || len(e.Body) == 0 {
		return nil
	}
	return &e
}

// Store writes the entry atomically (temporary file + rename).
func (c *Cache) Store(e *cacheEntry) error {
	if c == nil {
		return nil
	}
	data, err := json.Marshal(e)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(c.dir, "tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, c.path(e.URL)); err != nil {
		os.Remove(tmpName)
		return err
	}
	return nil
}
