package service

import "testing"

func TestTallyVotesDedupe(t *testing.T) {
	votes := []VariantVote{
		{VariantID: "A", UserID: "u1"},
		{VariantID: "B", UserID: "u2"},
		{VariantID: "B", UserID: "u1"}, // u1 从 A 改投 B（后票覆盖）
	}
	tally := TallyVotes(votes)
	if tally["A"] != 0 {
		t.Fatalf("u1 已改投，A 票数应为 0，got %d", tally["A"])
	}
	if tally["B"] != 2 {
		t.Fatalf("B 应得 2 票，got %d", tally["B"])
	}
}

func TestTallyVotesIgnoresEmpty(t *testing.T) {
	tally := TallyVotes([]VariantVote{
		{VariantID: "", UserID: "u1"},
		{VariantID: "A", UserID: ""},
	})
	if len(tally) != 0 {
		t.Fatalf("空投票应被忽略，got %v", tally)
	}
}

func TestWinningVariantClear(t *testing.T) {
	winner, votes, found := WinningVariant(map[string]int{"A": 3, "B": 1, "C": 2})
	if !found || winner != "A" || votes != 3 {
		t.Fatalf("应判定 A 胜出(3票)，got winner=%q votes=%d found=%v", winner, votes, found)
	}
}

func TestWinningVariantTie(t *testing.T) {
	_, _, found := WinningVariant(map[string]int{"A": 2, "B": 2})
	if found {
		t.Fatal("平票时应返回 found=false")
	}
}

func TestWinningVariantEmpty(t *testing.T) {
	winner, votes, found := WinningVariant(nil)
	if found || winner != "" || votes != 0 {
		t.Fatalf("无投票应返回空值，got %q %d %v", winner, votes, found)
	}
}