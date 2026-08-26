package service

import (
	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/google/uuid"
)

// handleAnnotation 处理行程节点级协作批注的 WebSocket 消息（新建/投票/解决），
// 持久化后即刻广播给同房间所有在线成员，实现 300ms 内实时同步与多设备一致。
func (c *Client) handleAnnotation(wsType string, payload interface{}) {
	m, ok := payload.(map[string]interface{})
	if !ok {
		m = map[string]interface{}{}
	}

	switch wsType {
	case "annotation_add":
		nodeKey := str(m["node_key"])
		content := str(m["content"])
		if nodeKey == "" || content == "" {
			return
		}
		ann := models.NodeAnnotation{
			ID:       uuid.New().String(),
			RoomID:   c.RoomID,
			TripID:   str(m["trip_id"]),
			NodeKey:  nodeKey,
			UserID:   c.UserID,
			Content:  content,
			ParentID: str(m["parent_id"]),
			Status:   "open",
		}
		if database.DB != nil {
			database.DB.Create(&ann)
		}
		c.Hub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: c.RoomID,
			UserID: c.UserID,
			Payload: map[string]interface{}{
				"action":     "add",
				"annotation": annotationDTO(ann),
			},
		}

	case "annotation_vote":
		annotationID := str(m["annotation_id"])
		value := intf(m["value"])
		if annotationID == "" || (value != 1 && value != -1) {
			return
		}
		if database.DB != nil {
			database.DB.Where("annotation_id = ? AND user_id = ?", annotationID, c.UserID).Delete(&models.NodeAnnotationVote{})
			database.DB.Create(&models.NodeAnnotationVote{AnnotationID: annotationID, UserID: c.UserID, Value: value})
		}
		c.Hub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: c.RoomID,
			UserID: c.UserID,
			Payload: map[string]interface{}{"action": "vote", "annotation_id": annotationID},
		}

	case "annotation_resolve":
		annotationID := str(m["annotation_id"])
		status := str(m["status"])
		if annotationID == "" || (status != "open" && status != "resolved") {
			return
		}
		if database.DB != nil {
			database.DB.Model(&models.NodeAnnotation{}).Where("id = ?", annotationID).Update("status", status)
		}
		c.Hub.Broadcast <- models.WSMessage{
			Type:   "annotation_sync",
			RoomID: c.RoomID,
			UserID: c.UserID,
			Payload: map[string]interface{}{"action": "resolve", "annotation_id": annotationID, "status": status},
		}
	}
}

// annotationDTO 将批注转为对外传输结构（含作者昵称，便于前端即时展示）。
func annotationDTO(a models.NodeAnnotation) map[string]interface{} {
	author := a.UserID
	if database.DB != nil {
		var u models.User
		if err := database.DB.Where("id = ?", a.UserID).First(&u).Error; err == nil {
			if u.Nickname != "" {
				author = u.Nickname
			} else if u.Username != "" {
				author = u.Username
			}
		}
	}
	return map[string]interface{}{
		"id":         a.ID,
		"room_id":    a.RoomID,
		"trip_id":    a.TripID,
		"node_key":   a.NodeKey,
		"user_id":    a.UserID,
		"author":     author,
		"content":    a.Content,
		"parent_id":  a.ParentID,
		"status":     a.Status,
		"created_at": a.CreatedAt,
	}
}

// str 安全读取 map 中的字符串字段。
func str(v interface{}) string {
	s, ok := v.(string)
	if !ok {
		return ""
	}
	return s
}

// intf 安全读取 map 中的整数字段（兼容 JSON 数字默认 float64）。
func intf(v interface{}) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	case float32:
		return int(n)
	}
	return 0
}