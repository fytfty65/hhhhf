// Package metrics holds small process-wide counters that more than one layer
// needs to observe.
//
// It exists as its own leaf package because internal/api imports
// internal/handlers (route.go registers handlers), so counters cannot live in
// either one without an import cycle.
package metrics

import "sync/atomic"

// Bandit learning-loop counters.
//
// The "missing_arm" counter exists to make a specific silent failure visible:
// when the client does not return the bandit arm it was shown, the reward is
// dropped and the policy learns nothing. That was the state of this codebase
// before the arm id was threaded back from the planner to the satisfaction
// form, and it produced no error, no log line, and no metric.
var (
	banditFeedbackSent       atomic.Int64
	banditFeedbackAccepted   atomic.Int64
	banditFeedbackRejected   atomic.Int64
	banditFeedbackMissingArm atomic.Int64
)

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
