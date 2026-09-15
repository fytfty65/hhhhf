package handlers

import (
	"net/http"
	"strings"
	"unicode/utf8"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
)

func truncateRunes(value string, max int) string {
	if max <= 0 || value == "" {
		return ""
	}
	if utf8.RuneCountInString(value) <= max {
		return value
	}
	runes := []rune(value)
	return string(runes[:max])
}

// requireRoomMember centralizes room-level authorization for HTTP resources.
// Authentication alone must never grant access to another user's room.
func requireRoomMember(c *gin.Context, roomID string) (string, bool) {
	userID := CurrentUserID(c)
	roomID = strings.TrimSpace(roomID)
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
			"error": "未登录",
			"code":  "AUTH_REQUIRED",
		})
		return "", false
	}
	if roomID == "" {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
			"error": "缺少 room_id",
			"code":  "ROOM_ID_REQUIRED",
		})
		return "", false
	}
	if database.DB == nil {
		c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{
			"error": "数据服务暂不可用",
			"code":  "DATABASE_UNAVAILABLE",
		})
		return "", false
	}
	var member models.RoomMember
	if err := database.DB.Where("room_id = ? AND user_id = ?", roomID, userID).First(&member).Error; err != nil {
		// Deliberately use one response for a missing room and a non-member to
		// avoid turning this endpoint into a room-existence oracle.
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
			"error": "不是该房间成员",
			"code":  "ROOM_MEMBERSHIP_REQUIRED",
		})
		return "", false
	}
	return userID, true
}

// currentUserIDOrReject is useful for handlers that accept a legacy user_id
// field. The field is validated by AuthMiddleware, but the principal remains
// authoritative even if a handler is called without that middleware in a test.
func currentUserIDOrReject(c *gin.Context) (string, bool) {
	userID := CurrentUserID(c)
	if userID == "" {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{
			"error": "未登录",
			"code":  "AUTH_REQUIRED",
		})
		return "", false
	}
	return userID, true
}

// requireTripOwner verifies a persisted trip before exposing trip-scoped
// data. Some legacy flows use a room code as trip_id; those are authorized by
// requireRoomMember at the call site instead of this helper.
func requireTripOwner(c *gin.Context, tripID string) (string, bool) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return "", false
	}
	tripID = strings.TrimSpace(tripID)
	if tripID == "" {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
			"error": "缺少 trip_id",
			"code":  "TRIP_ID_REQUIRED",
		})
		return "", false
	}
	if database.DB == nil {
		c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{
			"error": "数据服务暂不可用",
			"code":  "DATABASE_UNAVAILABLE",
		})
		return "", false
	}
	var trip models.TripPlan
	if err := database.DB.Where("id = ?", tripID).First(&trip).Error; err != nil {
		c.AbortWithStatusJSON(http.StatusNotFound, gin.H{
			"error": "行程不存在",
			"code":  "TRIP_NOT_FOUND",
		})
		return "", false
	}
	if trip.UserID != userID {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
			"error": "无权访问该行程",
			"code":  "TRIP_ACCESS_DENIED",
		})
		return "", false
	}
	return userID, true
}

// requireTripOrRoomAccess authorizes resources that may be attached either to
// a persisted personal trip or to a live collaboration room.  The planning
// workspace historically used the room invite code as its temporary trip key,
// so accepting both forms keeps that flow durable without treating arbitrary
// city names as an authorization boundary.
func requireTripOrRoomAccess(c *gin.Context, resourceID string) (string, bool) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return "", false
	}
	resourceID = strings.TrimSpace(resourceID)
	if resourceID == "" {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{
			"error": "缺少 trip_id",
			"code":  "TRIP_ID_REQUIRED",
		})
		return "", false
	}
	if database.DB == nil {
		c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{
			"error": "数据服务暂不可用",
			"code":  "DATABASE_UNAVAILABLE",
		})
		return "", false
	}

	// A saved trip is always scoped to its owner.
	var trip models.TripPlan
	if err := database.DB.Where("id = ?", resourceID).First(&trip).Error; err == nil {
		if trip.UserID != userID {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
				"error": "无权访问该行程",
				"code":  "TRIP_ACCESS_DENIED",
			})
			return "", false
		}
		return userID, true
	}

	// A live room uses its opaque ID/invite code as the temporary resource key.
	var member models.RoomMember
	if err := database.DB.Where("room_id = ? AND user_id = ?", resourceID, userID).First(&member).Error; err == nil {
		return userID, true
	}

	// Do not disclose whether an arbitrary identifier belongs to another room.
	c.AbortWithStatusJSON(http.StatusNotFound, gin.H{
		"error": "行程或协作房间不存在",
		"code":  "TRIP_OR_ROOM_NOT_FOUND",
	})
	return "", false
}

// requireOptionalTripOrRoomAccess is used by feedback forms where a trip ID
// is optional. Empty IDs are valid for a live room-less survey, while a
// supplied ID must still pass the same ownership/membership boundary.
func requireOptionalTripOrRoomAccess(c *gin.Context, resourceID string) (string, bool) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return "", false
	}
	if strings.TrimSpace(resourceID) == "" {
		return userID, true
	}
	return requireTripOrRoomAccess(c, resourceID)
}
