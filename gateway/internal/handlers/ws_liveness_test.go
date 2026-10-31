package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"gateway/internal/database"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

// WebSocket liveness tests over a real connection.
//
// Why these exist: HandleWebSocket's read loop ran with no read deadline, so a
// peer that disappeared without a close frame — the normal outcome for a dropped
// mobile connection — left its goroutine blocked in ReadMessage forever, still
// registered in the hub and holding its send channel. Nothing failed, nothing was
// logged; the connection simply never went away. These tests open an actual
// WebSocket against the real handler and assert both directions of the contract:
// a silent peer is reclaimed, and a peer that answers pings is not.

// wsTestEnv sets up a real migrated database and starts a hub, then serves the
// real router over a real HTTP listener so a WebSocket can actually be dialed.
//
// Serving the whole router (rather than a hand-built subset) is deliberate: it
// exercises the same middleware order as main.go, including AuthMiddleware, which
// is what resolves the session token to a user identity.
func wsTestEnv(t *testing.T) (*httptest.Server, *gin.Engine) {
	t.Helper()
	gin.SetMode(gin.TestMode)

	dir := t.TempDir()
	t.Setenv("DB_PATH", filepath.Join(dir, "omniroute.db"))
	// In-process session store: no Redis needed, and nothing shared with a
	// developer's running instance.
	t.Setenv("REDIS_URL", "")
	t.Setenv("APP_ENV", "development")
	t.Setenv("ALLOWED_ORIGINS", "")

	if err := database.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}
	t.Cleanup(func() {
		if database.DB == nil {
			return
		}
		if sqlDB, err := database.DB.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
	})

	GlobalHub = service.NewHub()
	go GlobalHub.Run()

	router := gin.New()
	registerWSRoutes(router)

	srv := httptest.NewServer(router)
	t.Cleanup(srv.Close)
	return srv, router
}

// registerWSRoutes mounts the routes these tests need.
//
// It cannot call api.RegisterRoutes (that would be an import cycle: api imports
// handlers), so the subset is declared here. It must mirror the real wiring —
// including AuthMiddleware on the /api/v1 group, which is what resolves the
// session token to a user identity. The token is therefore issued and consumed by
// the same process and the same in-memory session store, which is what makes the
// identity resolution in this test real rather than stubbed.
func registerWSRoutes(r *gin.Engine) {
	r.POST("/api/auth/register", AuthRegisterRateLimitMiddleware(), RegisterHandler)
	r.POST("/api/auth/login", AuthLoginRateLimitMiddleware(), LoginHandler)
	r.POST("/api/auth/ws-ticket", AuthMiddleware(), IssueWSTicketHandler)

	apiGroup := r.Group("/api/v1")
	apiGroup.Use(AuthMiddleware())
	apiGroup.POST("/room/create", RoomRateLimitMiddleware(), CreateRoomHandler)
	apiGroup.POST("/room/join", RoomRateLimitMiddleware(), JoinRoomHandler)

	r.GET("/ws", AuthMiddleware(), HandleWebSocket)
}

// wsDial opens a WebSocket against the test server using a freshly registered
// user and a room they own, so the handler's identity and membership checks pass.
func wsDial(t *testing.T, srv *httptest.Server, r *gin.Engine, extraQuery string) (*websocket.Conn, string) {
	t.Helper()

	token, userID := registerUserForWS(t, r, "ws_smoke_"+strings.ReplaceAll(t.Name(), "/", "_"))
	roomID := createRoomForWS(t, r, token)

	// The socket uses a single-use ticket, never the long-lived token.
	code, ticketResp := wsJSON(t, r, http.MethodPost, "/api/auth/ws-ticket", token, nil)
	ticket, _ := ticketResp["ticket"].(string)
	if code != http.StatusOK || ticket == "" {
		t.Fatalf("ws-ticket: %d (%v)", code, ticketResp)
	}

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") +
		"/ws?room_id=" + roomID + "&ticket=" + ticket + "&user_id=" + userID + extraQuery

	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, resp, err := dialer.Dial(wsURL, nil)
	if err != nil {
		status := 0
		if resp != nil {
			status = resp.StatusCode
			if body := readAll(resp); body != "" {
				t.Fatalf("dial %s: %v (status %d, body %s)", wsURL, err, status, body)
			}
		}
		t.Fatalf("dial %s: %v (status %d)", wsURL, err, status)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn, roomID
}

func readAll(resp *http.Response) string {
	if resp == nil || resp.Body == nil {
		return ""
	}
	defer resp.Body.Close()
	var buf bytes.Buffer
	_, _ = buf.ReadFrom(resp.Body)
	return buf.String()
}

// registerUserForWS creates a user through the real endpoint and returns the
// session token and user id. This is what makes the test meaningful: the token is
// minted by the real session store and resolved by the real AuthMiddleware.
func registerUserForWS(t *testing.T, r *gin.Engine, username string) (string, string) {
	t.Helper()
	code, reg := wsJSON(t, r, http.MethodPost, "/api/auth/register", "", map[string]any{
		"username": username,
		"password": "ws-smoke-password",
	})
	if code != http.StatusOK {
		t.Fatalf("register: %d (%v)", code, reg)
	}
	// Registration deliberately does not create a session; sign in to obtain a
	// short-lived access token plus the device session that backs its ticket.
	code, login := wsJSON(t, r, http.MethodPost, "/api/auth/login", "", map[string]any{
		"username": username,
		"password": "ws-smoke-password",
	})
	if code != http.StatusOK {
		t.Fatalf("login: %d (%v)", code, login)
	}
	token, _ := login["token"].(string)
	user, _ := login["user"].(map[string]any)
	userID, _ := user["id"].(string)
	if token == "" || userID == "" {
		t.Fatalf("login did not return token/user: %v", login)
	}
	return token, userID
}

// createRoomForWS creates a room owned by the caller and returns the room id.
// The handler keys rooms by invite code; the response shape is probed rather than
// assumed so a field rename shows up as a clear failure here.
func createRoomForWS(t *testing.T, r *gin.Engine, token string) string {
	t.Helper()
	code, room := wsJSON(t, r, http.MethodPost, "/api/v1/room/create", token, map[string]any{
		"username":  "ws-smoke",
		"room_name": "WS 存活测试房间",
	})
	if code != http.StatusOK {
		t.Fatalf("create room: %d (%v)", code, room)
	}
	for _, key := range []string{"room_id", "id", "invite_code", "code", "roomId"} {
		if v, ok := room[key].(string); ok && v != "" {
			return v
		}
	}
	if nested, ok := room["room"].(map[string]any); ok {
		for _, key := range []string{"id", "invite_code"} {
			if v, ok := nested[key].(string); ok && v != "" {
				return v
			}
		}
	}
	t.Fatalf("could not determine room id from %v", room)
	return ""
}

// TestWebSocketReclaimsSilentPeer is the regression test for the leak. The client
// connects and then goes completely silent — it never processes the server's ping
// and so never replies with a pong — which is what a dropped connection looks
// like. The server must close it once the read deadline expires rather than
// leaving the goroutine blocked in ReadMessage forever.
func TestWebSocketReclaimsSilentPeer(t *testing.T) {
	// Shorten the budget so the assertion takes seconds, not a minute.
	t.Setenv("WS_PONG_WAIT_SECONDS", "1")

	srv, router := wsTestEnv(t)
	conn, _ := wsDial(t, srv, router, "")

	// Make the client genuinely unresponsive. Reading at all would make gorilla's
	// default ping handler reply with a pong, which keeps the server's read
	// deadline refreshed forever — i.e. a healthy client, which is the opposite
	// of what this test needs. Installing a no-op ping handler and then draining
	// messages keeps the client silent on the wire while still letting us observe
	// the moment the server gives up on it.
	conn.SetPingHandler(func(string) error { return nil })

	start := time.Now()
	var readErr error
	for i := 0; i < 200; i++ {
		if _, _, readErr = conn.ReadMessage(); readErr != nil {
			break
		}
	}
	elapsed := time.Since(start)

	if readErr == nil {
		t.Fatal("expected the server to close an unresponsive connection, but reads kept succeeding")
	}
	// Budget is 1s; allow generous slack for scheduling. The point is that it
	// closed on its own rather than being held open indefinitely.
	if elapsed > 20*time.Second {
		t.Fatalf("server took %v to reclaim an unresponsive peer; the read deadline is not being applied", elapsed)
	}
}

// TestWebSocketKeepsResponsivePeerAlive is the converse guard: reclamation must
// not be so aggressive that a healthy client is disconnected.
//
// The client here reads *continuously with no read deadline*, which is what a real
// browser does. That matters for a subtle reason discovered while writing this
// test: gorilla treats a read-deadline expiry as a permanent failure of the
// connection ("repeated read on failed websocket connection"), so polling with
// short read deadlines does not merely risk missing a message — it poisons the
// connection and stops gorilla from answering the server's ping. That made the
// server correctly drop a client the test had already broken, which looked like a
// false failure in the reclamation logic. A healthy client must therefore be
// modelled as one that reads rather than one that polls.
func TestWebSocketKeepsResponsivePeerAlive(t *testing.T) {
	// A deadline well under the derived ping spacing would be too tight for a
	// slow CI machine; use a budget where several pings fit inside the window.
	t.Setenv("WS_PONG_WAIT_SECONDS", "2")

	srv, router := wsTestEnv(t)
	conn, _ := wsDial(t, srv, router, "")

	// The client's default ping handler answers each ping with a pong, which
	// refreshes the server's read deadline. Stay for several ping periods.
	aliveFor := 4 * time.Second
	done := make(chan error, 1)
	go func() {
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				done <- err
				return
			}
		}
	}()

	select {
	case err := <-done:
		t.Fatalf("a responsive peer was disconnected after less than %v: %v", aliveFor, err)
	case <-time.After(aliveFor):
		// Still connected: the liveness budget is not reaping healthy peers.
	}
}

func isTimeoutErr(err error) bool {
	type timeouter interface{ Timeout() bool }
	if te, ok := err.(timeouter); ok {
		return te.Timeout()
	}
	return strings.Contains(strings.ToLower(err.Error()), "timeout")
}

func wsJSON(t *testing.T, r *gin.Engine, method, path, token string, body any) (int, map[string]any) {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	req := httptest.NewRequest(method, path, strings.NewReader(string(raw)))
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	out := map[string]any{}
	if res.Body.Len() > 0 {
		_ = json.Unmarshal(res.Body.Bytes(), &out)
	}
	return res.Code, out
}
