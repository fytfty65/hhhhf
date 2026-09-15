package service

import (
	"testing"

	"gateway/internal/contracts"
)

func TestRankCandidatesPrefersExplicitInterestWithoutChangingSource(t *testing.T) {
	candidates := []contracts.Candidate{
		{Name: "博物馆", Score: 0.80, Tags: []string{"文化"}, Source: contracts.Source{Provider: "supplier"}},
		{Name: "本地美食街", Score: 0.78, Tags: []string{"美食"}, Source: contracts.Source{Provider: "supplier"}},
	}
	ranked := RankCandidates(candidates, map[string]any{"interest": "美食"})
	if ranked[0].Name != "本地美食街" {
		t.Fatalf("expected preference-aware ranking, got %+v", ranked)
	}
	if ranked[0].Source.Provider != "supplier" {
		t.Fatal("ranking must preserve provider provenance")
	}
}
