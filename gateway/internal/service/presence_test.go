package service

import (
	"testing"
	"time"
)

func newPresence(userID, nodeKey string) NodePresence {
	return NodePresence{UserID: userID, Name: userID, NodeKey: nodeKey, UpdatedAt: time.Now()}
}

func TestUpsertPresenceDedupByUser(t *testing.T) {
	m := map[string]NodePresence{}
	now := time.Now()
	if !UpsertPresence(m, newPresence("u1", "故宫"), now) {
		t.Fatal("首次写入应返回变化")
	}
	if m["u1"].NodeKey != "故宫" {
		t.Fatalf("节点未写入，got %+v", m["u1"])
	}
	// 同一用户换节点：仍是同一 Key，后到覆盖
	if !UpsertPresence(m, newPresence("u1", "长城"), now.Add(time.Second)) {
		t.Fatal("切换节点应返回变化")
	}
	if len(m) != 1 || m["u1"].NodeKey != "长城" {
		t.Fatalf("同一用户应仅保留一个光标，got %+v", m)
	}
}

func TestUpsertPresenceHeartbeatNoChange(t *testing.T) {
	m := map[string]NodePresence{}
	now := time.Now()
	UpsertPresence(m, newPresence("u1", "故宫"), now)
	if UpsertPresence(m, newPresence("u1", "故宫"), now.Add(time.Second)) {
		t.Fatal("相同内容仅续期，不应返回变化")
	}
}

func TestRemovePresence(t *testing.T) {
	m := map[string]NodePresence{}
	UpsertPresence(m, newPresence("u1", "故宫"), time.Now())
	if !RemovePresence(m, "u1") {
		t.Fatal("应成功移除已存在的用户")
	}
	if RemovePresence(m, "u1") {
		t.Fatal("重复移除应返回 false")
	}
}

func TestPruneExpiredPresence(t *testing.T) {
	now := time.Now()
	m := map[string]NodePresence{
		"fresh":   {UserID: "fresh", NodeKey: "A", UpdatedAt: now},
		"expired": {UserID: "expired", NodeKey: "B", UpdatedAt: now.Add(-time.Minute)},
	}
	PruneExpiredPresence(m, now, 10*time.Second)
	if _, ok := m["fresh"]; !ok {
		t.Fatal("未过期记录不应被清理")
	}
	if _, ok := m["expired"]; ok {
		t.Fatal("过期记录应被清理")
	}
}

func TestPresenceSnapshotOrderAndFilter(t *testing.T) {
	m := map[string]NodePresence{
		"b": {UserID: "b", Name: "乙", NodeKey: "长城"},
		"a": {UserID: "a", Name: "甲", NodeKey: "故宫"},
		"x": {UserID: "x", Name: "空", NodeKey: ""}, // 空节点应被过滤
	}
	snap := PresenceSnapshot(m)
	if len(snap) != 2 {
		t.Fatalf("空节点应被过滤，期望 2 条，got %d", len(snap))
	}
	if snap[0].Name != "甲" || snap[1].Name != "乙" {
		t.Fatalf("应按节点排序（故宫<长城），got %+v", snap)
	}
}

func TestParsePresenceFallbackAndOverride(t *testing.T) {
	// 无 payload：回退连接身份
	p := ParsePresence("u1", "昵称", "美食家", "/a.png", nil)
	if p.Name != "昵称" || p.Role != "美食家" || p.AvatarURL != "/a.png" {
		t.Fatalf("缺失 payload 应回退连接身份，got %+v", p)
	}
	// 有 payload：覆盖身份并补充 node_key
	p = ParsePresence("u1", "昵称", "美食家", "/a.png", map[string]interface{}{
		"node_key":    "外滩",
		"name":        "小美",
		"avatar_seed": "seed123",
	})
	if p.NodeKey != "外滩" || p.Name != "小美" || p.AvatarSeed != "seed123" || p.AvatarURL != "/a.png" {
		t.Fatalf("payload 应覆盖身份，got %+v", p)
	}
}