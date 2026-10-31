package database

import (
	"fmt"

	"gorm.io/gorm"
)

// compositeIndex describes one index added by schema version 21. The tables grow
// with usage (chat history, planning events, recommendation exposure, feedback,
// community feed), and every read below filters on one column and sorts on
// created_at. A single-column index leaves SQLite to fetch and sort the matching
// rows; the composite index serves both operations in one pass, which keeps the
// single writer's lock hold time down.
type compositeIndex struct {
	name    string
	table   string
	columns string
}

func compositeIndexes() []compositeIndex {
	return []compositeIndex{
		// Retention sweeps delete `created_at < cutoff` on these four tables.
		{"idx_messages_created_at", "messages", "created_at"},
		{"idx_planning_events_created_at", "planning_events", "created_at"},
		{"idx_recommendation_events_created_at", "recommendation_events", "created_at"},
		{"idx_feedback_logs_created_at", "feedback_logs", "created_at"},

		// Room chat history: WHERE room_id = ? ORDER BY created_at (limit).
		{"idx_messages_room_created", "messages", "room_id, created_at"},

		// Planning analytics: per-user, per-type, per-room and per-trip windows.
		{"idx_planning_events_user_created", "planning_events", "user_id, created_at"},
		{"idx_planning_events_type_created", "planning_events", "event_type, created_at"},
		{"idx_planning_events_room_created", "planning_events", "room_id, created_at"},
		{"idx_planning_events_trip_created", "planning_events", "trip_id, created_at"},

		// A/B metrics: per-user history and per-variant windows.
		{"idx_recommendation_events_user_created", "recommendation_events", "user_id, created_at"},
		{"idx_recommendation_events_variant_created", "recommendation_events", "variant, created_at"},

		// Feedback aggregation by room (evolution memory) and by user (profile).
		{"idx_feedback_logs_room_created", "feedback_logs", "room_id, created_at"},
		{"idx_feedback_logs_user_created", "feedback_logs", "user_id, created_at"},

		// Community feed: newest first, optionally filtered by destination.
		{"idx_community_posts_created_at", "community_posts", "created_at"},
		{"idx_community_posts_city_created", "community_posts", "dest_city, created_at"},
		{"idx_comments_post_created", "comments", "post_id, created_at"},
	}
}

// migrateCompositeIndexes is non-destructive: CREATE INDEX only builds a new
// B-tree, so no backup is required (unlike the v20 table rebuild).
func migrateCompositeIndexes(tx *gorm.DB) error {
	for _, index := range compositeIndexes() {
		if !tableExists(tx, index.table) {
			continue
		}
		statement := fmt.Sprintf(
			"CREATE INDEX IF NOT EXISTS %s ON %s(%s)",
			index.name, index.table, index.columns,
		)
		if err := tx.Exec(statement).Error; err != nil {
			return fmt.Errorf("create index %s: %w", index.name, err)
		}
	}
	return nil
}
