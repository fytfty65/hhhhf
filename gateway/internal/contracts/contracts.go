package contracts

import "time"

// Error is the stable error payload shared by Gateway and frontend clients.
type Error struct {
	Code      string            `json:"code"`
	Message   string            `json:"message"`
	RequestID string            `json:"request_id,omitempty"`
	Details   map[string]string `json:"details,omitempty"`
}

type Meta struct {
	RequestID string    `json:"request_id,omitempty"`
	Timestamp time.Time `json:"timestamp"`
	Version   string    `json:"contract_version"`
}

type Envelope struct {
	Data  any    `json:"data,omitempty"`
	Meta  Meta   `json:"meta"`
	Error *Error `json:"error,omitempty"`
}

// PlanningContext is the canonical identity and constraint contract. IDs are
// mandatory at the boundary; display names are never used as authorization
// keys or persistence identifiers.
type PlanningContext struct {
	UserID      string         `json:"user_id"`
	TripID      string         `json:"trip_id"`
	RoomID      string         `json:"room_id,omitempty"`
	Destination string         `json:"destination"`
	Origin      string         `json:"origin,omitempty"`
	StartDate   string         `json:"start_date,omitempty"`
	EndDate     string         `json:"end_date,omitempty"`
	Travelers   int            `json:"travelers,omitempty"`
	Budget      float64        `json:"budget,omitempty"`
	Currency    string         `json:"currency,omitempty"`
	Preferences map[string]any `json:"preferences,omitempty"`
	// HardConstraints and SoftPreferences keep the digital-twin contract
	// extensible without overloading display-only preference fields.
	HardConstraints map[string]any `json:"hard_constraints,omitempty"`
	SoftPreferences map[string]any `json:"soft_preferences,omitempty"`
	Profile         map[string]any `json:"profile,omitempty"`
	RealtimeState   map[string]any `json:"realtime_state,omitempty"`
}

type Source struct {
	Provider  string    `json:"provider"`
	Retrieved time.Time `json:"retrieved_at"`
	Estimated bool      `json:"estimated"`
}

// Evidence is an auditable explanation for a candidate field. Values are
// copied from a supplier response or explicitly marked estimated; the model
// layer must never invent live travel facts.
type Evidence struct {
	Field      string    `json:"field"`
	Value      any       `json:"value,omitempty"`
	Source     string    `json:"source"`
	ObservedAt time.Time `json:"observed_at"`
	Estimated  bool      `json:"estimated"`
}

type CandidateRef struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Reason string `json:"reason,omitempty"`
}

type Candidate struct {
	ID           string            `json:"id"`
	Name         string            `json:"name"`
	Score        float64           `json:"score"`
	Price        float64           `json:"price,omitempty"`
	Duration     int               `json:"duration_minutes,omitempty"`
	Tags         []string          `json:"tags,omitempty"`
	Reasons      []string          `json:"reasons,omitempty"`
	Constraints  []string          `json:"constraints,omitempty"`
	Source       Source            `json:"source"`
	FieldSources map[string]string `json:"field_sources,omitempty"`
	Evidence     []Evidence        `json:"evidence,omitempty"`
	Confidence   float64           `json:"confidence"`
	// FreshnessSeconds is measured from Source.Retrieved at response time.
	FreshnessSeconds int            `json:"freshness_seconds"`
	Alternatives     []CandidateRef `json:"alternatives,omitempty"`
	Extra            map[string]any `json:"extra,omitempty"`
}
