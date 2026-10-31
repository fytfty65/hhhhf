package models

import "time"

// DeviceSession is one signed-in device. The refresh token is stored only as a
// hash, so a database leak cannot be replayed. Access tokens are short-lived and
// never written to the database.
type DeviceSession struct {
	ID               string     `gorm:"primaryKey" json:"id"`
	UserID           string     `gorm:"index;not null" json:"-"`
	RefreshTokenHash string     `gorm:"uniqueIndex;not null" json:"-"`
	UserAgent        string     `json:"user_agent,omitempty"`
	IP               string     `json:"ip,omitempty"`
	CreatedAt        time.Time  `json:"created_at"`
	LastUsedAt       time.Time  `json:"last_used_at"`
	ExpiresAt        time.Time  `gorm:"index" json:"expires_at"`
	RevokedAt        *time.Time `gorm:"index" json:"revoked_at,omitempty"`
}

// Active reports whether the session can still be refreshed.
func (s DeviceSession) Active(now time.Time) bool {
	return s.RevokedAt == nil && now.Before(s.ExpiresAt)
}

// PasswordResetToken backs the forgot-password flow. Only the hash is stored,
// and each token is single-use with a short TTL.
type PasswordResetToken struct {
	ID        string     `gorm:"primaryKey"`
	UserID    string     `gorm:"index;not null"`
	TokenHash string     `gorm:"uniqueIndex;not null"`
	ExpiresAt time.Time  `gorm:"index"`
	UsedAt    *time.Time
	CreatedAt time.Time
}
