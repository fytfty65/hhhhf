package handlers

import (
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// 这张图只能包含**当前用户自己**的偏好与反馈：别的用户的 target 不能串进来。
func TestBuildUserTraitGraphOnlyOwnData(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open("file:trait-graph-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.UserPreference{}, &models.FeedbackLog{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })

	prefs := []models.UserPreference{
		{
			UserID: "user-a", LikedTags: `["自然风光"]`, DislikedTags: `["排队"]`,
			BudgetTendency: "mid", NatureRatio: 0.5, CultureRatio: 0.3, FoodRatio: 0.2,
			FeedbackCount: 5,
		},
		{UserID: "user-b", LikedTags: `["主题乐园"]`, FeedbackCount: 2},
	}
	if err := db.Create(&prefs).Error; err != nil {
		t.Fatalf("seed prefs: %v", err)
	}
	logs := []models.FeedbackLog{
		{UserID: "user-a", Target: "九寨沟", Score: 1, Reason: "风景好"},
		{UserID: "user-a", Target: "九寨沟", Score: 1},
		{UserID: "user-b", Target: "迪士尼", Score: -1, Reason: "人太多"},
	}
	if err := db.Create(&logs).Error; err != nil {
		t.Fatalf("seed logs: %v", err)
	}

	graphA := buildUserTraitGraph("user-a", "")
	nodesA, ok := graphA["nodes"].([]traitGraphNode)
	if !ok {
		t.Fatalf("nodes type = %T", graphA["nodes"])
	}
	labels := map[string]bool{}
	for _, node := range nodesA {
		labels[node.Label] = true
	}
	if !labels["九寨沟"] {
		t.Fatalf("user-a 自己的反馈目标应出现在图谱里：%+v", nodesA)
	}
	if labels["迪士尼"] || labels["主题乐园"] {
		t.Fatalf("别的用户的数据串进了图谱：%+v", nodesA)
	}
	if empty, _ := graphA["empty"].(bool); empty {
		t.Fatalf("user-a 有画像与反馈，empty 应为 false")
	}
	if graphA["owner_scope"] != "self_only" {
		t.Fatalf("owner_scope = %v", graphA["owner_scope"])
	}

	// 没有画像也没有反馈的用户：只返回中心节点，不给默认配比/预算冒充特征。
	graphEmpty := buildUserTraitGraph("user-c", "")
	nodesEmpty, _ := graphEmpty["nodes"].([]traitGraphNode)
	if len(nodesEmpty) != 1 || nodesEmpty[0].ID != "self" {
		t.Fatalf("空用户不应生成特征节点：%+v", nodesEmpty)
	}
	if empty, _ := graphEmpty["empty"].(bool); !empty {
		t.Fatalf("空用户 empty 应为 true")
	}
}

// 生成过路书但没填偏好/没反馈的用户，也应该有特征：来自路书里的分类与标签。
// 被截断成 1 个节点的历史路书不能参与统计，否则会把保存故障当成偏好。
func TestBuildUserTraitGraphDerivesFromTrips(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open("file:trait-graph-trip-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.UserPreference{}, &models.FeedbackLog{}, &models.TripPlan{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })

	complete := `{"city":"杭州","routes":[
		{"name":"西溪湿地","category":"风景名胜","tags":["风景","自然"]},
		{"name":"灵隐寺","category":"风景名胜","tags":["古迹"]},
		{"name":"浙江省博物馆","category":"科教文化服务","tags":["文化","博物"]},
		{"name":"中国扇博物馆","category":"科教文化服务","tags":["文化"]},
		{"name":"楼外楼","category":"餐饮服务","tags":["美食"]},
		{"name":"河坊街小吃","category":"餐饮服务","tags":["美食"]},
		{"name":"杭州酒店","category":"住宿服务","tags":["住宿"],"is_hotel":true}
	]}`
	truncated := `{"city":"武威","routes":[{"name":"雷台汉墓","category":"","tags":["文化"]}]}`
	trips := []models.TripPlan{
		{ID: "trip-complete", UserID: "user-trip", Title: "杭州1日行程", Content: complete, Days: 1},
		{ID: "trip-truncated", UserID: "user-trip", Title: "武威1日行程", Content: truncated, Days: 1},
	}
	if err := db.Create(&trips).Error; err != nil {
		t.Fatalf("seed trips: %v", err)
	}

	graph := buildUserTraitGraph("user-trip", "")
	if empty, _ := graph["empty"].(bool); empty {
		t.Fatalf("有完整路书的用户不应是空图")
	}
	if samples, _ := graph["sample_count"].(int); samples != 1 {
		t.Fatalf("sample_count = %v，只应统计 1 份完整行程", graph["sample_count"])
	}
	nodes, _ := graph["nodes"].([]traitGraphNode)
	byID := map[string]traitGraphNode{}
	for _, node := range nodes {
		byID[node.ID] = node
	}
	for _, id := range []string{"nature", "culture", "food"} {
		node, ok := byID[id]
		if !ok || node.Source != "trip_plan" {
			t.Fatalf("缺少来自路书的维度节点 %s：%+v", id, nodes)
		}
	}
	if node, ok := byID["tag:文化"]; !ok || node.Source != "trip_plan" {
		t.Fatalf("路书里反复出现的标签应进入图谱：%+v", nodes)
	}
	if _, ok := byID["tag:住宿"]; ok {
		t.Fatalf("住宿类标签不应进入偏好统计：%+v", nodes)
	}
}

// 用户在图上做的权重新标定与移出，刷新后必须还在（存在 UserPreference 里）。
func TestBuildUserTraitGraphAppliesStoredOverrides(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open("file:trait-graph-override-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.UserPreference{}, &models.FeedbackLog{}, &models.TripPlan{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })

	pref := models.UserPreference{
		UserID:        "user-w",
		LikedTags:     `["自然风光","露营"]`,
		FeedbackCount: 3,
		TraitWeights:  `{"tag:自然风光":0.6}`,
		TraitHidden:   `["tag:露营"]`,
	}
	if err := db.Create(&pref).Error; err != nil {
		t.Fatalf("seed pref: %v", err)
	}

	graph := buildUserTraitGraph("user-w", "")
	nodes, _ := graph["nodes"].([]traitGraphNode)
	byID := map[string]traitGraphNode{}
	for _, node := range nodes {
		byID[node.ID] = node
	}
	if _, ok := byID["tag:露营"]; ok {
		t.Fatalf("移出的节点不应再出现：%+v", nodes)
	}
	kept, ok := byID["tag:自然风光"]
	if !ok {
		t.Fatalf("未移出的节点应保留：%+v", nodes)
	}
	if kept.Weight != 0.6 {
		t.Fatalf("节点权重应被用户设置覆盖为 0.6，实际 %v", kept.Weight)
	}
	edges, _ := graph["edges"].([]traitGraphEdge)
	for _, edge := range edges {
		if edge.To == "tag:露营" {
			t.Fatalf("移出节点的连线不应残留：%+v", edges)
		}
		if edge.To == "tag:自然风光" && edge.Weight != 0.6 {
			t.Fatalf("连线权重应跟随节点设置，实际 %v", edge.Weight)
		}
	}
}

// 房间共同交集必须来自真实房间成员的交集，不能靠前端猜。
func TestBuildUserTraitGraphRoomConsensus(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open("file:trait-graph-room-test?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if err := db.AutoMigrate(&models.UserPreference{}, &models.FeedbackLog{}, &models.TripPlan{}, &models.Room{}, &models.RoomMember{}); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })

	prefs := []models.UserPreference{
		{UserID: "user-a", LikedTags: `["自然风光","露营"]`, FeedbackCount: 3},
		{UserID: "user-b", LikedTags: `["自然风光","美食"]`, FeedbackCount: 3},
		{UserID: "user-c", LikedTags: `["小众"]`, FeedbackCount: 3},
	}
	if err := db.Create(&prefs).Error; err != nil {
		t.Fatalf("seed prefs: %v", err)
	}
	rooms := []models.Room{
		{ID: "room-1", InviteCode: "ABC123", Name: "双人房"},
		{ID: "room-2", InviteCode: "XYZ789", Name: "单人房"},
	}
	if err := db.Create(&rooms).Error; err != nil {
		t.Fatalf("seed rooms: %v", err)
	}
	members := []models.RoomMember{
		{RoomID: "room-1", UserID: "user-a"},
		{RoomID: "room-1", UserID: "user-b"},
		{RoomID: "room-2", UserID: "user-a"},
	}
	if err := db.Create(&members).Error; err != nil {
		t.Fatalf("seed members: %v", err)
	}

	graph := buildUserTraitGraph("user-a", "abc123")
	if code, _ := graph["room_code"].(string); code != "ABC123" {
		t.Fatalf("room_code = %v，应回填真实房间号", graph["room_code"])
	}
	if count, _ := graph["room_member_count"].(int); count != 2 {
		t.Fatalf("room_member_count = %v", graph["room_member_count"])
	}
	nodes, _ := graph["nodes"].([]traitGraphNode)
	byID := map[string]traitGraphNode{}
	for _, node := range nodes {
		byID[node.ID] = node
	}
	if shared := byID["tag:自然风光"]; !shared.SharedWithRoom || shared.RoomOverlapCount != 2 {
		t.Fatalf("两位成员都有的标签应标为房间共同交集：%+v", shared)
	}
	if solo := byID["tag:露营"]; solo.SharedWithRoom {
		t.Fatalf("只有自己有的标签不应标为共同交集：%+v", solo)
	}
	if shared, _ := graph["room_shared_count"].(int); shared != 1 {
		t.Fatalf("room_shared_count = %v，应为 1", graph["room_shared_count"])
	}

	// 房间里只有自己时，不给出任何共同交集，也不应把别人的标签带进来。
	soloGraph := buildUserTraitGraph("user-a", "XYZ789")
	soloNodes, _ := soloGraph["nodes"].([]traitGraphNode)
	for _, node := range soloNodes {
		if node.SharedWithRoom {
			t.Fatalf("单人房间不应有共同交集：%+v", soloNodes)
		}
	}
	if count, _ := soloGraph["room_member_count"].(int); count != 1 {
		t.Fatalf("单人房间 room_member_count = %v", soloGraph["room_member_count"])
	}
}
