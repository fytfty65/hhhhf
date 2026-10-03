package api

import (
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"gateway/internal/metrics"

	"github.com/gin-gonic/gin"
)

// Prometheus exposition for the gateway.
//
// The JSON /metrics endpoint reports a lifetime *average* latency per path,
// which is unusable for alerting: an average hides the tail, and because it is
// cumulative since process start it gets progressively blunter over uptime. This
// file adds a Prometheus-format endpoint with real histograms so p50/p95/p99 can
// be computed by the scraper, which is what an SLO actually needs.
//
// Two endpoints coexist on purpose: /metrics keeps the JSON shape existing
// tooling and tests rely on, /metrics/prometheus serves scrapers.

// latencyBucketsMs are the histogram bucket upper bounds in milliseconds. They
// straddle the latency ranges this service actually exhibits: fast cached reads
// (<50ms), local DB work (<100ms), and proxied provider calls (seconds).
var latencyBucketsMs = []float64{5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000}

type latencyHistogram struct {
	counts []uint64 // one per bucket, plus a final +Inf bucket
	sum    float64
	total  uint64
}

func newLatencyHistogram() *latencyHistogram {
	return &latencyHistogram{counts: make([]uint64, len(latencyBucketsMs)+1)}
}

func (h *latencyHistogram) observe(ms float64) {
	h.total++
	h.sum += ms
	for i, bound := range latencyBucketsMs {
		if ms <= bound {
			h.counts[i]++
			return
		}
	}
	h.counts[len(latencyBucketsMs)]++ // +Inf
}

// prometheusExtra holds per-path histograms and the in-flight gauge.
var (
	promMu        sync.Mutex
	pathHistogram = map[string]*latencyHistogram{}
	inFlight      int64
)

func recordPrometheusLatency(method, path string, ms float64) {
	promMu.Lock()
	key := method + " " + path
	h := pathHistogram[key]
	if h == nil {
		h = newLatencyHistogram()
		pathHistogram[key] = h
	}
	h.observe(ms)
	promMu.Unlock()
}

// prometheusLabelValue escapes a label value per the exposition format.
func prometheusLabelValue(value string) string {
	replacer := strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`)
	return replacer.Replace(value)
}

// PrometheusHandler renders the metrics in Prometheus text exposition format.
//
// Path labels are bounded: the gateway registers a fixed route table, so the
// per-path series count cannot grow with user input. If that ever changes, the
// label must be switched to the matched route pattern (c.FullPath()) rather than
// the raw URL to avoid unbounded cardinality.
func PrometheusHandler(c *gin.Context) {
	metricsMu.Lock()
	total := requestsTotal
	byStatus := make(map[string]int, len(requestsByStatus))
	for k, v := range requestsByStatus {
		byStatus[k] = v
	}
	uptime := int(time.Since(startTime).Seconds())
	metricsMu.Unlock()

	promMu.Lock()
	histograms := make(map[string]*latencyHistogram, len(pathHistogram))
	for k, v := range pathHistogram {
		counts := make([]uint64, len(v.counts))
		copy(counts, v.counts)
		histograms[k] = &latencyHistogram{counts: counts, sum: v.sum, total: v.total}
	}
	promMu.Unlock()

	var b strings.Builder

	b.WriteString("# HELP omniroute_gateway_up Whether the gateway process is serving.\n")
	b.WriteString("# TYPE omniroute_gateway_up gauge\n")
	b.WriteString("omniroute_gateway_up 1\n")

	b.WriteString("# HELP omniroute_gateway_uptime_seconds Seconds since process start.\n")
	b.WriteString("# TYPE omniroute_gateway_uptime_seconds gauge\n")
	fmt.Fprintf(&b, "omniroute_gateway_uptime_seconds %d\n", uptime)

	b.WriteString("# HELP omniroute_gateway_requests_total Total HTTP requests handled.\n")
	b.WriteString("# TYPE omniroute_gateway_requests_total counter\n")
	fmt.Fprintf(&b, "omniroute_gateway_requests_total %d\n", total)

	b.WriteString("# HELP omniroute_gateway_responses_total HTTP responses by status code.\n")
	b.WriteString("# TYPE omniroute_gateway_responses_total counter\n")
	statusKeys := make([]string, 0, len(byStatus))
	for k := range byStatus {
		statusKeys = append(statusKeys, k)
	}
	sort.Strings(statusKeys)
	for _, code := range statusKeys {
		fmt.Fprintf(&b, "omniroute_gateway_responses_total{status=\"%s\"} %d\n",
			prometheusLabelValue(code), byStatus[code])
	}

	// Learning-loop health: a climbing missing_arm means reward signals are being
	// dropped, so the policy cannot learn.
	b.WriteString("# HELP omniroute_gateway_bandit_feedback_total Reward signals by outcome.\n")
	b.WriteString("# TYPE omniroute_gateway_bandit_feedback_total counter\n")
	counters := metrics.BanditFeedbackCounters()
	for _, outcome := range []string{"sent", "accepted", "rejected", "missing_arm"} {
		fmt.Fprintf(&b, "omniroute_gateway_bandit_feedback_total{outcome=\"%s\"} %d\n",
			outcome, counters[outcome])
	}

	// Audit retention worker health. These aggregate gauges make a silent
	// cleanup outage alertable without exposing audit rows or database details.
	b.WriteString("# HELP omniroute_model_audit_cleanup_runs_total Audit cleanup runs completed or attempted.\n")
	b.WriteString("# TYPE omniroute_model_audit_cleanup_runs_total counter\n")
	auditCleanup := metrics.AuditCleanupSnapshot()
	fmt.Fprintf(&b, "omniroute_model_audit_cleanup_runs_total %d\n", int64(auditCleanup["runs"].(int64)))
	b.WriteString("# HELP omniroute_model_audit_cleanup_failures_total Audit cleanup failures.\n")
	b.WriteString("# TYPE omniroute_model_audit_cleanup_failures_total counter\n")
	fmt.Fprintf(&b, "omniroute_model_audit_cleanup_failures_total %d\n", int64(auditCleanup["failures"].(int64)))
	b.WriteString("# HELP omniroute_model_audit_cleanup_last_deleted Number of audit rows deleted by the latest cleanup.\n")
	b.WriteString("# TYPE omniroute_model_audit_cleanup_last_deleted gauge\n")
	fmt.Fprintf(&b, "omniroute_model_audit_cleanup_last_deleted %d\n", int64(auditCleanup["last_deleted"].(int64)))
	b.WriteString("# HELP omniroute_model_audit_cleanup_last_run_timestamp_seconds Unix timestamp of the latest cleanup attempt.\n")
	b.WriteString("# TYPE omniroute_model_audit_cleanup_last_run_timestamp_seconds gauge\n")
	lastRun, _ := time.Parse(time.RFC3339, auditCleanup["last_run_at"].(string))
	fmt.Fprintf(&b, "omniroute_model_audit_cleanup_last_run_timestamp_seconds %d\n", lastRun.Unix())
	b.WriteString("# HELP omniroute_model_audit_cleanup_healthy Whether the latest audit cleanup completed without error.\n")
	b.WriteString("# TYPE omniroute_model_audit_cleanup_healthy gauge\n")
	healthy := 0
	if auditCleanup["status"] == "healthy" {
		healthy = 1
	}
	fmt.Fprintf(&b, "omniroute_model_audit_cleanup_healthy %d\n", healthy)

	// Provider quality delivery worker health. These counters intentionally
	// contain no user, trip, token, or provider payload labels.
	b.WriteString("# HELP omniroute_provider_quality_push_total Web Push worker outcomes.\n")
	b.WriteString("# TYPE omniroute_provider_quality_push_total counter\n")
	pushSnapshot := metrics.ProviderQualityPushSnapshot()
	workerEnabled := 0
	if strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_WORKER_ENABLED")), "true") {
		workerEnabled = 1
	}
	dryRun := 0
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	if strings.EqualFold(strings.TrimSpace(os.Getenv("PROVIDER_QUALITY_WEB_PUSH_DRY_RUN")), "true") && (env == "staging" || env == "test") {
		dryRun = 1
	}
	b.WriteString("# HELP omniroute_provider_quality_worker_enabled Whether the provider quality Web Push worker is explicitly enabled.\n")
	b.WriteString("# TYPE omniroute_provider_quality_worker_enabled gauge\n")
	fmt.Fprintf(&b, "omniroute_provider_quality_worker_enabled %d\n", workerEnabled)
	b.WriteString("# HELP omniroute_provider_quality_worker_dry_run Whether the worker is running in staging/test dry-run mode.\n")
	b.WriteString("# TYPE omniroute_provider_quality_worker_dry_run gauge\n")
	fmt.Fprintf(&b, "omniroute_provider_quality_worker_dry_run %d\n", dryRun)
	b.WriteString("# HELP omniroute_provider_quality_push_last_run_timestamp_seconds Unix timestamp of the latest worker run.\n")
	b.WriteString("# TYPE omniroute_provider_quality_push_last_run_timestamp_seconds gauge\n")
	fmt.Fprintf(&b, "omniroute_provider_quality_push_last_run_timestamp_seconds %d\n", pushSnapshot["last_run_unix"])
	for _, outcome := range []string{"runs", "errors", "claimed", "sent", "retrying", "failed", "invalid_token", "no_token", "dry_runs"} {
		fmt.Fprintf(&b, "omniroute_provider_quality_push_total{outcome=\"%s\"} %d\n", outcome, pushSnapshot[outcome])
	}
	b.WriteString("# HELP omniroute_provider_quality_delivery_cleanup_total Delivery retention cleanup outcomes.\n")
	b.WriteString("# TYPE omniroute_provider_quality_delivery_cleanup_total counter\n")
	for _, outcome := range []string{"runs", "deleted", "failures"} {
		key := "cleanup_" + outcome
		fmt.Fprintf(&b, "omniroute_provider_quality_delivery_cleanup_total{outcome=\"%s\"} %d\n", outcome, pushSnapshot[key])
	}
	b.WriteString("# HELP omniroute_provider_quality_delivery_cleanup_last_success_timestamp_seconds Unix timestamp of the latest successful delivery cleanup.\n")
	b.WriteString("# TYPE omniroute_provider_quality_delivery_cleanup_last_success_timestamp_seconds gauge\n")
	fmt.Fprintf(&b, "omniroute_provider_quality_delivery_cleanup_last_success_timestamp_seconds %d\n", pushSnapshot["cleanup_last_success_unix"])

	b.WriteString("# HELP omniroute_gateway_request_duration_milliseconds HTTP request latency.\n")
	b.WriteString("# TYPE omniroute_gateway_request_duration_milliseconds histogram\n")
	routeKeys := make([]string, 0, len(histograms))
	for k := range histograms {
		routeKeys = append(routeKeys, k)
	}
	sort.Strings(routeKeys)
	for _, key := range routeKeys {
		h := histograms[key]
		parts := strings.SplitN(key, " ", 2)
		method, path := parts[0], ""
		if len(parts) == 2 {
			path = parts[1]
		}
		labels := fmt.Sprintf("method=\"%s\",path=\"%s\"",
			prometheusLabelValue(method), prometheusLabelValue(path))
		var cumulative uint64
		for i, bound := range latencyBucketsMs {
			cumulative += h.counts[i]
			fmt.Fprintf(&b, "omniroute_gateway_request_duration_milliseconds_bucket{%s,le=\"%g\"} %d\n",
				labels, bound, cumulative)
		}
		cumulative += h.counts[len(latencyBucketsMs)]
		fmt.Fprintf(&b, "omniroute_gateway_request_duration_milliseconds_bucket{%s,le=\"+Inf\"} %d\n",
			labels, cumulative)
		fmt.Fprintf(&b, "omniroute_gateway_request_duration_milliseconds_sum{%s} %g\n", labels, h.sum)
		fmt.Fprintf(&b, "omniroute_gateway_request_duration_milliseconds_count{%s} %d\n", labels, h.total)
	}

	// Process-level goroutine and DB-pool gauges are intentionally omitted: the
	// gateway does not currently expose them and inventing a value would be
	// worse than omitting the series.

	c.Data(http.StatusOK, "text/plain; version=0.0.4; charset=utf-8", []byte(b.String()))
}
