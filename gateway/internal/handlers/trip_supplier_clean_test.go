package handlers

import (
	"strings"
	"testing"

	"gateway/internal/models"
)

func TestSanitizeTripContentClearsPlaceholdersOnly(t *testing.T) {
	raw := `{"route":[{"name":"宽窄巷子","rating":"暂无供应商数据","open_time":"08:00-22:00","cost_estimate":"暂无"}],"final_data":{"route":[{"name":"某酒店","rating":"4.8","cost_estimate":"暂无供应商数据","open_time":"暂无数据"}]}}`

	cleaned := sanitizeTripContent(raw)

	for _, gone := range []string{`"rating":"暂无供应商数据"`, `"cost_estimate":"暂无"`, `"open_time":"暂无数据"`, `"cost_estimate":"暂无供应商数据"`} {
		if strings.Contains(cleaned, gone) {
			t.Fatalf("占位没有清掉: %s\n%s", gone, cleaned)
		}
	}
	// 真值必须原样保留
	for _, kept := range []string{`"open_time":"08:00-22:00"`, `"rating":"4.8"`} {
		if !strings.Contains(cleaned, kept) {
			t.Fatalf("真值被误清: %s\n%s", kept, cleaned)
		}
	}
	if !strings.Contains(cleaned, `"cost_estimate":""`) {
		t.Fatalf("占位应清成空串: %s", cleaned)
	}
}

// 真实落库结构（App 把 cost_estimate 转成 price_label）：历史行程的占位就挂在这里。
func TestSanitizeTripContentClearsPriceLabelPlaceholder(t *testing.T) {
	raw := `{"city":"武威","summary":"所有cost_estimate均照抄底座原始数据","routes":[{"name":"沙漠雕塑公园","price_label":"暂无供应商数据"},{"name":"飞天假日酒店","price_label":"¥142"}]}`
	cleaned := sanitizeTripContent(raw)
	if strings.Contains(cleaned, `"price_label":"暂无供应商数据"`) {
		t.Fatalf("price_label 占位没有清掉: %s", cleaned)
	}
	if !strings.Contains(cleaned, `"price_label":"¥142"`) {
		t.Fatalf("真实价格被误清: %s", cleaned)
	}
}

func TestSanitizeTripContentRecursesIntoSlices(t *testing.T) {
	raw := `[{"day":1,"route":[{"name":"锦里","rating":"未核实","transport":"地铁"}]}]`
	cleaned := sanitizeTripContent(raw)
	if strings.Contains(cleaned, "未核实") {
		t.Fatalf("嵌套数组里的占位没有清掉: %s", cleaned)
	}
	if !strings.Contains(cleaned, `"transport":"地铁"`) {
		t.Fatalf("非供应商事实字段不应被改动: %s", cleaned)
	}
}

func TestSanitizeTripContentLeavesCleanPayloadByteIdentical(t *testing.T) {
	raw := `{"route":[{"name":"都江堰","rating":"4.7","open_time":"08:00-18:00","cost_estimate":"¥80"}]}`
	if got := sanitizeTripContent(raw); got != raw {
		t.Fatalf("无改动的行程不应被重新序列化\nwant: %s\ngot:  %s", raw, got)
	}
}

func TestSanitizeTripContentToleratesNonJSON(t *testing.T) {
	for _, raw := range []string{"", "   ", "纯文本行程描述", "{不是合法 JSON"} {
		if got := sanitizeTripContent(raw); got != raw {
			t.Fatalf("非 JSON 内容必须原样返回: %q -> %q", raw, got)
		}
	}
}

func TestSanitizeTripsSupplierFactsMutatesEveryTrip(t *testing.T) {
	trips := []models.TripPlan{
		{ID: "t1", Content: `{"route":[{"name":"A","rating":"暂无供应商数据"}]}`},
		{ID: "t2", Content: `{"route":[{"name":"B","rating":"4.6"}]}`},
	}
	sanitizeTripsSupplierFacts(trips)
	if strings.Contains(trips[0].Content, "暂无供应商数据") {
		t.Fatalf("历史行程占位没有清掉: %s", trips[0].Content)
	}
	if !strings.Contains(trips[1].Content, `"rating":"4.6"`) {
		t.Fatalf("真值被误改: %s", trips[1].Content)
	}
}
