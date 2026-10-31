package service

import (
	"os"
	"testing"
	"time"
)

// Tests for the WebSocket connection lifetime budget.
//
// These exist because the read path in internal/handlers/ws_handler.go used to
// run a bare ReadMessage loop with no deadline, so a peer that vanished without a
// close frame left its goroutine blocked forever, still registered in the hub.
// The fix applies the same budget ReadPump already used; these tests pin the
// numbers that make that budget coherent.

// TestPingPeriodLeavesRoomForAPong is the invariant that makes the deadline
// work: the server must ping and receive the pong *before* the read deadline
// expires. If pingPeriod were >= pongWait, every connection would be reaped even
// when perfectly healthy.
//
// Note the deliberate shape of the budget: a ping goes out every PingPeriod and
// the pong must arrive before the previous deadline. The pong deadline is
// therefore evaluated from the last pong, so PingPeriod must be strictly less
// than PongWait, but only by enough to cover network latency — a ping sent at
// 54s against a 60s deadline has 6s to complete, which is the standard
// gorilla/websocket arrangement.
func TestPingPeriodLeavesRoomForAPong(t *testing.T) {
	if PingPeriod <= 0 || PongWait <= 0 {
		t.Fatalf("durations must be positive: pingPeriod=%v pongWait=%v", PingPeriod, PongWait)
	}
	if PingPeriod >= PongWait {
		t.Fatalf("pingPeriod (%v) must be strictly less than pongWait (%v), "+
			"otherwise healthy connections are dropped", PingPeriod, PongWait)
	}
	// The slack is the window a pong has to arrive. Too little and a healthy but
	// slow client is disconnected; this guards against someone tightening
	// PongWait without also adjusting PingPeriod.
	const minSlack = 3 * time.Second
	if slack := PongWait - PingPeriod; slack < minSlack {
		t.Fatalf("pong wait leaves only %v of slack for the pong to arrive; "+
			"need at least %v (pingPeriod=%v pongWait=%v)", slack, minSlack, PingPeriod, PongWait)
	}
}

// TestMaxMessageSizeIsBounded keeps the inbound frame limit sane: large enough
// for a route payload, small enough that one peer cannot exhaust memory.
func TestMaxMessageSizeIsBounded(t *testing.T) {
	if MaxMessageSize < 64<<10 {
		t.Fatalf("MaxMessageSize (%d) is too small for a planning payload", MaxMessageSize)
	}
	if MaxMessageSize > 8<<20 {
		t.Fatalf("MaxMessageSize (%d) is too large to bound memory per connection", MaxMessageSize)
	}
}

// TestConnectionPongWaitDefaultsToProductionBudget pins the default so a test
// override cannot accidentally become the shipped value.
func TestConnectionPongWaitDefaultsToProductionBudget(t *testing.T) {
	t.Setenv("WS_PONG_WAIT_SECONDS", "")
	if got := ConnectionPongWait(); got != PongWait {
		t.Fatalf("default must be PongWait (%v), got %v", PongWait, got)
	}
}

// TestConnectionPongWaitHonoursOverride allows the fast test path.
func TestConnectionPongWaitHonoursOverride(t *testing.T) {
	t.Setenv("WS_PONG_WAIT_SECONDS", "3")
	if got := ConnectionPongWait(); got != 3*time.Second {
		t.Fatalf("expected 3s, got %v", got)
	}
}

// TestConnectionPongWaitFallsBackOnBadInput is the safety property: a typo in the
// override must not disable reclamation (which would reintroduce the leak) and
// must not produce a zero/negative deadline (which would kill every connection
// instantly).
func TestConnectionPongWaitFallsBackOnBadInput(t *testing.T) {
	for _, bad := range []string{"abc", "0", "-5", "  ", "1.5"} {
		t.Run(bad, func(t *testing.T) {
			t.Setenv("WS_PONG_WAIT_SECONDS", bad)
			got := ConnectionPongWait()
			if got != PongWait {
				t.Fatalf("input %q must fall back to PongWait (%v), got %v", bad, PongWait, got)
			}
			if got <= 0 {
				t.Fatalf("fallback must be positive, got %v", got)
			}
		})
	}
}

// TestConnectionPongWaitIsReadPerCall documents that the value is not cached at
// package init: tests set the environment after the package is loaded.
func TestConnectionPongWaitIsReadPerCall(t *testing.T) {
	t.Setenv("WS_PONG_WAIT_SECONDS", "2")
	first := ConnectionPongWait()
	if err := os.Setenv("WS_PONG_WAIT_SECONDS", "7"); err != nil {
		t.Fatal(err)
	}
	second := ConnectionPongWait()
	if first != 2*time.Second || second != 7*time.Second {
		t.Fatalf("expected 2s then 7s, got %v then %v", first, second)
	}
}

// TestConnectionPingPeriodTracksTheDeadline is the guard that caught a real
// mistake: shrinking only the read deadline (for a fast test) while the ping
// period stayed at 54s meant a healthy client was disconnected before the first
// ping ever arrived. The ping must always be sent comfortably before the
// deadline, at every budget.
func TestConnectionPingPeriodTracksTheDeadline(t *testing.T) {
	t.Setenv("WS_PONG_WAIT_SECONDS", "1")
	wait, period := ConnectionPongWait(), ConnectionPingPeriod()
	if period <= 0 || period >= wait {
		t.Fatalf("with a %v deadline the ping period must be positive and shorter, got %v", wait, period)
	}

	// With the production budget the exact production cadence is preserved, so a
	// test override cannot silently change shipped timing.
	t.Setenv("WS_PONG_WAIT_SECONDS", "")
	if got := ConnectionPingPeriod(); got != PingPeriod {
		t.Fatalf("production ping period must stay %v, got %v", PingPeriod, got)
	}
	if got := ConnectionPongWait(); got != PongWait {
		t.Fatalf("production pong wait must stay %v, got %v", PongWait, got)
	}
}

// TestConnectionPingPeriodIsSaneForBadInput ensures a bad override cannot produce
// a zero or negative ticker period, which would panic time.NewTicker.
func TestConnectionPingPeriodIsSaneForBadInput(t *testing.T) {
	for _, bad := range []string{"abc", "0", "-3", ""} {
		t.Run(bad, func(t *testing.T) {
			t.Setenv("WS_PONG_WAIT_SECONDS", bad)
			if got := ConnectionPingPeriod(); got <= 0 {
				t.Fatalf("ping period must be positive, got %v", got)
			}
		})
	}
}
