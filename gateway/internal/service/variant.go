package service

// VariantVote 归一化的方案投票样本（纯函数层不依赖数据库模型，便于单元测试）。
type VariantVote struct {
	VariantID string
	UserID    string
}

// TallyVotes 统计每套方案获得的票数。
// 遵循「一人一票、后票覆盖前票」规则：同一用户重复投票时仅保留最后一次选择。
func TallyVotes(votes []VariantVote) map[string]int {
	latest := make(map[string]string) // userID -> variantID（最新一票）
	for _, v := range votes {
		if v.UserID == "" || v.VariantID == "" {
			continue
		}
		latest[v.UserID] = v.VariantID
	}

	tally := make(map[string]int)
	for _, variantID := range latest {
		tally[variantID]++
	}
	return tally
}

// WinningVariant 从票数统计中选出得票最高的方案。
// 返回胜出方案 ID 与其票数；若出现平票（多方案并列最高）或无人投票，found 为 false。
// 平票时 winnerID 返回第一个达到最高票的方案（结果不保证确定，由调用方决定仲裁策略）。
func WinningVariant(tally map[string]int) (winnerID string, votes int, found bool) {
	if len(tally) == 0 {
		return "", 0, false
	}

	best := -1
	tie := false
	for id, v := range tally {
		if v > best {
			best = v
			winnerID = id
			tie = false
		} else if v == best {
			tie = true
		}
	}
	if best <= 0 || tie {
		return winnerID, best, false
	}
	return winnerID, best, true
}