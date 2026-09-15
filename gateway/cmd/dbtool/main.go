package main

import (
	"flag"
	"fmt"
	"log"
	"os"

	"gateway/internal/database"
	"github.com/joho/godotenv"
)

func main() {
	_ = godotenv.Load()
	backupDir := flag.String("dir", "", "backup directory (defaults to BACKUP_DIR)")
	restore := flag.String("restore", "", "validated SQLite backup file to restore")
	flag.Parse()
	if *restore != "" {
		if err := database.RestoreBackup(*restore); err != nil {
			log.Fatal(err)
		}
		// Re-open and migrate the restored file in-process so the command also
		// verifies that the restored schema is usable before reporting success.
		if err := database.InitDB(); err != nil {
			log.Fatal(err)
		}
		fmt.Println("database restored")
		return
	}
	if err := database.InitDB(); err != nil {
		log.Fatal(err)
	}
	path, err := database.Backup(*backupDir)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Fprintln(os.Stdout, path)
}
