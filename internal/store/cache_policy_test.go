package store

import (
	"testing"
	"time"
)

func TestCachePolicyDoesNotHideInvalidationAndHandlesMaximum(t *testing.T) {
	entry := CacheEntry{CheckedAt: time.Now().Add(-time.Hour).UTC().Format(time.RFC3339Nano), Policy: &CachePolicy{Enabled: true, IntervalMinutes: MaxCacheIntervalMinutes}}
	if entry.Stale(time.Minute) {
		t.Fatal("maximum interval overflowed")
	}
	entry.Invalidated = true
	if !entry.Stale(time.Minute) {
		t.Fatal("policy hid mutation invalidation")
	}
	entry.Invalidated = false
	entry.Policy.Enabled = false
	if !entry.Stale(time.Minute) {
		t.Fatal("disabled policy was considered fresh")
	}
	entry.Policy.Enabled = true
	entry.CheckedAt = "invalid"
	if !entry.Stale(time.Minute) {
		t.Fatal("invalid timestamp was considered fresh")
	}
}
