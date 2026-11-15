package handlers

import (
	"bufio"
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

// 定义前端传来的 Payload 结构体
type FrontendMessage struct {
	Type    string `json:"type"`
	Payload struct {
		Destinations    []string `json:"destinations"`
		UserPreferences struct {
			Mode                 string        `json:"mode"`
			Role                 string        `json:"role"`
			Intent               string        `json:"intent"`
			HistorySequence      []string      `json:"history_sequence"`
			CurrentExistingRoute []interface{} `json:"current_existing_route"`
		} `json:"user_preferences"`
	} `json:"payload"`
}

var negotiationSlots = struct {
	sync.Mutex
	active map[string]bool
}{active: make(map[string]bool)}

// 房间当前视图（大厅 / 规划页）由网关保存一份。
//
// 为什么不让房主来回答「成员索要视图」：房主的读循环在 agent_negotiate 期间
// 会被推演整段阻塞，此时新成员进场问「房主在哪」房主根本读不到消息，
// 成员就只能停在大厅等推演结束。这里由网关直接回放最近一次 room_view。
var roomViewStates = struct {
	sync.Mutex
	views map[string]map[string]interface{}
}{views: make(map[string]map[string]interface{})}

func recordRoomView(roomID string, payload map[string]interface{}) {
	roomViewStates.Lock()
	roomViewStates.views[roomID] = payload
	roomViewStates.Unlock()
}

func currentRoomView(roomID string) map[string]interface{} {
	roomViewStates.Lock()
	defer roomViewStates.Unlock()
	return roomViewStates.views[roomID]
}

// 房间节点建议（成员对某个节点的文字建议）。
// 缓存最近 200 条，供晚加入的成员/房主进场时拉取；采纳或忽略只改状态，
// 不删除记录，方便回看谁提了什么、最后由谁处理。
var roomCommentStates = struct {
	sync.Mutex
	comments map[string][]map[string]interface{}
}{comments: make(map[string][]map[string]interface{})}

const maxRoomComments = 200

func appendRoomComment(roomID string, comment map[string]interface{}) {
	roomCommentStates.Lock()
	defer roomCommentStates.Unlock()
	list := append(roomCommentStates.comments[roomID], comment)
	if len(list) > maxRoomComments {
		list = list[len(list)-maxRoomComments:]
	}
	roomCommentStates.comments[roomID] = list
}

func updateRoomCommentStatus(roomID, id, status, userID, userName string) {
	roomCommentStates.Lock()
	defer roomCommentStates.Unlock()
	list := roomCommentStates.comments[roomID]
	for _, comment := range list {
		if comment["id"] == id {
			comment["status"] = status
			comment["handled_by"] = userID
			comment["handled_by_name"] = userName
		}
	}
}

func snapshotRoomComments(roomID string) []map[string]interface{} {
	roomCommentStates.Lock()
	defer roomCommentStates.Unlock()
	list := roomCommentStates.comments[roomID]
	out := make([]map[string]interface{}, len(list))
	copy(out, list)
	return out
}

func acquireNegotiation(roomID string) bool {
	negotiationSlots.Lock()
	defer negotiationSlots.Unlock()
	if negotiationSlots.active[roomID] {
		return false
	}
	negotiationSlots.active[roomID] = true
	return true
}

func releaseNegotiation(roomID string) {
	negotiationSlots.Lock()
	delete(negotiationSlots.active, roomID)
	negotiationSlots.Unlock()
}

// collectCommunityReference 拉取社区热门行程作为软引导参考，注入给 Python 推理时借用（不改动 AI 核心逻辑）。
func collectCommunityReference() []map[string]string {
	var posts []models.CommunityPost
	if err := database.DB.Order("likes desc, created_at desc").Limit(3).Find(&posts).Error; err != nil {
		return []map[string]string{}
	}
	ref := make([]map[string]string, 0, len(posts))
	for _, p := range posts {
		// 截断内容，避免把超长 JSON 灌入 Prompt
		content := p.Content
		runes := []rune(content)
		if len(runes) > 400 {
			content = string(runes[:400]) + "..."
		}
		ref = append(ref, map[string]string{
			"title":     p.Title,
			"dest_city": p.DestCity,
			"content":   content,
		})
	}
	return ref
}

// collectEvolutionMemory 拉取房间内最近的真实交互反馈（正/负样本），实现系统自我进化闭环。
func collectEvolutionMemory(roomID string) []models.FeedbackLog {
	feedback := make([]models.FeedbackLog, 0)
	if database.DB == nil {
		return feedback
	}
	database.DB.Where("room_id = ?", roomID).Order("created_at desc").Limit(5).Find(&feedback)
	return feedback
}

// collectMemberProfiles 聚合房间内各在线成员的用户旅行画像，供推荐引擎做多画像融合。
func collectMemberProfiles(roomMembers []map[string]string) map[string]interface{} {
	profiles := map[string]interface{}{}
	for _, m := range roomMembers {
		id, ok := m["id"]
		if !ok || id == "" {
			continue
		}
		profiles[id] = service.ProfilePayload(id)
	}
	return profiles
}

// HandleWebSocket 处理前端的 WS 升级请求
func HandleWebSocket(c *gin.Context) {
	roomID := c.Query("room_id")
	userID := CurrentUserID(c)
	nickname := c.Query("nickname")
	role := c.Query("role")
	intent := c.Query("intent")
	avatarUrl := c.Query("avatar_url")

	// 容错处理
	if roomID == "" {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "缺少 room_id", "code": "ROOM_ID_REQUIRED"})
		return
	}
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "未登录", "code": "AUTH_REQUIRED"})
		return
	}
	if nickname == "" {
		nickname = userID
	}
	if role == "" {
		role = "成员"
	}
	// Query-string display fields are untrusted input and are later included in
	// prompts/logs. Bound them before retaining them for the connection.
	nickname = truncateRunes(strings.TrimSpace(nickname), 80)
	role = truncateRunes(strings.TrimSpace(role), 40)
	intent = truncateRunes(strings.TrimSpace(intent), 500)
	if _, ok := requireRoomMember(c, roomID); !ok {
		return
	}

	// 每个连接使用局部 upgrader：Subprotocols 需要回显客户端请求的
	// `bearer.<token>`，若共用一个全局变量并发升级会互相覆盖（数据竞争）。
	upgrader := websocket.Upgrader{
		CheckOrigin:  websocketOriginAllowed,
		Subprotocols: websocketSubprotocols(c.Request),
	}
	conn, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Println("🔥 WebSocket 升级失败:", err)
		return
	}
	conn.SetReadLimit(service.MaxMessageSize)

	// Liveness budget for the read side.
	//
	// This loop cannot delegate to service.ReadPump (it routes a larger message
	// vocabulary), and it previously read with no deadline whatsoever. A peer
	// that disappears without sending a close frame — a dropped mobile
	// connection, which is the common case — left this goroutine blocked in
	// ReadMessage forever, still registered in the hub with a live send channel.
	// WritePump's periodic ping is what elicits the pong that refreshes this
	// deadline, so an unresponsive peer is now reclaimed within the budget.
	// ConnectionPongWait() honours WS_PONG_WAIT_SECONDS so tests can assert the
	// behaviour without waiting the production 60s.
	pongWait := service.ConnectionPongWait()
	_ = conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(pongWait))
	})

	client := &service.Client{
		Hub:       GlobalHub,
		Conn:      conn,
		Send:      make(chan models.WSMessage, 256),
		RoomID:    roomID,
		UserID:    userID,
		Nickname:  nickname,
		Role:      role,
		Intent:    intent,
		AvatarUrl: avatarUrl,
	}
	client.Hub.Register <- client
	go client.WritePump()

	defer func() {
		client.Hub.Unregister <- client
		conn.Close()
	}()

	log.Printf("🟢 [Neural Link] 节点接入! Room: %s | User: %s", roomID, userID)

	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			log.Printf("🔴 节点断开 (%s): %v", userID, err)
			break
		}
		if len(message) > 1<<20 {
			_ = conn.WriteJSON(map[string]interface{}{"type": "error", "payload": map[string]string{"code": "MESSAGE_TOO_LARGE", "message": "消息体过大"}})
			continue
		}

		var msg FrontendMessage
		if err := json.Unmarshal(message, &msg); err != nil {
			log.Println("⚠️ JSON 解析错误:", err)
			continue
		}

		// 模块8：协作光标同步 / 编辑节点在场态（头像悬浮）。经 Hub 串行化落地 + 广播，线程安全。
		switch msg.Type {
		case "editing_node", "cursor_move":
			var p struct {
				Payload struct {
					NodeKey    string `json:"node_key"`
					Name       string `json:"name"`
					Role       string `json:"role"`
					AvatarUrl  string `json:"avatar_url"`
					AvatarSeed string `json:"avatar_seed"`
				} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			presence := service.ParsePresence(userID, nickname, role, avatarUrl, map[string]interface{}{
				"node_key":    p.Payload.NodeKey,
				"name":        p.Payload.Name,
				"role":        p.Payload.Role,
				"avatar_url":  p.Payload.AvatarUrl,
				"avatar_seed": p.Payload.AvatarSeed,
			})
			if presence.NodeKey == "" {
				GlobalHub.ClearPresence(roomID, userID)
			} else {
				GlobalHub.TrackPresence(roomID, presence)
			}
			continue
		case "cursor_leave":
			GlobalHub.ClearPresence(roomID, userID)
			continue
		case "room_view":
			// 房主所在视图（大厅 / 规划页）。/ws 只广播白名单类型，
			// 这里显式转发并注入发送者身份：前端据此忽略自己的回显、
			// 显示「谁在规划」，跟随中的成员据此自动进出规划页。
			var p struct {
				Payload map[string]interface{} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			payload := p.Payload
			if payload == nil {
				payload = map[string]interface{}{}
			}
			payload["user_id"] = userID
			payload["user_name"] = nickname
			recordRoomView(roomID, payload)
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    "room_view",
				RoomID:  roomID,
				UserID:  userID,
				Payload: payload,
			}
			continue
		case "room_view_request":
			// 新成员进场索要房主当前视图：网关直接回放，避免房主读循环被推演占住。
			// 只在房主仍在线时回放：房主关掉页面后，缓存里那份「规划中」已经失效，
			// 不该再把之后进场的成员拉进一个没有房主的规划页。
			if view := currentRoomView(roomID); view != nil {
				hostID, _ := view["user_id"].(string)
				online := false
				for _, m := range GlobalHub.CollectMembers(roomID) {
					if m["id"] == hostID {
						online = true
						break
					}
				}
				if online {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    "room_view",
						RoomID:  roomID,
						UserID:  "system_room_view",
						Payload: view,
					}
				}
			}
			continue
		case "room_plan_request":
			// 新成员进入房间时索要房主已推演好的方案：把请求转发给房间，
			// 由房主端决定是否回应（房主手里才有那份方案）。
			var p struct {
				Payload map[string]interface{} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			payload := p.Payload
			if payload == nil {
				payload = map[string]interface{}{}
			}
			payload["user_id"] = userID
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    msg.Type,
				RoomID:  roomID,
				UserID:  userID,
				Payload: payload,
			}
			continue
		case "room_plan":
			// 房主推演完成的方案：原样转发给房间内其他成员复用，
			// 不再让每个成员各自调用一次 AI。
			var p struct {
				Payload interface{} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    "room_plan",
				RoomID:  roomID,
				UserID:  userID,
				Payload: p.Payload,
			}
			continue
		case "node_comment":
			// 成员对某个行程节点的文字建议。缓存一份并广播给房间，
			// 房主端按节点聚合成「建议箱」，成员也能在自己的节点卡片上看到队友的话。
			var p struct {
				Payload map[string]interface{} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			payload := p.Payload
			if payload == nil {
				continue
			}
			nodeName, _ := payload["node_name"].(string)
			text, _ := payload["text"].(string)
			nodeName = truncateRunes(strings.TrimSpace(nodeName), 200)
			text = truncateRunes(strings.TrimSpace(text), 300)
			if nodeName == "" || text == "" {
				continue
			}
			id, _ := payload["id"].(string)
			id = strings.TrimSpace(id)
			if id == "" {
				id = "c_" + userID + "_" + strconv.FormatInt(time.Now().UnixNano(), 10)
			}
			comment := map[string]interface{}{
				"id":        id,
				"node_name": nodeName,
				"day":       payload["day"],
				"text":      text,
				"user_id":   userID,
				"user_name": nickname,
				"ts":        time.Now().UnixMilli(),
				"status":    "open",
			}
			appendRoomComment(roomID, comment)
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    "node_comment",
				RoomID:  roomID,
				UserID:  userID,
				Payload: comment,
			}
			continue
		case "node_comment_ack":
			// 房主采纳或忽略一条建议：只改状态并广播，让所有端看到处理结果。
			var p struct {
				Payload struct {
					ID     string `json:"id"`
					Status string `json:"status"`
				} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			id := strings.TrimSpace(p.Payload.ID)
			status := strings.ToLower(strings.TrimSpace(p.Payload.Status))
			if id == "" || (status != "adopted" && status != "dismissed" && status != "open") {
				continue
			}
			updateRoomCommentStatus(roomID, id, status, userID, nickname)
			GlobalHub.Broadcast <- models.WSMessage{
				Type:   "node_comment_ack",
				RoomID: roomID,
				UserID: userID,
				Payload: map[string]interface{}{
					"id":              id,
					"status":          status,
					"handled_by":      userID,
					"handled_by_name": nickname,
				},
			}
			continue
		case "node_comment_request":
			// 晚加入的成员进场时拉取已有建议，避免只能看到之后新增的几条。
			GlobalHub.Broadcast <- models.WSMessage{
				Type:    "node_comments_snapshot",
				RoomID:  roomID,
				UserID:  "system_room_comments",
				Payload: snapshotRoomComments(roomID),
			}
			continue
		case "node_vote":
			// 节点投票同步：本人已经在前端做了乐观更新，这里只负责把投票事实
			// 广播给房间内其他成员。空字符串 / "null" 表示撤销投票。
			// 节点名来自客户端，按展示字段处理：截断到合理长度后再进入广播。
			var p struct {
				Payload struct {
					NodeName string `json:"node_name"`
					Vote     string `json:"vote"`
				} `json:"payload"`
			}
			_ = json.Unmarshal(message, &p)
			nodeName := truncateRunes(strings.TrimSpace(p.Payload.NodeName), 200)
			if nodeName == "" {
				continue
			}
			vote := strings.ToLower(strings.TrimSpace(p.Payload.Vote))
			if vote == "null" {
				vote = ""
			}
			if vote != "" && vote != "up" && vote != "down" {
				continue
			}
			GlobalHub.Broadcast <- models.WSMessage{
				Type:   "node_vote",
				RoomID: roomID,
				UserID: userID,
				Payload: map[string]string{
					"node_name": nodeName,
					"vote":      vote,
					"user_id":   userID,
					"nickname":  nickname,
				},
			}
			continue
		}

		if msg.Type == "agent_negotiate" {
			if len(msg.Payload.Destinations) > 20 || len(msg.Payload.UserPreferences.HistorySequence) > 100 || len(msg.Payload.UserPreferences.CurrentExistingRoute) > 500 {
				GlobalHub.Broadcast <- models.WSMessage{Type: "error", RoomID: roomID, UserID: "system_omnigateway", Payload: map[string]string{"code": "NEGOTIATION_PAYLOAD_TOO_LARGE", "message": "推演参数过大，请精简诉求后重试"}}
				continue
			}
			if allowed, retryAfter := allowAgentNegotiation(userID); !allowed {
				seconds := int(retryAfter.Seconds())
				if retryAfter > time.Duration(seconds)*time.Second {
					seconds++
				}
				if seconds < 1 {
					seconds = 1
				}
				GlobalHub.Broadcast <- models.WSMessage{
					Type:   "error",
					RoomID: roomID,
					UserID: "system_omnigateway",
					Payload: map[string]interface{}{
						"code":        "AI_RATE_LIMITED",
						"message":     "智能体推演请求过于频繁，请稍后再试",
						"retry_after": seconds,
					},
				}
				continue
			}
			if !acquireNegotiation(roomID) {
				GlobalHub.Broadcast <- models.WSMessage{Type: "error", RoomID: roomID, Payload: map[string]string{"code": "NEGOTIATION_IN_PROGRESS", "message": "该房间已有推演任务进行中，请稍后再试"}}
				continue
			}
			intent := msg.Payload.UserPreferences.Intent
			log.Printf("🚀 接收到前端意图: 模式[%s], 诉求[%s]", msg.Payload.UserPreferences.Mode, intent)

			// 接下来的推演会把这个读循环占住几分钟：期间服务端收不到 pong，
			// 读截止时间不会被刷新。不清掉它的话，推演一结束（下一次 ReadMessage）
			// 就会立刻判成 i/o timeout 并断开**发起人**的连接 —— 表现为房主推演完
			// 打开的就是「房间连接正在建立，请稍后再点击开始规划」，再也动不了。
			_ = conn.SetReadDeadline(time.Time{})
			restoreReadDeadline := func() { _ = conn.SetReadDeadline(time.Now().Add(pongWait)) }

			// 👑 多人协同：聚合当前房间内所有在线成员的画像，传递给 Python 做多智能体博弈
			roomMembers := GlobalHub.CollectMembers(roomID)
			if len(roomMembers) == 0 {
				// 兜底：至少带上当前发起人自己
				roomMembers = []map[string]string{{
					"id":         userID,
					"name":       nickname,
					"role":       role,
					"intent":     intent,
					"avatar_url": avatarUrl,
				}}
			}

			userPrefsMap := map[string]interface{}{
				"mode":                   msg.Payload.UserPreferences.Mode,
				"role":                   msg.Payload.UserPreferences.Role,
				"intent":                 msg.Payload.UserPreferences.Intent,
				"history_sequence":       msg.Payload.UserPreferences.HistorySequence,
				"current_existing_route": msg.Payload.UserPreferences.CurrentExistingRoute,
				"room_members":           roomMembers,
				"community_reference":    collectCommunityReference(),
				// 🎯 个性化旅行画像注入：发起人与房间成员的画像一并下发给推荐引擎
				"personalized_profile": service.ProfilePayload(userID),
				"member_profiles":      collectMemberProfiles(roomMembers),
				// 参考路线画像（用户主动粘贴过的"我喜欢的一条路线"抽出的特征）：
				// 放在顶层是 ai-service 的读取口径（`user_prefs.get("reference_profile")`）；
				// 它在 Python 侧只于**当轮诉求没说明白**时参与决策，并会把依据回填到方案里。
				"reference_profile": service.ReferenceProfilePayload(userID),
			}

			// 将城市提取完全交给 Python 引擎处理，不再做硬编码猜测
			pythonPayload := map[string]interface{}{
				"type":    "negotiation_request",
				"room_id": roomID,
				"user_id": userID,
				"payload": map[string]interface{}{
					"current_request": map[string]interface{}{
						"destinations":     msg.Payload.Destinations,
						"city":             "",
						"user_preferences": userPrefsMap,
					},
					"evolution_memory": collectEvolutionMemory(roomID),
				},
			}

			// 向 Python 算法引擎发起 HTTP POST 请求
			pythonBase := os.Getenv("AI_SERVICE_URL")
			if pythonBase == "" {
				pythonBase = "http://127.0.0.1:8000"
			}
			pythonURL := strings.TrimRight(pythonBase, "/") + "/api/v1/agent/negotiate"
			reqBody, _ := json.Marshal(pythonPayload)

			log.Println("🧠 正在呼叫 Python 多智能体推演矩阵 (流式模式)...")
			httpClient := &http.Client{
				Timeout: 300 * time.Second,
			}
			// Bind the upstream call to the client's request context. With a
			// plain http.NewRequest the context is context.Background(), so a
			// user who closes the tab mid-planning left the gateway streaming
			// from the AI service (and burning LLM tokens) for up to the 300s
			// client timeout, with nobody left to receive the output. The
			// request context is cancelled when the client connection goes away,
			// so the upstream call is torn down with it.
			httpReq, reqErr := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, pythonURL, bytes.NewBuffer(reqBody))
			if reqErr == nil {
				httpReq.Header.Set("Content-Type", "application/json")
				if internalToken := strings.TrimSpace(os.Getenv("AI_SERVICE_INTERNAL_TOKEN")); internalToken != "" {
					httpReq.Header.Set("X-Omni-Internal-Token", internalToken)
				}
				if requestID := c.GetHeader("X-Request-ID"); requestID != "" {
					httpReq.Header.Set("X-Request-ID", requestID)
				}
			}
			var resp *http.Response
			var err error
			if reqErr != nil {
				err = reqErr
			} else {
				resp, err = httpClient.Do(httpReq)
			}

			if err != nil {
				releaseNegotiation(roomID)
				restoreReadDeadline()
				log.Println("🔥 呼叫 Python 引擎失败:", err)
				GlobalHub.Broadcast <- models.WSMessage{
					Type:    "error",
					RoomID:  roomID,
					Payload: "AI 引擎未响应，请检查 Python 服务是否在 8000 端口启动",
				}
				continue
			}
			if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
				status := resp.StatusCode
				resp.Body.Close()
				releaseNegotiation(roomID)
				restoreReadDeadline()
				GlobalHub.Broadcast <- models.WSMessage{
					Type:    "error",
					RoomID:  roomID,
					UserID:  "system_omnigateway",
					Payload: map[string]interface{}{"code": "AI_UPSTREAM_ERROR", "message": "智能体服务暂时不可用", "status": status},
				}
				continue
			}

			// ==========================================
			// 🚨 核心改造 2：流式读取 Python 响应，并实时广播！
			// ==========================================
			reader := bufio.NewReader(resp.Body)
			streamBytes := 0
			for {
				line, err := reader.ReadBytes('\n')
				if err != nil {
					break // 流结束或断开连接
				}

				line = bytes.TrimSpace(line)
				streamBytes += len(line)
				if streamBytes > 16<<20 {
					break
				}
				if len(line) == 0 {
					continue
				}

				var chunk map[string]interface{}
				if err := json.Unmarshal(line, &chunk); err != nil {
					continue
				}

				// 1. 👑 流式推理 token（正文逐字下发）
				if token, ok := chunk["token"]; ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    "stream_token",
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: token,
					}
					continue
				}

				// 2. � 真实导航路网坐标（高德 polyline），驱动前端绘制真实路线
				if path, ok := chunk["actual_path"]; ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    "actual_path",
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: path,
					}
					continue
				}

				// 3. 👑 结构化事件透传：target_city / weather_info / traffic_info /
				//    travel_details / budget_breakdown / safety_info / final_route 等
				if msgType, ok := chunk["type"].(string); ok {
					GlobalHub.Broadcast <- models.WSMessage{
						Type:    msgType,
						RoomID:  roomID,
						UserID:  "system_omnigateway",
						Payload: chunk["payload"],
					}
				}
			}
			resp.Body.Close() // 读取完毕后关闭连接
			releaseNegotiation(roomID)
			restoreReadDeadline()
			log.Printf("🏆 拓扑路书流式传输完毕，房间: %s", roomID)
		}
	}
}
