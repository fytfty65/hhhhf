package database

import (
	"errors"
	"strings"
	"time"

	"github.com/mattn/go-sqlite3"
	"gorm.io/gorm"
)

// SQLite has exactly one writer. The DSN sets a busy timeout so a second
// writer waits instead of failing immediately, but a timeout can still expire
// under a slow backup or a long analytics write. Retrying the *write* is safe
// for the append-only rows this package inserts, and it turns a user-visible
// 500 into a slightly slower success.
//
// Reads must never be retried blindly: a WAL reader sees a consistent snapshot,
// and replaying an arbitrary query is the caller's decision, not ours.

const maxWriteAttempts = 4

var writeRetryBackoff = []time.Duration{
	25 * time.Millisecond,
	75 * time.Millisecond,
	200 * time.Millisecond,
}

// IsRetryableWrite reports whether err is SQLite's "busy"/"locked" contention
// rather than a real failure. The typed check is preferred; the string check
// covers errors that GORM or a wrapper has already turned into a plain error.
func IsRetryableWrite(err error) bool {
	if err == nil {
		return false
	}
	var sqliteErr sqlite3.Error
	if errors.As(err, &sqliteErr) {
		return sqliteErr.Code == sqlite3.ErrBusy || sqliteErr.Code == sqlite3.ErrLocked
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "database is locked") ||
		strings.Contains(message, "database table is locked") ||
		strings.Contains(message, "sqlite_busy") ||
		strings.Contains(message, "sqlite_locked")
}

// RetryWrite runs fn until it succeeds, fails with a non-retryable error, or
// exhausts the attempts. It is intended for short insert/update work; wrapping a
// multi-second transaction would hold the caller far longer than a retry helps.
func RetryWrite(fn func() error) error {
	var err error
	for attempt := 0; attempt < maxWriteAttempts; attempt++ {
		if err = fn(); err == nil || !IsRetryableWrite(err) {
			return err
		}
		if attempt < len(writeRetryBackoff) {
			time.Sleep(writeRetryBackoff[attempt])
		}
	}
	return err
}

// CreateWithRetry inserts one row through the write pool.
func CreateWithRetry(db *gorm.DB, value any) error {
	return RetryWrite(func() error {
		return db.Create(value).Error
	})
}

// CreateInBatchesWithRetry inserts a slice in batches of batchSize.
//
// Batching matters more than it looks on SQLite: one INSERT statement per row
// means one write-lock acquisition per row, so a burst of N events holds and
// releases the writer N times. CreateInBatches turns that into N/batchSize
// round trips. Retries wrap the whole batch; duplicate rows on a retry are
// avoided because a failed batch either committed wholly or not at all.
func CreateInBatchesWithRetry(db *gorm.DB, value any, batchSize int) error {
	if batchSize <= 0 {
		batchSize = 100
	}
	return RetryWrite(func() error {
		return db.CreateInBatches(value, batchSize).Error
	})
}
