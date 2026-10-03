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

func TestRankCandidatesWithBehaviorUsesBoundedModuleSignal(t *testing.T) {
	candidates := []contracts.Candidate{
		{Name: "铁路方案", Score: 0.80, Tags: []string{"交通"}},
		{Name: "航班方案", Score: 0.83, Tags: []string{"交通"}},
	}
	ranked := RankCandidatesWithBehavior(candidates, nil, map[string]any{"module": "transport", "verified": 4, "adopted": 4})
	if ranked[0].Name != "航班方案" {
		t.Fatalf("aggregate behavior signal must not override provider ordering, got %+v", ranked)
	}
	other := RankCandidatesWithBehavior(candidates, nil, map[string]any{"module": "dining", "verified": 4, "adopted": 4})
	if other[0].Name != "航班方案" {
		t.Fatalf("non-matching module must not affect transport candidates, got %+v", other)
	}
}

func TestRankCandidatesWithBehaviorDoesNotOverrideLargeProviderGap(t *testing.T) {
	candidates := []contracts.Candidate{{Name: "铁路方案", Score: 0.40, Tags: []string{"交通"}}, {Name: "航班方案", Score: 0.90, Tags: []string{"交通"}}}
	ranked := RankCandidatesWithBehavior(candidates, nil, map[string]any{"module": "transport", "verified": 4, "adopted": 4})
	if ranked[0].Name != "航班方案" {
		t.Fatalf("behavior signal must remain bounded, got %+v", ranked)
	}
}

func TestRankCandidatesWithBehaviorFallsBackWhenSamplesInsufficient(t *testing.T) {
	candidates := []contracts.Candidate{{Name: "铁路方案", Score: 0.80, Tags: []string{"交通"}}, {Name: "航班方案", Score: 0.83, Tags: []string{"交通"}}}
	baseline := RankCandidates(candidates, nil)
	ranked := RankCandidatesWithBehavior(candidates, nil, map[string]any{"module": "transport", "enabled": false, "verified": 4, "adopted": 4})
	if ranked[0].Name != baseline[0].Name {
		t.Fatalf("insufficient evidence must preserve baseline ranking: baseline=%+v ranked=%+v", baseline, ranked)
	}
}
