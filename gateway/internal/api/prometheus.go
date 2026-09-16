package api

import (
	"fmt"
	"net/http"
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
