package handlers

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// Node-vote sync tests over a real WebSocket pair.
//
// Why these exist: votes used to live only in the voter's browser state, so a
// second member in the same room never saw them and "collaborative voting" was a
// local-only illusion. The gateway now rebroadcasts a vote fact; these tests pin
// that contract down (relayed to peers, attributed to the voter, invalid votes
// dropped, and null meaning "withdraw").

func dialWSForRoom(t *testing.T, serverURL, roomID, token, userID string) *websocket.Conn {
	t.Helper()
	wsURL := "ws" + strings.TrimPrefix(serverURL, "http") +
		"/ws?room_id=" + roomID + "&access_token=" + token + "&user_id=" + userID

	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, resp, err := dialer.Dial(wsURL, nil)
	if err != nil {
		status := 0
		if resp != nil {
			status = resp.StatusCode
		}
		t.Fatalf("dial %s: %v (status %d)", wsURL, err, status)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn
}

// waitForMemberCount blocks until the room reports the expected number of online
// members, so the vote broadcast cannot race a still-registering peer.
func waitForMemberCount(t *testing.T, conn *websocket.Conn, want int, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		msg, ok := readWSMessage(t, conn, "room_members_update", deadline)
		if !ok {
			t.Fatalf("room never reached %d members before %v", want, timeout)
		}
		if members, ok := msg["payload"].([]any); ok && len(members) >= want {
			return
		}
	}
}

// readWSMessage drains frames until the wanted type arrives or the deadline
// passes. Other broadcast traffic (members, presence) is ignored.
func readWSMessage(t *testing.T, conn *websocket.Conn, wantType string, deadline time.Time) (map[string]any, bool) {
	t.Helper()
	for {
		if err := conn.SetReadDeadline(deadline); err != nil {
			t.Fatalf("set read deadline: %v", err)
		}
		_, raw, err := conn.ReadMessage()
		if err != nil {
			if isTimeoutErr(err) {
				return nil, false
			}
			t.Fatalf("reading %q: %v", wantType, err)
		}
		var msg map[string]any
		if err := json.Unmarshal(raw, &msg); err != nil {
			continue
		}
		if msg["type"] == wantType {
			return msg, true
		}
	}
}

func TestWebSocketBroadcastsNodeVoteToRoomMembers(t *testing.T) {
	srv, router := wsTestEnv(t)

	ownerToken, ownerID := registerUserForWS(t, router, "vote_owner")
	roomID := createRoomForWS(t, router, ownerToken)

	peerToken, peerID := registerUserForWS(t, router, "vote_peer")
	code, joined := wsJSON(t, router, http.MethodPost, "/api/v1/room/join", peerToken, map[string]any{
		"username":    "vote_peer",
		"invite_code": roomID,
	})
	if code != http.StatusOK {
		t.Fatalf("join room: %d (%v)", code, joined)
	}

	ownerConn := dialWSForRoom(t, srv.URL, roomID, ownerToken, ownerID)
	peerConn := dialWSForRoom(t, srv.URL, roomID, peerToken, peerID)

	// Both sockets must be registered in the hub before the vote is sent.
	waitForMemberCount(t, ownerConn, 2, 5*time.Second)

	// An invalid vote must be dropped rather than relayed; the first node_vote the
	// peer sees has to be the valid one that follows.
	if err := ownerConn.WriteJSON(map[string]any{
		"type":    "node_vote",
		"payload": map[string]any{"node_name": "故宫", "vote": "maybe"},
	}); err != nil {
		t.Fatalf("write invalid vote: %v", err)
	}
	if err := ownerConn.WriteJSON(map[string]any{
		"type":    "node_vote",
		"payload": map[string]any{"node_name": "故宫", "vote": "up"},
	}); err != nil {
		t.Fatalf("write vote: %v", err)
	}

	msg, ok := readWSMessage(t, peerConn, "node_vote", time.Now().Add(5*time.Second))
	if !ok {
		t.Fatal("peer never received the node_vote broadcast")
	}
	if msg["user_id"] != ownerID {
		t.Fatalf("vote attributed to %v, want %s", msg["user_id"], ownerID)
	}
	payload, _ := msg["payload"].(map[string]any)
	if payload == nil || payload["node_name"] != "故宫" || payload["vote"] != "up" {
		t.Fatalf("unexpected vote payload: %v", msg["payload"])
	}
	if payload["user_id"] != ownerID {
		t.Fatalf("payload user_id %v, want %s", payload["user_id"], ownerID)
	}

	// null means "withdraw my vote" and must arrive as an empty value, not "null".
	if err := ownerConn.WriteJSON(map[string]any{
		"type":    "node_vote",
		"payload": map[string]any{"node_name": "故宫", "vote": nil},
	}); err != nil {
		t.Fatalf("write withdraw vote: %v", err)
	}
	withdraw, ok := readWSMessage(t, peerConn, "node_vote", time.Now().Add(5*time.Second))
	if !ok {
		t.Fatal("peer never received the withdrawal broadcast")
	}
	withdrawPayload, _ := withdraw["payload"].(map[string]any)
	if withdrawPayload == nil || withdrawPayload["node_name"] != "故宫" || withdrawPayload["vote"] != "" {
		t.Fatalf("unexpected withdrawal payload: %v", withdraw["payload"])
	}
}
