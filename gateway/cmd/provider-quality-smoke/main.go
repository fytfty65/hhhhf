// Command provider-quality-smoke runs one credential-free provider-quality
// Web Push scheduling check. It is intentionally dry-run only: production or
// Firebase credentials cannot be used by this command, preventing an operator
// from accidentally sending notifications while validating staging wiring.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"gateway/internal/database"
	"gateway/internal/handlers"
)

type smokeOutput struct {
	Status  string         `json:"status"`
	DryRun  bool           `json:"dry_run"`
	Due     int            `json:"due_count"`
	Limit   int            `json:"batch_limit"`
	Results map[string]int `json:"result"`
}

func main() {
	var dbPath string
	var limit int
	var dryRun bool
	var nowText string
	flag.StringVar(&dbPath, "db", strings.TrimSpace(os.Getenv("DB_PATH")), "Gateway SQLite database path")
	flag.IntVar(&limit, "limit", 20, "maximum number of due deliveries to inspect (1-100)")
	flag.BoolVar(&dryRun, "dry-run", true, "preview only; real delivery is not supported by this command")
	flag.StringVar(&nowText, "now", "", "optional RFC3339 timestamp for deterministic smoke checks")
	flag.Parse()

	if !dryRun {
		fail("real delivery is intentionally disabled; use the authorized worker in an isolated staging deployment")
	}
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	if env != "staging" && env != "test" {
		fail("APP_ENV must be staging or test")
	}
	if strings.TrimSpace(dbPath) != "" {
		if err := os.Setenv("DB_PATH", dbPath); err != nil {
			fail("set DB_PATH: " + err.Error())
		}
	}
	if limit < 1 || limit > 100 {
		fail("limit must be between 1 and 100")
	}
	now := time.Now().UTC()
	if strings.TrimSpace(nowText) != "" {
		parsed, err := time.Parse(time.RFC3339, strings.TrimSpace(nowText))
		if err != nil {
			fail("now must be RFC3339: " + err.Error())
		}
		now = parsed.UTC()
	}
	if err := database.InitDB(); err != nil {
		fail("database initialization failed: " + err.Error())
	}
	defer func() {
		if sqlDB, err := database.DB.DB(); err == nil {
			_ = sqlDB.Close()
		}
	}()
	result, err := handlers.RunProviderQualityWebPushOnce(context.Background(), database.DB, nil, limit, now, true)
	if err != nil {
		fail("dry-run failed: " + err.Error())
	}
	out := smokeOutput{Status: "pass", DryRun: true, Due: result["dry_run"], Limit: limit, Results: map[string]int{"dry_run": result["dry_run"]}}
	encoded, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		fail("encode result: " + err.Error())
	}
	fmt.Println(string(encoded))
}

func fail(message string) {
	result := map[string]any{"status": "blocked", "reason": message}
	encoded, _ := json.Marshal(result)
	fmt.Fprintln(os.Stderr, string(encoded))
	os.Exit(2)
}
