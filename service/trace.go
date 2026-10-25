package main

import (
	"encoding/json"
	"os"
)

// traceRecord is one JSON Lines entry: one per HTTP attempt.
type traceRecord struct {
	TS        string  `json:"ts"`
	Method    string  `json:"method"`
	URL       string  `json:"url"`
	Status    int     `json:"status"`
	Attempt   int     `json:"attempt"`
	FromCache bool    `json:"from_cache"`
	Error     *string `json:"error"`
}

// Tracer appends records to a file. A nil *Tracer is a valid no-op tracer.
type Tracer struct {
	f *os.File
}

func newTracer(path string) (*Tracer, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return nil, err
	}
	return &Tracer{f: f}, nil
}

func (t *Tracer) Log(rec traceRecord) {
	if t == nil {
		return
	}
	data, err := json.Marshal(rec)
	if err != nil {
		return
	}
	data = append(data, '\n')
	_, _ = t.f.Write(data) // unbuffered: every attempt is on disk immediately
}

func (t *Tracer) Close() {
	if t != nil {
		_ = t.f.Close()
	}
}
