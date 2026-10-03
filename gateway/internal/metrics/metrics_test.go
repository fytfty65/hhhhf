package metrics

import (
	"errors"
	"testing"
	"time"
)

func TestAuditCleanupSnapshotTracksSuccessAndFailure(t *testing.T) {
	before := AuditCleanupSnapshot()
	RecordAuditCleanup(3, nil)
	afterSuccess := AuditCleanupSnapshot()
	if afterSuccess["status"] != "healthy" || afterSuccess["last_deleted"] != int64(3) {
		t.Fatalf("unexpected successful cleanup snapshot: %#v", afterSuccess)
	}
	if afterSuccess["runs"].(int64) != before["runs"].(int64)+1 {
		t.Fatalf("cleanup run counter did not increment: before=%#v after=%#v", before, afterSuccess)
	}
	RecordAuditCleanup(0, errors.New("test failure"))
	afterFailure := AuditCleanupSnapshot()
	if afterFailure["status"] != "warning" || afterFailure["failures"].(int64) != afterSuccess["failures"].(int64)+1 {
		t.Fatalf("unexpected failed cleanup snapshot: %#v", afterFailure)
	}
}

func TestProviderQualitySnapshotsExposeRunAndCleanupTimestamps(t *testing.T) {
	before := time.Now().UTC().Unix()
	RecordProviderQualityPush(map[string]int{"sent": 1}, nil)
	RecordProviderQualityDeliveryCleanup(2, nil)
	after := ProviderQualityPushSnapshot()
	if got := after["last_run_unix"]; got < before {
		t.Fatalf("worker run timestamp was not recorded: %#v", after)
	}
	if got := after["cleanup_last_success_unix"]; got < before {
		t.Fatalf("cleanup success timestamp was not recorded: %#v", after)
	}
	if after["cleanup_deleted"] < 2 || after["sent"] < 1 {
		t.Fatalf("aggregate counters were not recorded: %#v", after)
	}

	previousSuccess := after["cleanup_last_success_unix"]
	RecordProviderQualityDeliveryCleanup(0, errors.New("temporary db failure"))
	failed := ProviderQualityPushSnapshot()
	if failed["cleanup_failures"] < 1 || failed["cleanup_last_success_unix"] != previousSuccess {
		t.Fatalf("failed cleanup must not move last-success timestamp: %#v", failed)
	}
}
