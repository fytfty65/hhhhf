// Command provider-quality-preflight validates local FCM staging wiring.
// It never exchanges OAuth tokens, calls FCM, or prints credential values.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"gateway/internal/push"
)

func main() {
	var env string
	flag.StringVar(&env, "env", strings.TrimSpace(os.Getenv("APP_ENV")), "deployment environment; must be staging or test")
	flag.Parse()
	result := push.PreflightFromEnv(env)
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, `{"status":"blocked","reason":"result_encoding_failed"}`)
		os.Exit(2)
	}
	fmt.Println(string(encoded))
	if result.Status != "ready" {
		os.Exit(2)
	}
}
