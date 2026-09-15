package database

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"gateway/internal/models"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

var DB *gorm.DB

// Increment this value when a schema change needs an explicit, auditable
// migration step. Migrations are applied one version at a time so a partially
// upgraded deployment can resume safely on the next start.
const schemaVersion = 7

func migrateSchema(tx *gorm.DB, version int) error {
	switch version {
	case 1:
		return tx.AutoMigrate(
			&models.User{}, &models.TripPlan{}, &models.CommunityPost{},
			&models.PostFavorite{}, &models.Comment{}, &models.Room{},
			&models.RoomMember{}, &models.Message{}, &models.FeedbackLog{},
			&models.UserPreference{}, &models.NodeAnnotation{},
			&models.NodeAnnotationVote{}, &models.RecommendationEvent{},
			&models.BudgetPlan{}, &models.ExpenseRecord{}, &models.PlanVariant{},
			&models.PlanVariantVote{}, &models.TripSatisfaction{},
			&models.RiskSubscription{},
		)
	case 2:
		// Older builds used a global unique TripID for satisfaction records,
		// which prevented two members of one room from submitting feedback.
		// Drop that legacy index before AutoMigrate creates the owner-scoped key.
		if err := tx.Exec("DROP INDEX IF EXISTS ux_satisfaction_trip").Error; err != nil {
			return err
		}
		if err := tx.AutoMigrate(&models.TripSatisfaction{}); err != nil {
			return err
		}
		return tx.Exec("CREATE INDEX IF NOT EXISTS idx_trip_satisfactions_trip_id ON trip_satisfactions(trip_id)").Error
	case 3:
		return tx.AutoMigrate(&models.PlanningEvent{})
	case 4:
		return tx.AutoMigrate(&models.ModelDeployment{})
	case 5:
		return tx.AutoMigrate(&models.PlanningRun{})
	case 6:
		return tx.AutoMigrate(&models.TravelOrder{})
	case 7:
		return tx.AutoMigrate(&models.TripExecutionState{})
	default:
		return fmt.Errorf("unknown schema migration %d", version)
	}
}

func InitDB() error {
	dbPath := strings.TrimSpace(os.Getenv("DB_PATH"))
	if dbPath == "" {
		dbPath = "omniroute.db"
	}
	if dir := filepath.Dir(dbPath); dir != "." {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			return err
		}
	}
	var err error
	DB, err = gorm.Open(sqlite.Open(dbPath), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		return fmt.Errorf("open sqlite: %w", err)
	}
	if err := DB.Exec("PRAGMA journal_mode=WAL").Error; err != nil {
		return err
	}
	if err := DB.Exec("PRAGMA busy_timeout=5000").Error; err != nil {
		return err
	}
	if err := DB.Exec("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)").Error; err != nil {
		return err
	}
	var applied int64
	DB.Raw("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").Scan(&applied)
	if applied < schemaVersion {
		if err := DB.Transaction(func(tx *gorm.DB) error {
			for version := int(applied) + 1; version <= schemaVersion; version++ {
				if err := migrateSchema(tx, version); err != nil {
					return fmt.Errorf("migration %d: %w", version, err)
				}
				if err := tx.Exec("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", version, time.Now().UTC()).Error; err != nil {
					return err
				}
			}
			return nil
		}); err != nil {
			return fmt.Errorf("apply schema migrations: %w", err)
		}
	}
	return nil
}

func Backup(dir string) (string, error) {
	if DB == nil {
		return "", fmt.Errorf("database is not initialized")
	}
	if strings.TrimSpace(dir) == "" {
		dir = strings.TrimSpace(os.Getenv("BACKUP_DIR"))
	}
	if dir == "" {
		dir = "backups"
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	if err := DB.Exec("PRAGMA wal_checkpoint(TRUNCATE)").Error; err != nil {
		return "", err
	}
	dbPath := strings.TrimSpace(os.Getenv("DB_PATH"))
	if dbPath == "" {
		dbPath = "omniroute.db"
	}
	dest := filepath.Join(dir, "omniroute-"+time.Now().UTC().Format("20060102T150405.000000000Z")+".db")
	// VACUUM INTO asks SQLite for a transactionally consistent snapshot, even
	// when the gateway is serving concurrent writes. Fall back to a checkpointed
	// copy for older SQLite builds that do not support the statement.
	if err := DB.Exec("VACUUM INTO ?", dest).Error; err != nil {
		_ = os.Remove(dest)
		in, openErr := os.Open(dbPath)
		if openErr != nil {
			return "", openErr
		}
		out, createErr := os.OpenFile(dest, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
		if createErr != nil {
			_ = in.Close()
			return "", createErr
		}
		_, copyErr := io.Copy(out, in)
		_ = in.Close()
		if copyErr == nil {
			copyErr = out.Sync()
		}
		closeErr := out.Close()
		if copyErr != nil {
			_ = os.Remove(dest)
			return "", copyErr
		}
		if closeErr != nil {
			_ = os.Remove(dest)
			return "", closeErr
		}
	}
	if err := validateSQLiteFile(dest); err != nil {
		_ = os.Remove(dest)
		return "", err
	}
	cleanupBackups(dir)
	return dest, nil
}

func cleanupBackups(dir string) {
	days, err := strconv.Atoi(strings.TrimSpace(os.Getenv("BACKUP_RETENTION_DAYS")))
	if err != nil || days <= 0 {
		return
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	cutoff := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasPrefix(entry.Name(), "omniroute-") || !strings.HasSuffix(entry.Name(), ".db") {
			continue
		}
		if info, err := entry.Info(); err == nil && info.ModTime().Before(cutoff) {
			_ = os.Remove(filepath.Join(dir, entry.Name()))
		}
	}
}

func RestoreBackup(source string) error {
	source = filepath.Clean(strings.TrimSpace(source))
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	header := make([]byte, 16)
	if _, err := io.ReadFull(in, header); err != nil || string(header) != "SQLite format 3\x00" {
		return fmt.Errorf("invalid sqlite backup")
	}
	dbPath := strings.TrimSpace(os.Getenv("DB_PATH"))
	if dbPath == "" {
		dbPath = "omniroute.db"
	}
	tmp := dbPath + ".restore-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	if _, err = in.Seek(0, io.SeekStart); err != nil {
		_ = out.Close()
		_ = os.Remove(tmp)
		return err
	}
	if _, err = io.Copy(out, in); err != nil {
		_ = out.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err = out.Sync(); err != nil {
		_ = out.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err = out.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := validateSQLiteFile(tmp); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	_ = in.Close()

	if DB != nil {
		if sqlDB, closeErr := DB.DB(); closeErr == nil {
			_ = sqlDB.Close()
		}
		DB = nil
	}
	// Windows cannot atomically rename over an existing file. Keep a local
	// pre-restore copy so a failed replacement can be rolled back safely.
	previous := dbPath + ".before-restore-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	hadPrevious := false
	if _, statErr := os.Stat(dbPath); statErr == nil {
		if err := os.Rename(dbPath, previous); err != nil {
			_ = os.Remove(tmp)
			return err
		}
		hadPrevious = true
	}
	if err := os.Rename(tmp, dbPath); err != nil {
		if hadPrevious {
			_ = os.Rename(previous, dbPath)
		}
		_ = os.Remove(tmp)
		return err
	}
	_ = os.Remove(dbPath + "-wal")
	_ = os.Remove(dbPath + "-shm")
	return nil
}

// validateSQLiteFile opens a candidate database read-only and runs SQLite's
// integrity check before it is exposed as the live store.
func validateSQLiteFile(path string) error {
	check, err := gorm.Open(sqlite.Open(path+"?mode=ro"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		return fmt.Errorf("open sqlite backup: %w", err)
	}
	sqlDB, err := check.DB()
	if err != nil {
		return err
	}
	defer sqlDB.Close()
	var result string
	if err := check.Raw("PRAGMA integrity_check").Scan(&result).Error; err != nil {
		return err
	}
	if strings.ToLower(strings.TrimSpace(result)) != "ok" {
		return fmt.Errorf("sqlite integrity check failed: %s", result)
	}
	return nil
}
