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

// Read is a separate connection pool for read-only queries. SQLite in WAL mode
// allows concurrent readers alongside one writer, so reads do not have to queue
// behind the single write connection. `Reader()` returns DB when no separate
// pool exists (in-memory databases and tests), which keeps call sites safe.
var Read *gorm.DB

// readOwner records which write handle Read was opened for. Tests routinely
// replace database.DB with a fresh in-memory handle; without this guard a reader
// pool left over from an earlier InitDB call would keep querying the old
// database and the test would silently assert against stale rows.
var readOwner *gorm.DB

// sqliteReadConns bounds the reader pool. SQLite readers are cheap but each one
// is a file handle; four is enough to keep page loads and analytics from
// serialising behind each other without exhausting handles on small hosts.
const sqliteReadConns = 4

// Increment this value when a schema change needs an explicit, auditable
// migration step. Migrations are applied one version at a time so a partially
// upgraded deployment can resume safely on the next start.
const schemaVersion = 23

// Reader returns the pool that read-only queries should use. Writes must go
// through DB, which has exactly one connection.
func Reader() *gorm.DB {
	if Read != nil && readOwner == DB {
		return Read
	}
	return DB
}

// sqliteBusyTimeoutMs is how long SQLite waits for the write lock before
// returning SQLITE_BUSY. It must be set through the DSN rather than a one-off
// `PRAGMA` statement: busy_timeout is a *per-connection* setting, so executing it
// once through GORM only configures whichever pooled connection served that
// statement, leaving every other connection with the default.
const sqliteBusyTimeoutMs = 5000

// sqliteDSN attaches the per-connection pragmas the gateway relies on.
//
// mattn/go-sqlite3 parses these query parameters itself and applies them inside
// driver.Open, i.e. on every new pooled connection (see sqlite3.go:1199-1216 for
// _busy_timeout and :1304-1315 for _journal_mode). Parameters the caller already
// supplied win, so an explicit DB_PATH DSN can still override them.
func sqliteDSN(path string) string {
	pragmas := []string{
		"_busy_timeout=" + strconv.Itoa(sqliteBusyTimeoutMs),
		// WAL is what the concurrent read paths and the backup/retention loops
		// were written against. It is persisted in the database file, so setting
		// it per connection is cheap and idempotent.
		"_journal_mode=WAL",
		// Foreign keys must be enforced on *every* connection. SQLite defaults
		// this to OFF per connection, so a one-off PRAGMA is not enough.
		"_foreign_keys=1",
	}
	dsn := path
	for _, pragma := range pragmas {
		key := pragma[:strings.Index(pragma, "=")]
		if strings.Contains(dsn, key+"=") {
			continue // caller already specified it
		}
		if strings.Contains(dsn, "?") {
			dsn += "&" + pragma
		} else {
			dsn += "?" + pragma
		}
	}
	return dsn
}

// isMemoryDSN reports whether the DSN targets an in-memory database. An in-memory
// SQLite database only exists while a connection is open, so its pool must not be
// allowed to drop to zero idle connections between statements.
func isMemoryDSN(path string) bool {
	lower := strings.ToLower(path)
	return strings.Contains(lower, ":memory:") || strings.Contains(lower, "mode=memory")
}

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
	case 8:
		// Community feed reads aggregate comments and favourites per post. Both
		// child tables only carried the composite unique index on
		// (post_id, user_id), whose leading column does serve a lookup by
		// post_id, but the feed also filters favourites by user across a page of
		// posts, so add a purpose-built index for that direction.
		if err := tx.Exec("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id)").Error; err != nil {
			return err
		}
		if err := tx.Exec("CREATE INDEX IF NOT EXISTS idx_post_favorites_post_id ON post_favorites(post_id)").Error; err != nil {
			return err
		}
		return tx.Exec("CREATE INDEX IF NOT EXISTS idx_post_favorites_user_id ON post_favorites(user_id)").Error
	case 9:
		return tx.AutoMigrate(&models.RiskNotification{})
	case 10:
		return tx.AutoMigrate(&models.RiskNotification{})
	case 11:
		return tx.AutoMigrate(&models.WebPushSubscription{})
	case 12:
		return tx.AutoMigrate(&models.ModelAuditEvent{})
	case 13:
		return tx.AutoMigrate(&models.ModelAuditEvent{})
	case 14:
		return tx.AutoMigrate(&models.ProviderQualityAlertState{})
	case 15:
		return tx.AutoMigrate(&models.ProviderQualityAlertDelivery{})
	case 16:
		return tx.AutoMigrate(&models.ProviderQualityAlertDelivery{})
	case 17:
		// 参考路线画像：UserPreference 新增 reference_profile / reference_count 两列。
		// ⚠️ 这套 schema 是**版本化**的：只改模型不会给既有库加列（v1 建表之后就不再 AutoMigrate），
		// 必须像这里一样显式加一个版本，否则线上会出现"保存参考路线失败"（列不存在）。
		return tx.AutoMigrate(&models.UserPreference{})
	case 18:
		// 设备会话与密码重置令牌：短期 access token + 轮换 refresh token、
		// 单设备/全设备退出、改密与找回密码都依赖这两张表。
		return tx.AutoMigrate(&models.DeviceSession{}, &models.PasswordResetToken{})
	case 19:
		// 金额从 REAL 迁移到整数分，并删除旧的浮点列。
		return migrateMoneyToCents(tx)
	case 20:
		// 清理孤儿数据后重建相关表，真正打开外键约束（含删除策略）。
		return migrateForeignKeys(tx)
	case 21:
		// 高频写入/读取路径的复合索引。此前这些表只有单列索引，按
		// (user_id, created_at) / (event_type, created_at) 过滤并排序时会先回表
		// 再排序；SQLite 单写模型下，这会把写锁持有时间放大。
		return migrateCompositeIndexes(tx)
	case 22:
		// 临时账号：User 新增 is_guest / last_active_at 两列，
		// 既有库靠这次迁移补列，早期 guest_ 前缀的历史账号由 username 兜底识别。
		return tx.AutoMigrate(&models.User{})
	case 23:
		// 特征图谱的节点操作：UserPreference 新增 trait_weights / trait_hidden 两列，
		// 用来持久化「权重新标定」与「移出节点」，否则刷新就还原。
		return tx.AutoMigrate(&models.UserPreference{})
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
	DB, err = gorm.Open(sqlite.Open(sqliteDSN(dbPath)), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		return fmt.Errorf("open sqlite: %w", err)
	}

	// SQLite allows exactly one writer. With database/sql's defaults (unlimited
	// open connections, 2 idle) concurrent writers contend for the write lock and
	// surface as SQLITE_BUSY / 500s instead of queueing, and open file
	// descriptors scale with concurrency. One connection serialises access in Go,
	// where a bounded wait is far easier to reason about than an error the caller
	// has to interpret.
	sqlDB, err := DB.DB()
	if err != nil {
		return fmt.Errorf("sqlite pool: %w", err)
	}
	sqlDB.SetMaxOpenConns(1)
	if isMemoryDSN(dbPath) {
		// Keep the single in-memory connection alive; closing it would discard
		// the schema and every row.
		sqlDB.SetMaxIdleConns(1)
		sqlDB.SetConnMaxLifetime(0)
	} else {
		// A file-backed database survives connection churn, so idle connections
		// are not needed. This also keeps backups (VACUUM INTO) from racing a
		// lingering idle handle on Windows, where an open handle blocks deletes.
		sqlDB.SetMaxIdleConns(0)
	}

	// WAL and busy_timeout are applied per connection by the DSN above. The
	// journal_mode statement is kept as a belt-and-braces assertion for DSNs the
	// caller supplied verbatim: WAL is durable in the file, so doing it once is
	// sufficient here.
	if err := DB.Exec("PRAGMA journal_mode=WAL").Error; err != nil {
		return err
	}

	// A second, read-only pool lets SELECTs run while the single write
	// connection is busy. In-memory databases cannot do this: their schema only
	// exists while the one open connection is alive, so Reader() falls back to DB
	// there (and in every test that assigns database.DB directly).
	if !isMemoryDSN(dbPath) {
		Read, err = gorm.Open(sqlite.Open(sqliteDSN(dbPath)), &gorm.Config{
			Logger: logger.Default.LogMode(logger.Silent),
			// A ping during Open leaves one idle reader handle alive. On Windows
			// that handle keeps the database file locked and test cleanup (or a
			// backup) cannot delete it. The pool is therefore opened lazily and
			// configured below to retain no idle connections.
			DisableAutomaticPing: true,
		})
		if err != nil {
			return fmt.Errorf("open sqlite reader: %w", err)
		}
		readSQL, err := Read.DB()
		if err != nil {
			return fmt.Errorf("sqlite reader pool: %w", err)
		}
		readSQL.SetMaxOpenConns(sqliteReadConns)
		// No idle readers: SQLite lets readers coexist with the writer, but an
		// idle handle still pins the file. Opening a connection per read burst is
		// cheap; leaking a handle is not.
		readSQL.SetMaxIdleConns(0)
		readOwner = DB
	} else {
		Read = DB
		readOwner = DB
	}
	if err := DB.Exec("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)").Error; err != nil {
		return err
	}
	var applied int64
	DB.Raw("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").Scan(&applied)
	if applied < schemaVersion {
		// v20 rebuilds every FK-bearing table. Keep a snapshot before any
		// destructive DDL; an empty database needs none.
		if applied > 0 && applied < 20 && !isMemoryDSN(dbPath) &&
			!strings.EqualFold(strings.TrimSpace(os.Getenv("SKIP_MIGRATION_BACKUP")), "true") {
			if _, err := Backup("backups/pre-fk-migration"); err != nil {
				return fmt.Errorf("pre-migration backup: %w", err)
			}
		}
		// SQLite can only change `foreign_keys` outside a transaction. The DSN
		// turns it on per connection, but the table rebuild must run with it off
		// so cleanup and copy order cannot fail midway.
		if err := DB.Exec("PRAGMA foreign_keys=OFF").Error; err != nil {
			return err
		}
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
	// Assert enforcement on the serving connection. New connections receive it
	// from the DSN as well.
	if err := DB.Exec("PRAGMA foreign_keys=ON").Error; err != nil {
		return err
	}
	if applied < schemaVersion {
		if violations, err := ForeignKeyReport(); err != nil {
			return fmt.Errorf("post-migration integrity check: %w", err)
		} else if violations > 0 {
			return fmt.Errorf("post-migration integrity check found %d violations", violations)
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
