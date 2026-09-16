package api

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// Prometheus exposition tests.
//
// The JSON /metrics endpoint only reports lifetime averages, which cannot
// support an SLO. These tests pin the properties a scraper depends on: a valid
// content type, cumulative bucket counts, an explicit +Inf bucket, and
// sum/count series that let the scraper compute quantiles.

func histogramTestRouter() *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.GET("/metrics/prometheus", PrometheusHandler)
	return r
}

func renderPrometheus(t *testing.T) string {
	t.Helper()
	res := httptest.NewRecorder()
	histogramTestRouter().ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/metrics/prometheus", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", res.Code)
	}
	contentType := res.Header().Get("Content-Type")
	if !strings.HasPrefix(contentType, "text/plain") {
		t.Fatalf("Prometheus output must be text/plain, got %q", contentType)
	}
	if !strings.Contains(contentType, "version=0.0.4") {
		t.Fatalf("expected exposition version in content type, got %q", contentType)
	}
	return res.Body.String()
}

// resetPrometheusState clears the counters so each test starts clean.
func resetPrometheusState() {
	metricsMu.Lock()
	requestsTotal = 0
	requestsByStatus = map[string]int{}
	requestsByPath = map[string]int{}
	latencyByPath = map[string]float64{}
	latencyCountByPath = map[string]int{}
	metricsMu.Unlock()

	promMu.Lock()
	pathHistogram = map[string]*latencyHistogram{}
	promMu.Unlock()
}

func TestPrometheusExpositionHasRequiredSeries(t *testing.T) {
	resetPrometheusState()
	recordRequest("GET", "/ping", 200, 3)
	recordPrometheusLatency("GET", "/ping", 3)
	recordRequest("POST", "/api/v1/risk/realtime", 200, 9200)
	recordPrometheusLatency("POST", "/api/v1/risk/realtime", 9200)

	body := renderPrometheus(t)

	for _, want := range []string{
		"# TYPE omniroute_gateway_up gauge",
		"omniroute_gateway_up 1",
		"# TYPE omniroute_gateway_uptime_seconds gauge",
		"# TYPE omniroute_gateway_requests_total counter",
		"# TYPE omniroute_gateway_responses_total counter",
		`omniroute_gateway_responses_total{status="200"} 2`,
		"# TYPE omniroute_gateway_bandit_feedback_total counter",
		"# TYPE omniroute_gateway_request_duration_milliseconds histogram",
		`method="GET",path="/ping"`,
		`method="POST",path="/api/v1/risk/realtime"`,
		`le="+Inf"`,
		"_duration_milliseconds_sum{",
		"_duration_milliseconds_count{",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("exposition missing %q\n---\n%s", want, body)
		}
	}
}

func TestPrometheusBucketsAreCumulativeAndEndWithInf(t *testing.T) {
	resetPrometheusState()
	// One fast sample and one slow sample on the same route.
	recordPrometheusLatency("GET", "/x", 3)
	recordPrometheusLatency("GET", "/x", 9200)

	body := renderPrometheus(t)
	key := `method="GET",path="/x"`

	// Collect bucket counts in bucket order.
	values := []uint64{}
	var inf uint64
	for _, bound := range latencyBucketsMs {
		label := fmt.Sprintf(`_bucket{%s,le="%g"}`, key, bound)
		idx := strings.Index(body, label)
		if idx < 0 {
			t.Fatalf("missing bucket le=%g", bound)
		}
		rest := body[idx+len(label):]
		end := strings.IndexAny(rest, "\r\n")
		n, err := strconv.ParseUint(strings.TrimSpace(rest[:end]), 10, 64)
		if err != nil {
			t.Fatalf("bucket le=%g is not an integer: %v", bound, err)
		}
		values = append(values, n)
	}
	infLabel := fmt.Sprintf(`_bucket{%s,le="+Inf"}`, key)
	if idx := strings.Index(body, infLabel); idx >= 0 {
		rest := body[idx+len(infLabel):]
		end := strings.IndexAny(rest, "\r\n")
		inf, _ = strconv.ParseUint(strings.TrimSpace(rest[:end]), 10, 64)
	} else {
		t.Fatal("missing +Inf bucket")
	}

	// Cumulative means each bucket is >= the previous.
	for i := 1; i < len(values); i++ {
		if values[i] < values[i-1] {
			t.Fatalf("buckets are not cumulative at index %d: %d < %d", i, values[i], values[i-1])
		}
	}
	if inf != 2 {
		t.Fatalf("+Inf bucket must equal the sample count (2), got %d", inf)
	}
	if values[len(values)-1] > inf {
		t.Fatal("a finite bucket exceeded the +Inf bucket")
	}
	// The 5ms sample must land in the first bucket, so it cannot be zero.
	if values[0] == 0 {
		t.Fatal("expected the 3ms sample to fall in the le=5 bucket")
	}
}

func TestPrometheusCountAndSumMatchObservations(t *testing.T) {
	resetPrometheusState()
	recordPrometheusLatency("GET", "/y", 10)
	recordPrometheusLatency("GET", "/y", 20)
	recordPrometheusLatency("GET", "/y", 30)

	body := renderPrometheus(t)
	key := `method="GET",path="/y"`

	countLabel := fmt.Sprintf(`_duration_milliseconds_count{%s}`, key)
	idx := strings.Index(body, countLabel)
	if idx < 0 {
		t.Fatal("missing _count series")
	}
	rest := body[idx+len(countLabel):]
	end := strings.IndexAny(rest, "\r\n")
	count, err := strconv.ParseUint(strings.TrimSpace(rest[:end]), 10, 64)
	if err != nil || count != 3 {
		t.Fatalf("expected count 3, got %q (err %v)", rest[:end], err)
	}

	sumLabel := fmt.Sprintf(`_duration_milliseconds_sum{%s}`, key)
	idx = strings.Index(body, sumLabel)
	if idx < 0 {
		t.Fatal("missing _sum series")
	}
	rest = body[idx+len(sumLabel):]
	end = strings.IndexAny(rest, "\r\n")
	sum, err := strconv.ParseFloat(strings.TrimSpace(rest[:end]), 64)
	if err != nil || sum != 60 {
		t.Fatalf("expected sum 60, got %q (err %v)", rest[:end], err)
	}
}

func TestPrometheusLabelValuesAreEscaped(t *testing.T) {
	resetPrometheusState()
	recordPrometheusLatency("GET", `/weird"path\`, 1)
	body := renderPrometheus(t)
	// A raw quote would terminate the label early and corrupt the exposition.
	if strings.Contains(body, `path="/weird"path\`) {
		t.Fatalf("label value was not escaped:\n%s", body)
	}
	if !strings.Contains(body, `\"path\\`) {
		t.Fatalf("expected escaped quote and backslash in label:\n%s", body)
	}
}

func TestPrometheusOutputHasNoEmptyMetricNames(t *testing.T) {
	resetPrometheusState()
	body := renderPrometheus(t)
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, " ") {
			t.Fatalf("metric line must not start with whitespace: %q", line)
		}
		if !strings.Contains(line, " ") {
			t.Fatalf("metric line must be 'name value': %q", line)
		}
	}
}

func TestLatencyBucketsCoverObservedRange(t *testing.T) {
	// A 30s ceiling must exist: proxied provider calls observed up to ~9s, and a
	// histogram without a bucket above that would report every slow request as
	// +Inf, destroying the tail information the endpoint exists to provide.
	max := latencyBucketsMs[len(latencyBucketsMs)-1]
	if max < 30000 {
		t.Fatalf("top finite bucket %g is too low for observed latencies", max)
	}
	for i := 1; i < len(latencyBucketsMs); i++ {
		if latencyBucketsMs[i] <= latencyBucketsMs[i-1] {
			t.Fatalf("buckets must be strictly increasing at index %d", i)
		}
	}
}
