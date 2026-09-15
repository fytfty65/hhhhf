package service

import (
	"sort"
	"time"
)

// PresenceTTL 协作光标在场态的默认存活时长：超过该时长无心跳则视为离开节点。
const PresenceTTL = 30 * time.Second

// NodePresence 协作在场记录：表示某成员当前正聚焦的行程节点，用于「头像悬浮 + 光标同步」。
type NodePresence struct {
	UserID     string    `json:"user_id"`
	Name       string    `json:"name"`
	Role       string    `json:"role"`
	AvatarURL  string    `json:"avatar_url"`
	AvatarSeed string    `json:"avatar_seed"`
	NodeKey    string    `json:"node_key"` // 行程节点稳定标识（景点名）
	UpdatedAt  time.Time `json:"-"`
}

// UpsertPresence 将成员在场态写入 map（按 UserID 去重，后到覆盖，单成员只保留一个光标）。
// 返回是否产生「内容层面」的变化（用于决定是否需要重算/广播；仅续期返回 false）。
func UpsertPresence(existing map[string]NodePresence, p NodePresence, now time.Time) bool {
	p.UpdatedAt = now
	old, ok := existing[p.UserID]
	if ok && old.NodeKey == p.NodeKey && old.Name == p.Name && old.AvatarURL == p.AvatarURL &&
		old.AvatarSeed == p.AvatarSeed && old.Role == p.Role {
		existing[p.UserID] = p // 仅刷新心跳时间
		return false
	}
	existing[p.UserID] = p
	return true
}

// RemovePresence 移除某成员在场态，返回是否确实存在并移除。
func RemovePresence(existing map[string]NodePresence, userID string) bool {
	if _, ok := existing[userID]; !ok {
		return false
	}
	delete(existing, userID)
	return true
}

// PruneExpiredPresence 清理超过 TTL 的过期在场记录（原地删除并返回）。
func PruneExpiredPresence(existing map[string]NodePresence, now time.Time, ttl time.Duration) map[string]NodePresence {
	for k, p := range existing {
		if now.Sub(p.UpdatedAt) > ttl {
			delete(existing, k)
		}
	}
	return existing
}

// PresenceSnapshot 将在场态汇成确定性排序的切片（先节点、后名称），供广播。
// 空 NodeKey（未聚焦任何节点）的记录被过滤，避免无意义头像悬浮。
func PresenceSnapshot(existing map[string]NodePresence) []NodePresence {
	out := make([]NodePresence, 0, len(existing))
	for _, p := range existing {
		if p.NodeKey == "" {
			continue
		}
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].NodeKey != out[j].NodeKey {
			return out[i].NodeKey < out[j].NodeKey
		}
		return out[i].Name < out[j].Name
	})
	return out
}

// ParsePresence 融合「客户端上报的身份信息」与「连接注册时的兜底身份」，得到最终在场记录。
// 客户端可在 payload 中补充 name/role/avatar_url/avatar_seed，缺失时回退到连接身份。
func ParsePresence(userID, fallbackName, fallbackRole, fallbackAvatarURL string, payload map[string]interface{}) NodePresence {
	p := NodePresence{UserID: userID, Name: fallbackName, Role: fallbackRole, AvatarURL: fallbackAvatarURL}
	if payload == nil {
		return p
	}
	if v, ok := payload["node_key"].(string); ok {
		p.NodeKey = v
	}
	if v, ok := payload["name"].(string); ok && v != "" {
		p.Name = v
	}
	if v, ok := payload["role"].(string); ok && v != "" {
		p.Role = v
	}
	if v, ok := payload["avatar_url"].(string); ok && v != "" {
		p.AvatarURL = v
	}
	if v, ok := payload["avatar_seed"].(string); ok && v != "" {
		p.AvatarSeed = v
	}
	return p
}