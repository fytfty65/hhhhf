package service

import "testing"

func TestSeasonOfBoundaries(t *testing.T) {
	cases := map[int]string{
		3:  SeasonSpring,
		5:  SeasonSpring,
		6:  SeasonSummer,
		8:  SeasonSummer,
		9:  SeasonAutumn,
		11: SeasonAutumn,
		12: SeasonWinter,
		1:  SeasonWinter,
		2:  SeasonWinter,
	}
	for m, want := range cases {
		if got := SeasonOf(m); got != want {
			t.Fatalf("月份 %d 期望 %s，got %s", m, want, got)
		}
	}
}

func TestSeasonRelevanceHit(t *testing.T) {
	// 冰雪景点冬季相关度应显著高于夏季
	win := SeasonRelevance("哈尔滨冰雪大世界", "冰雪奇缘", nil, SeasonWinter)
	sum := SeasonRelevance("哈尔滨冰雪大世界", "冰雪奇缘", nil, SeasonSummer)
	if win <= sum {
		t.Fatalf("冰雪景点冬季相关度应高于夏季，win=%v sum=%v", win, sum)
	}
	if win > 1.0 || win < 0.5 {
		t.Fatalf("季节相关度应限定在合理区间，win=%v", win)
	}
}

func TestSeasonRelevanceNeutral(t *testing.T) {
	// 四季皆宜地标无季节关键词时给中性偏低分（0.3）
	if got := SeasonRelevance("故宫博物院", "皇家宫殿", nil, SeasonWinter); got != 0.3 {
		t.Fatalf("无季节特征景点应为中性 0.3，got %v", got)
	}
}

func TestInteractionWeightMonotonic(t *testing.T) {
	if InteractionWeight(0, 0) != 0 {
		t.Fatalf("零互动热度应为 0，got %v", InteractionWeight(0, 0))
	}
	if InteractionWeight(1, 0) >= InteractionWeight(0, 1) {
		t.Fatal("一次正反馈权重应高于一次点击（likes 权重更高）")
	}
	if InteractionWeight(10, 10) <= InteractionWeight(1, 1) {
		t.Fatal("热度应随互动量单调递增")
	}
}

func TestRankHotspotsSeasonBoost(t *testing.T) {
	// 零埋点下，季节相关度应决定排序：冬季冰雪景点居首
	hotspots := []Hotspot{
		{Name: "哈尔滨冰雪大世界", Desc: "冰雪"},
		{Name: "故宫博物院", Desc: "宫殿"},
	}
	ranked := RankHotspots(hotspots, nil, nil, SeasonWinter)
	if len(ranked) != 2 {
		t.Fatalf("应返回 2 条结果，got %d", len(ranked))
	}
	if ranked[0].Name != "哈尔滨冰雪大世界" {
		t.Fatalf("零埋点下冬季应让冰雪景点居首，got %v", ranked[0].Name)
	}
}

func TestRankHotspotsEngagementOverrides(t *testing.T) {
	// 埋点热度极高时，非季节地标可反超季节相关度（权重生效）
	hotspots := []Hotspot{
		{Name: "哈尔滨冰雪大世界", Desc: "冰雪"},
		{Name: "故宫博物院", Desc: "宫殿"},
	}
	clicks := map[string]int64{"故宫博物院": 1000}
	likes := map[string]int64{"故宫博物院": 500}
	ranked := RankHotspots(hotspots, clicks, likes, SeasonWinter)
	if ranked[0].Name != "故宫博物院" {
		t.Fatalf("埋点热度极高时应反超，got %v", ranked[0].Name)
	}
	// 埋点数据透传
	for _, r := range ranked {
		if r.Name == "故宫博物院" && (r.Clicks != 1000 || r.Likes != 500) {
			t.Fatalf("埋点数据未正确透传，%+v", r)
		}
	}
}

func TestRankHotspotsEmpty(t *testing.T) {
	if got := RankHotspots(nil, nil, nil, SeasonSpring); len(got) != 0 {
		t.Fatalf("空输入应返回空切片，got %v", got)
	}
}