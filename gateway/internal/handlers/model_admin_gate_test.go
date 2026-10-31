package handlers

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// Regression tests for the model-registry read gate.
//
// Background: the write side of the registry (register / evaluate / promote) was
// already admin-gated via isPlanningAdmin, but ListModelDeploymentsHandler only
// checked that *someone* was logged in. A ModelDeployment row carries the internal
// inference Endpoint, so every authenticated account could enumerate internal
// service URLs — internal-topology disclosure that also hands an attacker the
// targets that validateModelEndpointHost exists to contain.

// adminGateTestRouter wires the handler behind an injectable identity so the gate
// can be exercised without a full login flow.
func adminGateTestRouter(t *testing.T, userID string) *gin.Engine {
	t.Helper()

	// Isolated in-memory database; the handler queries it once the gate passes.
	old := database.DB
	db, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() { database.DB = old })
	if err := db.AutoMigrate(&models.ModelDeployment{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.ModelDeployment{
		Name: "shadow", Version: "shadow-v1", Status: "shadow",
		Endpoint: "http://internal-model-host:9000/predict",
	}).Error; err != nil {
		t.Fatal(err)
	}

	router := gin.New()
	if userID != "" {
		router.Use(func(c *gin.Context) {
			c.Set("user_id", userID)
			c.Next()
		})
	}
	router.GET("/planning/models", ListModelDeploymentsHandler)
	return router
}

func callListModels(t *testing.T, r *gin.Engine) *httptest.ResponseRecorder {
	t.Helper()
	res := httptest.NewRecorder()
	r.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/models", nil))
	return res
}

// TestListModelDeploymentsRequiresAdmin is the regression: a logged-in non-admin
// must not be able to read the registry.
func TestListModelDeploymentsRequiresAdmin(t *testing.T) {
	t.Setenv("PLANNING_ADMIN_USER_IDS", "admin-1")

	res := callListModels(t, adminGateTestRouter(t, "ordinary-user"))
	if res.Code != http.StatusForbidden {
		t.Fatalf("a non-admin must be refused, got %d (%s)", res.Code, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "MODEL_ADMIN_REQUIRED") {
		t.Fatalf("expected a machine-readable code, got %s", res.Body.String())
	}
	// The refusal must not leak the payload it was protecting.
	if strings.Contains(res.Body.String(), "internal-model-host") {
		t.Fatalf("the 403 body leaked the internal endpoint: %s", res.Body.String())
	}
}

// TestListModelDeploymentsRefusedWhenNoAdminConfigured covers the fail-closed
// default: with no allowlist, nobody is an admin, so the registry stays shut.
func TestListModelDeploymentsRefusedWhenNoAdminConfigured(t *testing.T) {
	t.Setenv("PLANNING_ADMIN_USER_IDS", "")
	res := callListModels(t, adminGateTestRouter(t, "some-user"))
	if res.Code != http.StatusForbidden {
		t.Fatalf("with no admin allowlist the registry must stay closed, got %d", res.Code)
	}
}

// TestListModelDeploymentsRequiresAuthentication keeps the unauthenticated case
// distinct from the unauthorised one.
func TestListModelDeploymentsRequiresAuthentication(t *testing.T) {
	t.Setenv("PLANNING_ADMIN_USER_IDS", "admin-1")
	// No identity middleware: currentUserIDOrReject must reject before the admin
	// check is even consulted.
	res := callListModels(t, adminGateTestRouter(t, ""))
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("an anonymous caller must get 401, got %d (%s)", res.Code, res.Body.String())
	}
}

// TestListModelDeploymentsAllowsAdmin proves the gate is not simply "deny all".
func TestListModelDeploymentsAllowsAdmin(t *testing.T) {
	t.Setenv("PLANNING_ADMIN_USER_IDS", "admin-1")
	res := callListModels(t, adminGateTestRouter(t, "admin-1"))
	if res.Code != http.StatusOK {
		t.Fatalf("an admin must be allowed, got %d (%s)", res.Code, res.Body.String())
	}
	if !strings.Contains(res.Body.String(), "shadow-v1") {
		t.Fatalf("the admin payload should include the registered deployment: %s", res.Body.String())
	}
}

// TestModelRuntimeHealthStaysReadableForOrdinaryUsers documents a deliberate
// asymmetry. The frontend fetches runtime-health inside one Promise.all whose
// catch clears every diagnostics panel, so a 403 there would blank the whole
// surface for normal users rather than one tile. The payload is bounded (counts,
// error rate, fairness spread) and carries no Endpoint, so it stays readable.
// If someone later decides to gate it too, this test is the place that records
// why the frontend call site has to change first.
func TestModelRuntimeHealthStaysReadableForOrdinaryUsers(t *testing.T) {
	old := database.DB
	db, err := gorm.Open(sqlite.Open(filepath.Join(t.TempDir(), "mh.db")), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() {
		// Close the pool before t.TempDir tries to remove the file: on Windows an
		// open SQLite handle makes the delete fail and the test reports a cleanup
		// error instead of its real result.
		if sqlDB, err := db.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
		database.DB = old
	})
	if err := db.AutoMigrate(&models.ModelDeployment{}, &models.PlanningEvent{}); err != nil {
		t.Fatal(err)
	}

	router := gin.New()
	router.Use(func(c *gin.Context) {
		c.Set("user_id", "ordinary-user")
		c.Next()
	})
	router.GET("/planning/models/runtime-health", ModelRuntimeHealthHandler)

	res := httptest.NewRecorder()
	router.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/planning/models/runtime-health", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("runtime-health must stay readable for ordinary users, got %d (%s)", res.Code, res.Body.String())
	}
	if strings.Contains(res.Body.String(), "internal-model-host") {
		t.Fatalf("runtime-health must not expose the model endpoint: %s", res.Body.String())
	}
}
