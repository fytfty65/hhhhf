package handlers

import "github.com/gin-gonic/gin"

// PlanningSignalsHandler normalizes optional weather and crowd suppliers for
// the planner. It deliberately returns availability metadata so consumers can
// distinguish live signals from estimator fallbacks.
func PlanningSignalsHandler(c *gin.Context) {
	ctx, ok := decodePlanningContext(c)
	if !ok {
		return
	}
	result := gin.H{"weather": gin.H{"available": false}, "crowd": gin.H{"available": false}}
	if data, source, available := fetchPlanningSignal(c.Request.Context(), "WEATHER", ctx); available {
		result["weather"] = gin.H{"available": true, "data": data, "source": source}
	}
	if data, source, available := fetchPlanningSignal(c.Request.Context(), "CROWD", ctx); available {
		result["crowd"] = gin.H{"available": true, "data": data, "source": source}
	}
	planningData(c, gin.H{"context": ctx, "signals": result, "fallback": gin.H{"weather": "local_forecast_estimate", "crowd": "historical_pattern_estimate"}})
}
