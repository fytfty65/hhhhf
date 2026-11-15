package handlers

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/models"
	"gateway/internal/service"

	"github.com/gin-gonic/gin"
)

// 个人旅行特征图谱（user-trait-graph-v1）。
//
// 数据只来自服务端已记录的事实，共三处：
//  1. UserPreference：偏好标签、预算倾向、自然/人文/美食配比、反馈样本数；
//  2. FeedbackLog：正负反馈的目标与原因（最多取最近 300 条）；
//  3. TripPlan：已保存路书里的节点分类与标签（只统计节点足够的完整行程）。
//
// 权限：userID 只取自会话（AuthMiddleware 写入的 principal），完全不读
// query/body 里的 user_id —— 传别人的 id 不会生效，图谱只能看自己的。
//
// 诚实口径：没有记录的特征不生成节点；样本为 0 时返回空图并说明怎么养出图谱，
// 不用默认值（比如默认 1/3 配比、默认 mid 预算）冒充「你的特征」。
// 行程里被截断的历史路书（少于 3 个停留点）不参与统计，避免把保存异常当成偏好。
func UserTraitGraphHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	planningData(c, buildUserTraitGraph(userID, c.Query("room_code")))
}

type traitGraphNode struct {
	ID       string  `json:"id"`
	Label    string  `json:"label"`
	Kind     string  `json:"kind"` // self | dimension | budget | tag | feedback
	Weight   float64 `json:"weight"`
	Evidence int     `json:"evidence_count"`
	Source   string  `json:"source"`
	// 房间共同交集：只有同房间至少 2 位成员都有的节点才标 true，由 GET 时实时计算。
	SharedWithRoom   bool `json:"shared_with_room,omitempty"`
	RoomOverlapCount int  `json:"room_overlap_count,omitempty"`
}

type traitGraphEdge struct {
	From     string  `json:"from"`
	To       string  `json:"to"`
	Relation string  `json:"relation"` // composition_weight | budget_tendency | likes | avoids | cuisine | trip_tag | feedback_up | feedback_down
	Weight   float64 `json:"weight"`
	Evidence int     `json:"evidence_count"`
	Note     string  `json:"note,omitempty"`
	Source   string  `json:"source"`
}

// buildUserTraitGraph 是纯派生逻辑（只读库，不写库），便于单测。
// roomCode 非空时额外计算"同房间成员共同偏好"，只在本人的节点上打标记，
// 不会把别人的画像节点返回给当前用户。
func buildUserTraitGraph(userID, roomCode string) gin.H {
	payload := service.ProfilePayload(userID)
	evidence := traitInt(payload["feedback_count"])

	// 没有反馈样本时，BuildProfileFromFeedback 会给出默认画像（风景/美食/人文、1:1:1、mid 预算）。
	// 那些不是"你的特征"，所以只有存在已保存的画像行（用户自己填过/系统写过）才采信标签；
	// 配比与预算还要再看是否偏离默认值，避免把默认值当成偏好。
	var stored models.UserPreference
	hasStored := database.DB != nil &&
		database.DB.Where("user_id = ?", userID).First(&stored).Error == nil
	trustTags := evidence > 0 || hasStored
	trustRatios := evidence > 0 || (hasStored && !traitIsDefaultRatios(stored))
	trustBudget := evidence > 0 || (hasStored && stored.BudgetTendency != "" && stored.BudgetTendency != "mid")

	nodes := []traitGraphNode{{
		ID: "self", Label: "你", Kind: "self", Weight: 1, Source: "session",
	}}
	edges := make([]traitGraphEdge, 0)
	seen := map[string]bool{"self": true}

	addNode := func(node traitGraphNode) bool {
		if node.ID == "" || seen[node.ID] || len(nodes) >= 60 {
			return false
		}
		seen[node.ID] = true
		nodes = append(nodes, node)
		return true
	}
	addEdge := func(from, to, relation string, weight float64, evidence int, note, source string) {
		if !seen[to] {
			return
		}
		edges = append(edges, traitGraphEdge{
			From: from, To: to, Relation: relation,
			Weight: traitRound1(weight), Evidence: evidence, Note: note, Source: source,
		})
	}

	// 1) 三类配比：只有真的有反馈样本、或画像行里的比例明显偏离默认时才画。
	if trustRatios {
		dims := []struct{ id, label, key string }{
			{"nature", "自然", "nature_ratio"},
			{"culture", "人文", "culture_ratio"},
			{"food", "美食", "food_ratio"},
		}
		for _, dim := range dims {
			ratio := traitFloat(payload[dim.key])
			if ratio <= 0 {
				continue
			}
			addNode(traitGraphNode{
				ID: dim.id, Label: dim.label, Kind: "dimension",
				Weight: traitRound1(ratio), Evidence: evidence, Source: "user_preference",
			})
			addEdge("self", dim.id, "composition_weight", ratio, evidence, "", "user_preference")
		}
	}

	// 2) 预算倾向（只有反馈样本、或用户明确设过非默认档时才画）。
	if trustBudget {
		if budget := traitBudgetLabel(traitText(payload["budget_tendency"])); budget != "" {
			addNode(traitGraphNode{
				ID: "budget", Label: budget, Kind: "budget",
				Weight: 1, Evidence: evidence, Source: "user_preference",
			})
			addEdge("self", "budget", "budget_tendency", 1, evidence, "", "user_preference")
		}
	}

	// 3) 偏好标签 / 口味：逐条都是用户记录过的值，条数不做虚构。
	if trustTags {
		for _, tag := range traitStrings(payload["liked_tags"]) {
			id := "tag:" + tag
			addNode(traitGraphNode{
				ID: id, Label: tag, Kind: "tag", Weight: 1, Source: "user_preference",
			})
			addEdge("self", id, "likes", 1, 0, "", "user_preference")
		}
		for _, tag := range traitStrings(payload["disliked_tags"]) {
			id := "avoid:" + tag
			addNode(traitGraphNode{
				ID: id, Label: tag, Kind: "tag", Weight: 1, Source: "user_preference",
			})
			addEdge("self", id, "avoids", 1, 0, "", "user_preference")
		}
		for _, cuisine := range traitStrings(payload["cuisine_prefs"]) {
			id := "cuisine:" + cuisine
			addNode(traitGraphNode{
				ID: id, Label: cuisine, Kind: "tag", Weight: 1, Source: "user_preference",
			})
			addEdge("self", id, "cuisine", 1, 0, "", "user_preference")
		}
	}

	// 4) 反馈目标：正/负各聚成一条边，权重与样本数都是真实的反馈条数。
	if database.DB != nil {
		var logs []models.FeedbackLog
		database.DB.Where("user_id = ?", userID).Order("created_at desc").Limit(300).Find(&logs)
		type aggregate struct {
			positive int
			negative int
			reason   string
		}
		byTarget := map[string]*aggregate{}
		order := make([]string, 0)
		for _, log := range logs {
			target := strings.TrimSpace(log.Target)
			if target == "" {
				continue
			}
			item, exists := byTarget[target]
			if !exists {
				item = &aggregate{}
				byTarget[target] = item
				order = append(order, target)
			}
			if log.Score > 0 {
				item.positive++
			} else if log.Score < 0 {
				item.negative++
			}
			if item.reason == "" && strings.TrimSpace(log.Reason) != "" {
				item.reason = traitTruncate(strings.TrimSpace(log.Reason), 40)
			}
		}
		for _, target := range order {
			item := byTarget[target]
			id := "fb:" + target
			label := traitTruncate(target, 40)
			addNode(traitGraphNode{
				ID: id, Label: label, Kind: "feedback",
				Weight:   float64(item.positive + item.negative),
				Evidence: item.positive + item.negative, Source: "feedback_log",
			})
			if item.positive > 0 {
				addEdge("self", id, "feedback_up", float64(item.positive), item.positive, item.reason, "feedback_log")
			}
			if item.negative > 0 {
				addEdge("self", id, "feedback_down", float64(item.negative), item.negative, item.reason, "feedback_log")
			}
		}
	}

	// 5) 已生成行程：路书里的分类与标签是用户真实排过的结果，而不是让用户再填一遍偏好。
	//    只统计最近 20 份完整行程（非住宿节点 >= 3）；历史上被截断成「1 天 1 个节点」的
	//    异常路书不参与，否则会把保存故障当成旅行偏好。
	tripCount := 0
	if database.DB != nil {
		var trips []models.TripPlan
		if err := database.DB.Where("user_id = ?", userID).Order("created_at desc").Limit(40).Find(&trips).Error; err == nil {
			dimTotals := map[string]int{}
			tagTrips := map[string]int{}
			for _, trip := range trips {
				var content traitTripContent
				if err := json.Unmarshal([]byte(trip.Content), &content); err != nil {
					continue
				}
				dimCounts := map[string]int{}
				tagSeen := map[string]bool{}
				stops := 0
				for _, node := range content.Routes {
					if node.IsHotel {
						continue
					}
					stops++
					if dim := traitTripDimension(node.Category, node.Tags); dim != "" {
						dimCounts[dim]++
					}
					for _, tag := range node.Tags {
						tag = strings.TrimSpace(tag)
						if tag == "" || traitTripTagIgnored(tag) {
							continue
						}
						tagSeen[tag] = true
					}
				}
				if stops < 3 {
					continue
				}
				tripCount++
				for dim, count := range dimCounts {
					dimTotals[dim] += count
				}
				for tag := range tagSeen {
					tagTrips[tag]++
				}
				if tripCount >= 20 {
					break
				}
			}

			classified := dimTotals["nature"] + dimTotals["culture"] + dimTotals["food"]
			if classified >= 6 {
				for _, dim := range []struct{ id, label string }{
					{"nature", "自然"}, {"culture", "人文"}, {"food", "美食"},
				} {
					count := dimTotals[dim.id]
					if count == 0 || seen[dim.id] {
						continue
					}
					ratio := float64(count) / float64(classified)
					if traitRound1(ratio) <= 0 {
						continue
					}
					addNode(traitGraphNode{
						ID: dim.id, Label: dim.label, Kind: "dimension",
						Weight: traitRound1(ratio), Evidence: tripCount, Source: "trip_plan",
					})
					addEdge("self", dim.id, "composition_weight", ratio, tripCount, "", "trip_plan")
				}
			}

			type tagStat struct {
				label string
				trips int
			}
			stats := make([]tagStat, 0, len(tagTrips))
			for tag, count := range tagTrips {
				// 只出现过一次的标签不足以说明偏好：行程多了以后至少要在两份行程里出现。
				minTrips := 1
				if tripCount >= 3 {
					minTrips = 2
				}
				if count < minTrips {
					continue
				}
				stats = append(stats, tagStat{label: tag, trips: count})
			}
			sort.Slice(stats, func(i, j int) bool {
				if stats[i].trips != stats[j].trips {
					return stats[i].trips > stats[j].trips
				}
				return stats[i].label < stats[j].label
			})
			added := 0
			for _, stat := range stats {
				if added >= 14 {
					break
				}
				id := "tag:" + stat.label
				if seen[id] {
					continue
				}
				addNode(traitGraphNode{
					ID: id, Label: stat.label, Kind: "tag",
					Weight: float64(stat.trips), Evidence: stat.trips, Source: "trip_plan",
				})
				addEdge("self", id, "trip_tag", float64(stat.trips), stat.trips, "", "trip_plan")
				added++
			}
		}
	}

	// 6) 用户在图上做过的显式操作：权重新标定、主动移出过时节点。
	//    两者都存在 UserPreference 里，刷新后依然生效。
	if hasStored {
		if hidden := parseTraitHidden(stored.TraitHidden); len(hidden) > 0 {
			hiddenSet := make(map[string]bool, len(hidden))
			for _, id := range hidden {
				hiddenSet[id] = true
			}
			keptNodes := make([]traitGraphNode, 0, len(nodes))
			for _, node := range nodes {
				if node.ID == "self" || !hiddenSet[node.ID] {
					keptNodes = append(keptNodes, node)
				}
			}
			nodes = keptNodes
			keptEdges := make([]traitGraphEdge, 0, len(edges))
			for _, edge := range edges {
				if !hiddenSet[edge.To] {
					keptEdges = append(keptEdges, edge)
				}
			}
			edges = keptEdges
		}
		if overrides := parseTraitWeights(stored.TraitWeights); len(overrides) > 0 {
			for i := range nodes {
				if weight, ok := overrides[nodes[i].ID]; ok && nodes[i].ID != "self" {
					nodes[i].Weight = traitRound2(weight)
				}
			}
			for i := range edges {
				if weight, ok := overrides[edges[i].To]; ok {
					edges[i].Weight = traitRound2(weight)
				}
			}
		}
	}

	// 7) 房间共同交集：由房间成员的真实画像取交集，而不是前端猜。
	sharedRoomCode, roomMemberCount, sharedCount := traitRoomConsensus(userID, roomCode, nodes)
	if roomMemberCount >= 2 && len(sharedCount) > 0 {
		for i := range nodes {
			if nodes[i].ID == "self" {
				continue
			}
			if count := sharedCount[nodes[i].ID]; count >= 2 {
				nodes[i].SharedWithRoom = true
				nodes[i].RoomOverlapCount = count
			}
		}
	}
	roomSharedTotal := 0
	for _, node := range nodes {
		if node.SharedWithRoom {
			roomSharedTotal++
		}
	}

	empty := len(nodes) <= 1
	totalEvidence := evidence + tripCount
	confidence := ""
	switch {
	case totalEvidence >= 10:
		confidence = "high"
	case totalEvidence >= 3:
		confidence = "medium"
	case totalEvidence > 0:
		confidence = "low"
	}
	disclosures := []string{
		"只包含当前登录账号自己的偏好、反馈与已生成行程，其他用户的数据不会进入这张图。",
		"没有记录过的特征不会生成节点；配比与预算只在有反馈样本时出现。",
		"行程特征来自已保存路书的分类与标签，少于 3 个停留点的历史路书不参与统计。",
	}
	if sharedRoomCode != "" && roomMemberCount >= 2 {
		disclosures = append(disclosures, "房间共同交集只统计同房间至少 2 位成员都有的特征，不展示其他成员的具体偏好。")
	}
	if empty {
		disclosures = append(disclosures, "还没有可用于建图的记录：生成一份行程、给几个地点点赞/踩，或填写偏好后再来看。")
	}

	return gin.H{
		"graph_version":     "user-trait-graph-v1",
		"owner_scope":       "self_only",
		"nodes":             nodes,
		"edges":             edges,
		"sample_count":      totalEvidence,
		"empty":             empty,
		"confidence":        confidence,
		"room_code":         sharedRoomCode,
		"room_member_count": roomMemberCount,
		"room_shared_count": roomSharedTotal,
		"disclosures":       disclosures,
		"updated_at":        time.Now().UTC(),
		"source":            candidateSource("omniroute-user-trait-graph", true),
	}
}

// 已保存路书 content 的最小结构（与前端 persistTripRoutes 写入的一致）。
type traitTripContent struct {
	City   string `json:"city"`
	Routes []struct {
		Name     string   `json:"name"`
		Category string   `json:"category"`
		Tags     []string `json:"tags"`
		IsHotel  bool     `json:"is_hotel"`
	} `json:"routes"`
}

// 把行程节点的分类/标签归到自然/人文/美食：住宿、交通等不参与配比。
// 先看分类、再看标签，避免"风景名胜"被打上文化标签后被算成人文。
func traitTripDimension(category string, tags []string) string {
	if dim := traitDimensionText(category); dim != "" {
		return dim
	}
	for _, tag := range tags {
		if dim := traitDimensionText(tag); dim != "" {
			return dim
		}
	}
	return ""
}

func traitDimensionText(text string) string {
	text = strings.ToLower(strings.TrimSpace(text))
	switch {
	case text == "":
		return ""
	case strings.Contains(text, "餐") || strings.Contains(text, "美食") || strings.Contains(text, "小吃") ||
		strings.Contains(text, "food") || strings.Contains(text, "寻味"):
		return "food"
	case strings.Contains(text, "博物") || strings.Contains(text, "文化") || strings.Contains(text, "科教") ||
		strings.Contains(text, "艺术") || strings.Contains(text, "古迹") || strings.Contains(text, "历史") ||
		strings.Contains(text, "museum") || strings.Contains(text, "culture"):
		return "culture"
	case strings.Contains(text, "风景") || strings.Contains(text, "自然") || strings.Contains(text, "公园") ||
		strings.Contains(text, "湿地") || strings.Contains(text, "生态") || strings.Contains(text, "山") ||
		strings.Contains(text, "湖") || strings.Contains(text, "nature") || strings.Contains(text, "scenic"):
		return "nature"
	}
	return ""
}

// 住宿类标签不进入偏好统计：几乎每份行程都有，不能说明偏爱什么。
func traitTripTagIgnored(tag string) bool {
	switch tag {
	case "住宿", "酒店", "民宿", "客栈", "交通", "出行":
		return true
	}
	return false
}

func traitInt(value interface{}) int {
	switch v := value.(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	}
	return 0
}

func traitFloat(value interface{}) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case float32:
		return float64(v)
	case int:
		return float64(v)
	}
	return 0
}

func traitText(value interface{}) string {
	if v, ok := value.(string); ok {
		return strings.TrimSpace(v)
	}
	return ""
}

func traitStrings(value interface{}) []string {
	switch v := value.(type) {
	case []string:
		return v
	case []interface{}:
		out := make([]string, 0, len(v))
		for _, item := range v {
			out = append(out, traitText(item))
		}
		return out
	}
	return nil
}

func traitBudgetLabel(tendency string) string {
	switch tendency {
	case "low":
		return "预算：经济"
	case "high":
		return "预算：宽裕"
	case "mid":
		return "预算：适中"
	}
	return ""
}

// 1:1:1 是系统在「没选过比例」时补的默认值，不能当成用户偏好画进图谱。
func traitIsDefaultRatios(pref models.UserPreference) bool {
	within := func(value, target float64) bool {
		diff := value - target
		if diff < 0 {
			diff = -diff
		}
		return diff < 0.02
	}
	return within(pref.NatureRatio, 1.0/3) &&
		within(pref.CultureRatio, 1.0/3) &&
		within(pref.FoodRatio, 1.0/3)
}

func traitRound1(value float64) float64 {
	return float64(int(value*10+0.5)) / 10
}

func traitRound2(value float64) float64 {
	return float64(int(value*100+0.5)) / 100
}

// parseTraitWeights / parseTraitHidden 读的是用户在图谱上的显式操作记录。
// 解析失败按"没有操作过"处理，不让脏数据把整张图打挂。
func parseTraitWeights(raw string) map[string]float64 {
	out := map[string]float64{}
	if strings.TrimSpace(raw) == "" {
		return out
	}
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return map[string]float64{}
	}
	for id, weight := range out {
		if strings.TrimSpace(id) == "" || math.IsNaN(weight) || math.IsInf(weight, 0) {
			delete(out, id)
			continue
		}
		if weight < 0 {
			weight = 0
		}
		if weight > 1 {
			weight = 1
		}
		out[id] = weight
	}
	return out
}

func parseTraitHidden(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	var out []string
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return nil
	}
	cleaned := make([]string, 0, len(out))
	seen := map[string]bool{}
	for _, id := range out {
		id = strings.TrimSpace(id)
		if id == "" || id == "self" || seen[id] {
			continue
		}
		seen[id] = true
		cleaned = append(cleaned, id)
	}
	return cleaned
}

// traitRoomConsensus 取房间成员的真实画像交集。
// 只返回"同房间至少 2 位成员都有"的节点计数，调用方只会把它标在本人已有的节点上，
// 因此不会泄漏其他成员的具体偏好。
func traitRoomConsensus(userID, roomCode string, ownNodes []traitGraphNode) (string, int, map[string]int) {
	roomCode = strings.ToUpper(strings.TrimSpace(roomCode))
	if database.DB == nil || roomCode == "" {
		return "", 0, nil
	}
	var room models.Room
	if err := database.DB.Where("UPPER(invite_code) = ?", roomCode).First(&room).Error; err != nil {
		return "", 0, nil
	}
	var members []models.RoomMember
	database.DB.Where("room_id = ?", room.ID).Limit(12).Find(&members)
	if len(members) < 2 {
		return room.InviteCode, len(members), nil
	}

	counts := map[string]int{}
	for _, member := range members {
		ids := map[string]bool{}
		if member.UserID == userID {
			for _, node := range ownNodes {
				ids[node.ID] = true
			}
		} else {
			memberGraph := buildUserTraitGraph(member.UserID, "")
			if list, ok := memberGraph["nodes"].([]traitGraphNode); ok {
				for _, node := range list {
					ids[node.ID] = true
				}
			}
		}
		for id := range ids {
			if id != "self" {
				counts[id]++
			}
		}
	}
	return room.InviteCode, len(members), counts
}

type traitNodeUpdateRequest struct {
	NodeID string   `json:"node_id"`
	Weight *float64 `json:"weight"`
	Hidden *bool    `json:"hidden"`
}

// UserTraitGraphNodeUpdateHandler 持久化图谱上的节点操作：
//   - weight：重标定节点权重（0~1），GET 时覆盖派生权重；
//   - hidden：移出/恢复节点（false 用于撤销移出）。
func UserTraitGraphNodeUpdateHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if database.DB == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "偏好存储暂不可用"})
		return
	}
	var req traitNodeUpdateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请求体格式错误"})
		return
	}
	nodeID := strings.TrimSpace(req.NodeID)
	if nodeID == "" || nodeID == "self" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "node_id 不合法"})
		return
	}
	if req.Weight == nil && req.Hidden == nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "weight 与 hidden 至少提供一个"})
		return
	}
	if req.Weight != nil && (math.IsNaN(*req.Weight) || math.IsInf(*req.Weight, 0)) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "weight 不合法"})
		return
	}

	pref := traitPreferenceForUpdate(userID)
	// 只允许操作当前图谱里真实存在的节点；hidden=false 是撤销移出，节点此刻被过滤掉了，
	// 所以不做存在性校验。
	if (req.Weight != nil || (req.Hidden != nil && *req.Hidden)) && !traitGraphHasNode(userID, nodeID) {
		c.JSON(http.StatusNotFound, gin.H{"error": "图谱里没有这个节点"})
		return
	}
	if req.Weight != nil {
		weights := parseTraitWeights(pref.TraitWeights)
		weight := *req.Weight
		if weight < 0 {
			weight = 0
		}
		if weight > 1 {
			weight = 1
		}
		weights[nodeID] = weight
		raw, _ := json.Marshal(weights)
		pref.TraitWeights = string(raw)
	}
	if req.Hidden != nil {
		hidden := parseTraitHidden(pref.TraitHidden)
		next := make([]string, 0, len(hidden)+1)
		for _, id := range hidden {
			if id != nodeID {
				next = append(next, id)
			}
		}
		if *req.Hidden {
			next = append(next, nodeID)
		}
		raw, _ := json.Marshal(next)
		pref.TraitHidden = string(raw)
	}
	if err := database.DB.Save(&pref).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "图谱设置保存失败"})
		return
	}
	planningData(c, buildUserTraitGraph(userID, ""))
}

// UserTraitGraphNodeDeleteHandler 把节点移出图谱（软删除：保留原始偏好数据，只是不再展示）。
func UserTraitGraphNodeDeleteHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	nodeID := strings.TrimSpace(c.Param("id"))
	if nodeID == "" || nodeID == "self" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "node_id 不合法"})
		return
	}
	if database.DB == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "偏好存储暂不可用"})
		return
	}
	pref := traitPreferenceForUpdate(userID)
	hidden := parseTraitHidden(pref.TraitHidden)
	for _, id := range hidden {
		if id == nodeID {
			planningData(c, buildUserTraitGraph(userID, ""))
			return
		}
	}
	hidden = append(hidden, nodeID)
	raw, _ := json.Marshal(hidden)
	pref.TraitHidden = string(raw)
	if err := database.DB.Save(&pref).Error; err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "图谱设置保存失败"})
		return
	}
	planningData(c, buildUserTraitGraph(userID, ""))
}

func traitPreferenceForUpdate(userID string) models.UserPreference {
	var pref models.UserPreference
	if err := database.DB.Where("user_id = ?", userID).First(&pref).Error; err != nil {
		pref = models.UserPreference{UserID: userID}
		database.DB.Create(&pref)
	}
	return pref
}

func traitGraphHasNode(userID, nodeID string) bool {
	graph := buildUserTraitGraph(userID, "")
	list, _ := graph["nodes"].([]traitGraphNode)
	for _, node := range list {
		if node.ID == nodeID {
			return true
		}
	}
	return false
}

func traitTruncate(text string, max int) string {
	runes := []rune(text)
	if len(runes) <= max {
		return text
	}
	return fmt.Sprintf("%s…", string(runes[:max]))
}
