package handlers

import (
	"encoding/json"
	"net/http"
	"strings"

	"gateway/internal/service"

	"github.com/gin-gonic/gin"
)

// 参考路线画像：用户粘贴一条自己喜欢的路线（例如小红书帖子正文），
// 由 ai-service 抽成**结构化特征**（类别配比/节奏/时段/消费档/交通/住宿…），
// 网关保存抽取结果，用于"用户没提出更多需求时按这份偏好生成"。
//
// 三条硬约束（都写进了文案与测试）：
//  1. **不保存整篇原文**：原文只在抽取的那一次请求里存在。但必须说清楚 ——
//     默认会保存每个特征的 **evidence（出处片段，约 ±14 字）**，否则界面上无法解释"凭什么这么排"；
//     不想留片段就传 `keep_evidence=false`，这时只存特征值。
//     （实测教训：最初的文案写"不保存原文"，可 evidence 本身就是原文片段 —— 与事实不符，已改。）
//  2. 抽不到的特征保留 unknown，不用默认值冒充"学到了"（判断在 Python 侧，纯函数有单测）；
//  3. 用户当轮的明确诉求优先于画像（同样在 Python 侧，见 reference_profile_hint）。

type referenceRouteRequest struct {
	Text        string `json:"text"`
	SourceLabel string `json:"source_label"`
	// 是否保存"出处片段"（默认 true：留着才能在界面上解释依据）；false 则只存特征值
	KeepEvidence *bool `json:"keep_evidence"`
}

const referenceRouteMaxChars = 20000

// stripEvidence 去掉出处片段，只留特征值（用户选择不留片段时用）。
func stripEvidence(profile map[string]interface{}) map[string]interface{} {
	cleaned := make(map[string]interface{}, len(profile))
	for key, value := range profile {
		if key == "evidence" {
			continue
		}
		cleaned[key] = value
	}
	return cleaned
}

// SaveReferenceRouteHandler POST /api/v1/user/preference/reference-route
func SaveReferenceRouteHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	var req referenceRouteRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	text := strings.TrimSpace(req.Text)
	if text == "" {
		planningError(c, http.StatusBadRequest, "REFERENCE_TEXT_REQUIRED", "请粘贴参考路线的正文（整篇原文不会被保存，只保存抽取出的特征与出处片段）", nil)
		return
	}
	if len([]rune(text)) > referenceRouteMaxChars {
		planningError(c, http.StatusBadRequest, "REFERENCE_TEXT_TOO_LONG", "参考路线正文过长，请精简后再试", nil)
		return
	}

	// 已有画像一起送过去，让 ai-service 按同一套口径合并（多来源参考）
	existing := service.ReferenceProfilePayload(userID)
	body, err := json.Marshal(gin.H{
		"text":             text,
		"source_label":     strings.TrimSpace(req.SourceLabel),
		"existing_profile": existing,
	})
	if err != nil {
		planningError(c, http.StatusInternalServerError, "REFERENCE_ENCODE_FAILED", "参考路线处理失败", nil)
		return
	}

	data, status, err := callAI(c, "/api/v1/profile/reference-route", body)
	if err != nil || status != http.StatusOK {
		// 如实报告：抽取服务不可用时不能让用户以为"已经学到了"
		planningError(c, http.StatusBadGateway, "REFERENCE_EXTRACT_UNAVAILABLE", "参考路线解析服务暂不可用，请稍后重试", nil)
		return
	}

	var parsed struct {
		Profile map[string]interface{} `json:"profile"`
		Hints   map[string]interface{} `json:"hints"`
		Note    string                 `json:"note"`
	}
	if err := json.Unmarshal(data, &parsed); err != nil || len(parsed.Profile) == 0 {
		planningError(c, http.StatusBadGateway, "REFERENCE_EXTRACT_UNPARSABLE", "参考路线解析结果不可用", nil)
		return
	}

	encoded, err := json.Marshal(parsed.Profile)
	if err != nil {
		planningError(c, http.StatusInternalServerError, "REFERENCE_ENCODE_FAILED", "参考路线处理失败", nil)
		return
	}
	keepEvidence := true
	if req.KeepEvidence != nil {
		keepEvidence = *req.KeepEvidence
	}
	if !keepEvidence {
		encoded, err = json.Marshal(stripEvidence(parsed.Profile))
		if err != nil {
			planningError(c, http.StatusInternalServerError, "REFERENCE_ENCODE_FAILED", "参考路线处理失败", nil)
			return
		}
	}
	count := 1
	if raw, ok := parsed.Profile["reference_count"].(float64); ok && int(raw) > 0 {
		count = int(raw)
	}
	if err := service.StoreReferenceProfile(userID, string(encoded), count); err != nil {
		planningError(c, http.StatusInternalServerError, "REFERENCE_STORE_FAILED", "参考路线保存失败", nil)
		return
	}

	// 如实告诉用户我们存了什么：默认存"特征 + 出处片段"（片段是为了能解释依据）
	note := "已保存抽取出的偏好特征，以及每个特征的出处片段（用于在界面上解释依据）；整篇原文不会被保存。想改直接说（诉求会覆盖画像）。"
	if !keepEvidence {
		note = "已保存抽取出的偏好特征（按你的选择未保存出处片段）；整篇原文不会被保存。想改直接说（诉求会覆盖画像）。"
	}
	planningData(c, gin.H{
		"profile":         parsed.Profile,
		"hints":           parsed.Hints,
		"reference_count": count,
		"evidence_kept":   keepEvidence,
		"note":            note,
	})
}

// referenceOcrRequest 参考路线截图识别入参。
// 只收 base64（不落盘、不进 multipart 临时目录）：图片从浏览器到 ai-service 全程在内存里。
type referenceOcrRequest struct {
	ImageBase64 string `json:"image_base64"`
	MimeType    string `json:"mime_type"`
}

// 6MB base64 ≈ 4.4MB 原图；ai-service 侧还有 4MB 硬上限。这里先拦一道，
// 避免把超大请求体转发过去白烧一次带宽。
const referenceImageMaxBase64Chars = 6 * 1024 * 1024

func ocrStatusFor(code string) int {
	switch strings.ToLower(strings.TrimSpace(code)) {
	case "too_large":
		return http.StatusRequestEntityTooLarge
	case "unsupported_mime", "not_base64", "empty_image":
		return http.StatusBadRequest
	case "no_text_in_image", "empty_transcription":
		return http.StatusUnprocessableEntity
	case "vision_not_configured", "vision_call_failed":
		return http.StatusServiceUnavailable
	default:
		return http.StatusBadGateway
	}
}

// RecognizeReferenceRouteHandler POST /api/v1/user/preference/reference-route/ocr
//
// 参考路线**截图 → 文字**：把图片交给 ai-service 的视觉模型原样抄成文字，回给前端让
// 用户**先核对再决定是否学习**。三条硬约束：
//  1. **不存图片**：网关不落盘、不写库（响应里显式回 `image_stored:false`，便于前端如实告知）；
//  2. **不臆造**：识别不了就把原因码/原因话说回前端（HEIC、超限、纯风景图、模型故障）；
//  3. 登录才可用：否则这就是一个匿名的免费 OCR 网关。
func RecognizeReferenceRouteHandler(c *gin.Context) {
	if _, ok := currentUserIDOrReject(c); !ok {
		return
	}
	var req referenceOcrRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		planningError(c, http.StatusBadRequest, "INVALID_JSON", "请求数据格式不正确", nil)
		return
	}
	image := strings.TrimSpace(req.ImageBase64)
	if image == "" {
		planningError(c, http.StatusBadRequest, "REFERENCE_IMAGE_REQUIRED", "请先选择一张路线截图", nil)
		return
	}
	if len(image) > referenceImageMaxBase64Chars {
		planningError(c, http.StatusRequestEntityTooLarge, "REFERENCE_IMAGE_TOO_LARGE", "图片过大，请压缩或只截取路线部分", nil)
		return
	}

	body, err := json.Marshal(gin.H{"image_base64": image, "mime_type": strings.TrimSpace(req.MimeType)})
	if err != nil {
		planningError(c, http.StatusInternalServerError, "REFERENCE_ENCODE_FAILED", "参考路线处理失败", nil)
		return
	}
	data, status, err := callAI(c, "/api/v1/profile/reference-route/ocr", body)
	if err != nil || status != http.StatusOK {
		planningError(c, http.StatusBadGateway, "REFERENCE_OCR_UNAVAILABLE", "图片识别服务暂不可用，请改用粘贴文字", nil)
		return
	}

	var parsed struct {
		OK          bool     `json:"ok"`
		Code        string   `json:"code"`
		Message     string   `json:"message"`
		Text        string   `json:"text"`
		Model       string   `json:"model"`
		Chars       int      `json:"chars"`
		Warnings    []string `json:"warnings"`
		Note        string   `json:"note"`
		PrivacyNote string   `json:"privacy_note"`
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		planningError(c, http.StatusBadGateway, "REFERENCE_OCR_UNPARSABLE", "图片识别结果不可用，请改用粘贴文字", nil)
		return
	}
	if !parsed.OK || strings.TrimSpace(parsed.Text) == "" {
		code := strings.TrimSpace(parsed.Code)
		if code == "" {
			code = "ocr_failed"
		}
		message := strings.TrimSpace(parsed.Message)
		if message == "" {
			message = "没能从这张图里读出文字，请改用粘贴文字或换一张截图"
		}
		planningError(c, ocrStatusFor(code), "REFERENCE_OCR_"+strings.ToUpper(code), message, map[string]string{
			"privacy_note": parsed.PrivacyNote,
			"image_stored": "false",
		})
		return
	}

	note := strings.TrimSpace(parsed.Note)
	if note == "" {
		note = "识别结果请先核对（可能有错字/漏字），确认后再点学习；服务端不保存图片。"
	}
	planningData(c, gin.H{
		"text":         parsed.Text,
		"model":        parsed.Model,
		"chars":        parsed.Chars,
		"warnings":     parsed.Warnings,
		"note":         note,
		"privacy_note": parsed.PrivacyNote,
		// 显式事实：图片没有被保存（前端据此如实告知用户，而不是靠文案承诺）
		"image_stored": false,
	})
}

// ClearReferenceRouteHandler DELETE /api/v1/user/preference/reference-route
func ClearReferenceRouteHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	if err := service.ClearReferenceProfile(userID); err != nil {
		planningError(c, http.StatusInternalServerError, "REFERENCE_CLEAR_FAILED", "参考路线画像清除失败", nil)
		return
	}
	planningData(c, gin.H{"cleared": true})
}

// GetReferenceRouteHandler GET /api/v1/user/preference/reference-route
// 让前端能展示"我学到的是什么"（只回特征，永不回原文）。
func GetReferenceRouteHandler(c *gin.Context) {
	userID, ok := currentUserIDOrReject(c)
	if !ok {
		return
	}
	profile := service.ReferenceProfilePayload(userID)
	if profile == nil {
		planningData(c, gin.H{"profile": nil, "reference_count": 0})
		return
	}
	count := 0
	if raw, ok := profile["reference_count"].(float64); ok {
		count = int(raw)
	}
	planningData(c, gin.H{"profile": profile, "reference_count": count})
}
