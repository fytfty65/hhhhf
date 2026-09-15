package handlers

import (
	"io"
	"net/http"

	"github.com/gin-gonic/gin"
)

// CarbonFootprintHandler 碳足迹评估转发（P5）：透传请求体到 AI 服务，返回碳足迹核算结果。
// 前端可复用路线数据构建 segments（每段出行方式 + 距离）呼叫本接口，获得解释性绿碳核算。
func CarbonFootprintHandler(c *gin.Context) {
	const maxPayload = 1 << 20
	reqBody, err := io.ReadAll(io.LimitReader(c.Request.Body, maxPayload+1))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "请求体读取失败", "code": "INVALID_BODY"})
		return
	}
	if len(reqBody) > maxPayload {
		c.JSON(http.StatusRequestEntityTooLarge, gin.H{"error": "请求体过大", "code": "PAYLOAD_TOO_LARGE"})
		return
	}
	data, status, err := callAI(c, "/api/v1/carbon/footprint", reqBody)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "AI 服务不可用", "result": nil})
		return
	}
	c.Data(status, "application/json; charset=utf-8", data)
}
