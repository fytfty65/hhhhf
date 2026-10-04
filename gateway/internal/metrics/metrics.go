// Package metrics holds small process-wide counters that more than one layer
// needs to observe.
//
// It exists as its own leaf package because internal/api imports
// internal/handlers (route.go registers handlers), so counters cannot live in
// either one without an import cycle.
package metrics

import (
	"sync"
	"sync/atomic"
	"time"
)

// Bandit learning-loop counters.
//
// The "missing_arm" counter exists to make a specific silent failure visible:
// when the client does not return the bandit arm it was shown, the reward is
// dropped and the policy learns nothing. That was the state of this codebase
// before the arm id was threaded back from the planner to the satisfaction
// form, and it produced no error, no log line, and no metric.
var (
	banditFeedbackSent              atomic.Int64
	banditFeedbackAccepted          atomic.Int64
	banditFeedbackRejected          atomic.Int64
	banditFeedbackMissingArm        atomic.Int64
	auditCleanupMu                  sync.RWMutex
	auditCleanupLastRun             time.Time
	auditCleanupLastDeleted         int64
	auditCleanupLastError           string
	auditCleanupRuns                int64
	auditCleanupFailures            int64
	providerPushRuns                atomic.Int64
	providerPushErrors              atomic.Int64
	providerPushClaimed             atomic.Int64
	providerPushSent                atomic.Int64
	providerPushRetrying            atomic.Int64
	providerPushFailed              atomic.Int64
	providerPushInvalidToken        atomic.Int64
	providerPushNoToken             atomic.Int64
	providerPushDryRuns             atomic.Int64
	providerPushLastRunUnix         atomic.Int64
	providerDeliveryCleanups        atomic.Int64
	providerDeliveryDeleted         atomic.Int64
	providerDeliveryFailures        atomic.Int64
	providerDeliveryLastSuccessUnix atomic.Int64

	// WebSocket 广播背压：Hub 在客户端发送缓冲写满时原来直接 close+踢出，
	// **既没有日志也没有指标** —— 用户表现是"方案永远不来"，服务端却什么都看不到。
	// 这是一个真实的静默失败面（代码级确认）；计数与日志从 2026-10-04 起补上。
	// 注意：当天排查"脚本等 240s"时曾误判成本处丢弃所致，真正原因是脚本在房间 WS 上
	// 等一个不会出现的 final_route（该通道只发 ai_negotiation_result）。教训写在这里，
	// 免得下次又把"等错类型"记成"被踢了"。
	wsTokenDropped      atomic.Int64
	wsSlowClientDropped atomic.Int64
)

// WebSocket 背压丢弃原因（用于 RecordWSBroadcastDrop）。
const (
	WSDropStreamToken = "stream_token_backpressure"
	WSDropSlowClient  = "slow_client_disconnected"
)

// RecordWSBroadcastDrop 记录一次广播背压处置。
func RecordWSBroadcastDrop(reason string) {
	switch reason {
	case WSDropStreamToken:
		wsTokenDropped.Add(1)
	case WSDropSlowClient:
		wsSlowClientDropped.Add(1)
	}
}

// WSDropCount 返回某类背压处置的累计次数（调用方据此限流自己的日志）。
func WSDropCount(reason string) int64 {
	switch reason {
	case WSDropStreamToken:
		return wsTokenDropped.Load()
	case WSDropSlowClient:
		return wsSlowClientDropped.Load()
	default:
		return 0
	}
}

// WSBroadcastDropCounters 返回背压计数器快照（供健康检查/观测端点读取）。
func WSBroadcastDropCounters() map[string]int64 {
	return map[string]int64{
		WSDropStreamToken: wsTokenDropped.Load(),
		WSDropSlowClient:  wsSlowClientDropped.Load(),
	}
}

// Outcome names accepted by RecordBanditFeedback.
const (
	OutcomeSent       = "sent"
	OutcomeAccepted   = "accepted"
	OutcomeRejected   = "rejected"
	OutcomeMissingArm = "missing_arm"
)

// RecordBanditFeedback increments the counter for one reward-signal outcome.
func RecordBanditFeedback(outcome string) {
	switch outcome {
	case OutcomeSent:
		banditFeedbackSent.Add(1)
	case OutcomeAccepted:
		banditFeedbackAccepted.Add(1)
	case OutcomeRejected:
		banditFeedbackRejected.Add(1)
	case OutcomeMissingArm:
		banditFeedbackMissingArm.Add(1)
	}
}

// BanditFeedbackCounters returns a snapshot of the learning-loop counters.
func BanditFeedbackCounters() map[string]int64 {
	return map[string]int64{
		OutcomeSent:       banditFeedbackSent.Load(),
		OutcomeAccepted:   banditFeedbackAccepted.Load(),
		OutcomeRejected:   banditFeedbackRejected.Load(),
		OutcomeMissingArm: banditFeedbackMissingArm.Load(),
	}
}

// MissingArmCount reports how many reward signals were dropped for lack of an
// arm id; callers use it to rate-limit their own warnings.
func MissingArmCount() int64 {
	return banditFeedbackMissingArm.Load()
}

// RecordAuditCleanup stores only aggregate worker health; it never stores an
// event payload, user identity, database path or provider data.
func RecordAuditCleanup(deleted int64, err error) {
	auditCleanupMu.Lock()
	defer auditCleanupMu.Unlock()
	auditCleanupLastRun = time.Now().UTC()
	auditCleanupLastDeleted = deleted
	auditCleanupLastError = ""
	auditCleanupRuns++
	if err != nil {
		auditCleanupLastError = err.Error()
		auditCleanupFailures++
	}
}

func AuditCleanupSnapshot() map[string]any {
	auditCleanupMu.RLock()
	defer auditCleanupMu.RUnlock()
	status := "not_run"
	if !auditCleanupLastRun.IsZero() {
		status = "healthy"
		if auditCleanupLastError != "" {
			status = "warning"
		}
	}
	return map[string]any{
		"status": status, "last_run_at": auditCleanupLastRun.Format(time.RFC3339),
		"last_deleted": auditCleanupLastDeleted, "last_error": auditCleanupLastError,
		"runs": auditCleanupRuns, "failures": auditCleanupFailures,
	}
}

// RecordProviderQualityPush records only aggregate worker outcomes. It never
// receives or stores a user id, notification body, provider response, or
// device token, so the metrics endpoint cannot become a delivery data leak.
func RecordProviderQualityPush(result map[string]int, processErr error) {
	providerPushRuns.Add(1)
	providerPushLastRunUnix.Store(time.Now().UTC().Unix())
	if processErr != nil {
		providerPushErrors.Add(1)
	}
	if result == nil {
		return
	}
	providerPushClaimed.Add(int64(result["claimed"]))
	providerPushSent.Add(int64(result["sent"]))
	providerPushRetrying.Add(int64(result["retrying"]))
	providerPushFailed.Add(int64(result["failed"]))
	providerPushInvalidToken.Add(int64(result["invalid_token"]))
	providerPushNoToken.Add(int64(result["no_token"]))
	providerPushDryRuns.Add(int64(result["dry_run"]))
}

// RecordProviderQualityDeliveryCleanup records aggregate retention-worker
// health. Deletion is intentionally bounded to delivery rows only.
func RecordProviderQualityDeliveryCleanup(deleted int64, err error) {
	providerDeliveryCleanups.Add(1)
	if err != nil {
		providerDeliveryFailures.Add(1)
		return
	}
	providerDeliveryDeleted.Add(deleted)
	providerDeliveryLastSuccessUnix.Store(time.Now().UTC().Unix())
}

// ProviderQualityPushSnapshot returns a process-wide, credential-free view of
// the Web Push worker and delivery cleanup.
func ProviderQualityPushSnapshot() map[string]int64 {
	return map[string]int64{
		"runs": providerPushRuns.Load(), "errors": providerPushErrors.Load(),
		"claimed": providerPushClaimed.Load(), "sent": providerPushSent.Load(),
		"retrying": providerPushRetrying.Load(), "failed": providerPushFailed.Load(),
		"invalid_token": providerPushInvalidToken.Load(), "no_token": providerPushNoToken.Load(),
		"dry_runs":      providerPushDryRuns.Load(),
		"last_run_unix": providerPushLastRunUnix.Load(),
		"cleanup_runs":  providerDeliveryCleanups.Load(), "cleanup_deleted": providerDeliveryDeleted.Load(),
		"cleanup_failures":          providerDeliveryFailures.Load(),
		"cleanup_last_success_unix": providerDeliveryLastSuccessUnix.Load(),
	}
}
