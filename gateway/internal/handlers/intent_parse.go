package handlers

import (
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// 结构化解析支持的偏好标签，与移动端表单的选项保持一致。
var intentPreferenceLabels = []string{
	"美食", "自然风光", "人文历史", "亲子", "摄影", "慢节奏", "购物", "夜生活",
}

var intentPreferenceAliases = map[string]string{
	"美食":  "美食",
	"好吃":  "美食",
	"吃":   "美食",
	"自然":  "自然风光",
	"风景":  "自然风光",
	"山水":  "自然风光",
	"历史":  "人文历史",
	"文化":  "人文历史",
	"人文":  "人文历史",
	"博物馆": "人文历史",
	"亲子":  "亲子",
	"遛娃":  "亲子",
	"儿童":  "亲子",
	"摄影":  "摄影",
	"拍照":  "摄影",
	"出片":  "摄影",
	"慢节奏": "慢节奏",
	"悠闲":  "慢节奏",
	"休闲":  "慢节奏",
	"购物":  "购物",
	"逛街":  "购物",
	"夜生活": "夜生活",
	"夜景":  "夜生活",
}

// 已知城市：只做保守匹配，识别不到就让客户端提示用户补填。
var intentCityNames = []string{
	"成都", "杭州", "西安", "上海", "北京", "重庆", "南京", "苏州", "厦门", "广州",
	"深圳", "武汉", "长沙", "洛阳", "青岛", "大理", "丽江", "昆明", "三亚", "桂林",
	"哈尔滨", "天津", "郑州", "开封", "绍兴", "扬州", "泉州", "潮州", "顺德", "乐山",
	"敦煌", "喀什", "拉萨", "乌鲁木齐", "西宁", "银川", "贵阳", "南宁", "福州", "南昌",
	"宁波", "无锡", "常州", "徐州", "烟台", "威海", "大连", "沈阳", "长春", "呼和浩特",
	"兰州", "太原", "石家庄", "济南", "合肥", "常州", "嘉兴", "湖州", "台州", "温州",
	"珠海", "佛山", "东莞", "中山", "惠州", "汕头", "湛江", "海口", "文昌", "万宁",
	"张家界", "凤凰", "黄山", "婺源", "景德镇", "上饶", "九江", "宜昌", "襄阳", "恩施",
	"都江堰", "峨眉山", "九寨沟", "西昌", "香格里拉", "腾冲", "普洱", "北海", "阳朔",
}

var (
	intentDaysPattern     = regexp.MustCompile(`([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*天`)
	intentDatePattern     = regexp.MustCompile(`([0-9]{4})[-/年]([0-9]{1,2})[-/月]([0-9]{1,2})日?`)
	intentMonthDayPattern = regexp.MustCompile(`([0-9]{1,2})月([0-9]{1,2})日`)
	intentOriginPattern   = regexp.MustCompile(`从([\p{Han}]{2,8})(?:出发|去|到)`)
)

func chineseNumber(raw string) (int, bool) {
	if value, err := strconv.Atoi(raw); err == nil {
		return value, true
	}
	digits := map[rune]int{'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
		'六': 6, '七': 7, '八': 8, '九': 9}
	runes := []rune(raw)
	if len(runes) == 1 {
		if value, ok := digits[runes[0]]; ok {
			return value, true
		}
		return 0, false
	}
	if len(runes) == 2 && runes[0] == '十' {
		if value, ok := digits[runes[1]]; ok {
			return 10 + value, true
		}
	}
	if len(runes) == 2 && runes[1] == '十' {
		if value, ok := digits[runes[0]]; ok {
			return value * 10, true
		}
	}
	if len(runes) == 3 && runes[1] == '十' {
		tens, okTen := digits[runes[0]]
		ones, okOne := digits[runes[2]]
		if okTen && okOne {
			return tens*10 + ones, true
		}
	}
	return 0, false
}

func matchCity(text string) string {
	best := ""
	for _, city := range intentCityNames {
		if strings.Contains(text, city) && len([]rune(city)) > len([]rune(best)) {
			best = city
		}
	}
	return best
}

func matchPreferences(text string) []string {
	seen := make(map[string]bool)
	result := make([]string, 0, len(intentPreferenceLabels))
	for _, label := range intentPreferenceLabels {
		if strings.Contains(text, label) {
			seen[label] = true
			result = append(result, label)
		}
	}
	for alias, label := range intentPreferenceAliases {
		if seen[label] {
			continue
		}
		if strings.Contains(text, alias) {
			seen[label] = true
			result = append(result, label)
		}
	}
	// 固定输出顺序，避免 map 迭代带来的抖动。
	ordered := make([]string, 0, len(result))
	for _, label := range intentPreferenceLabels {
		if seen[label] {
			ordered = append(ordered, label)
		}
	}
	return ordered
}

func matchStartDate(text string, now time.Time) string {
	if match := intentDatePattern.FindStringSubmatch(text); len(match) == 4 {
		year, _ := strconv.Atoi(match[1])
		month, _ := strconv.Atoi(match[2])
		day, _ := strconv.Atoi(match[3])
		if date, ok := safeDate(year, month, day); ok {
			return date
		}
	}
	if match := intentMonthDayPattern.FindStringSubmatch(text); len(match) == 3 {
		month, _ := strconv.Atoi(match[1])
		day, _ := strconv.Atoi(match[2])
		year := now.Year()
		if date, ok := safeDate(year, month, day); ok {
			if parsed, err := time.Parse("2006-01-02", date); err == nil && parsed.Before(now.Truncate(24*time.Hour)) {
				year++
				if next, okNext := safeDate(year, month, day); okNext {
					return next
				}
			}
			return date
		}
	}
	offsets := map[string]int{"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
	for word, offset := range offsets {
		if strings.Contains(text, word) {
			return now.AddDate(0, 0, offset).Format("2006-01-02")
		}
	}
	return ""
}

func safeDate(year, month, day int) (string, bool) {
	if year < 2000 || year > 2100 || month < 1 || month > 12 || day < 1 || day > 31 {
		return "", false
	}
	value := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if value.Month() != time.Month(month) || value.Day() != day {
		return "", false
	}
	return value.Format("2006-01-02"), true
}

func matchOrigin(text string) string {
	match := intentOriginPattern.FindStringSubmatch(text)
	if len(match) != 2 {
		return ""
	}
	// 只有候选地名本身在已知城市里才采纳，避免把「从家里出发」当成城市。
	for _, city := range intentCityNames {
		if strings.Contains(match[1], city) {
			return city
		}
	}
	return ""
}

// ParsePlanningIntentHandler 把一句话描述解析为结构化表单字段。
// 只返回可确定的内容，未识别的字段留空，由客户端提示用户补填。
func ParsePlanningIntentHandler(c *gin.Context) {
	var req struct {
		Text string `json:"text"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_REQUEST", "请求参数错误", nil)
		return
	}
	text := strings.TrimSpace(req.Text)
	if len([]rune(text)) == 0 {
		planningError(c, http.StatusBadRequest, "EMPTY_TEXT", "请先描述你的行程", nil)
		return
	}
	if len([]rune(text)) > 200 {
		text = string([]rune(text)[:200])
	}

	city := matchCity(text)
	origin := matchOrigin(text)
	if origin == city {
		origin = ""
	}

	days := 0
	if match := intentDaysPattern.FindStringSubmatch(text); len(match) == 2 {
		if value, ok := chineseNumber(match[1]); ok {
			days = value
		}
	}
	if days < 1 {
		days = 0
	}
	if days > 30 {
		days = 30
	}

	planningData(c, gin.H{
		"city":        city,
		"origin":      origin,
		"days":        days,
		"start_date":  matchStartDate(text, time.Now()),
		"preferences": matchPreferences(text),
	})
}
