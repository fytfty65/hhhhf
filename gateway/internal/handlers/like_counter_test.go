package handlers

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"gateway/internal/database"
	"gateway/internal/models"

	"github.com/gin-gonic/gin"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// Regression tests for the community like counter.
//
// The original implementation was read-modify-write:
//
//	var post models.CommunityPost
//	DB.Where("id = ?", id).First(&post)
//	post.Likes = post.Likes + 1
//	DB.Save(&post)
//
// Two concurrent likes load the same value and one increment is silently lost.
// The fix pushes the arithmetic into a single UPDATE, so the database owns the
// read and the write.

func likeTestRouter(t *testing.T, postID string) *gin.Engine {
	t.Helper()
	old := database.DB
	// File-backed, not ":memory:". A memory database lives only as long as its
	// connection, and the pool recycles idle connections (SetMaxIdleConns(0)),
	// so the schema would vanish between the seeding step and the assertion and
	// the test would fail with "no such table" for reasons unrelated to the
	// handler. A temp file survives the churn and is removed by t.TempDir.
	db, err := gorm.Open(sqlite.Open(filepath.Join(t.TempDir(), "likes.db")), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	database.DB = db
	t.Cleanup(func() {
		// Close before t.TempDir removes the file: an open SQLite handle blocks
		// the delete on Windows and turns into a confusing cleanup failure.
		if sqlDB, err := db.DB(); err == nil && sqlDB != nil {
			_ = sqlDB.Close()
		}
		database.DB = old
	})
	if err := db.AutoMigrate(&models.CommunityPost{}); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&models.CommunityPost{ID: postID, Title: "帖", Content: "内容", UserID: "author"}).Error; err != nil {
		t.Fatal(err)
	}

	router := gin.New()
	router.POST("/like", LikePostHandler)
	return router
}

func postLike(t *testing.T, r *gin.Engine, postID string) int {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"post_id": postID})
	req := httptest.NewRequest(http.MethodPost, "/like", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	r.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("like returned %d: %s", res.Code, res.Body.String())
	}
	var out struct {
		Likes int `json:"likes"`
	}
	if err := json.Unmarshal(res.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode: %v (%s)", err, res.Body.String())
	}
	return out.Likes
}

// TestLikePostIncrementsAtomically drives concurrent likes and requires every
// increment to survive.
//
// Honest scope note: with the current pool configuration (SetMaxOpenConns(1))
// SQLite serialises these writers, so this test does not deterministically
// reproduce the original lost-update. Its value is as a contract plus a tripwire:
// it pins "N likes means N" and it *will* start failing if the pool is ever
// widened to allow parallel writers while the handler goes back to
// read-modify-write. The atomicity itself is enforced by the single UPDATE in the
// handler, which is what makes the guarantee independent of pool settings.
func TestLikePostIncrementsAtomically(t *testing.T) {
	const postID = "post-concurrent"
	router := likeTestRouter(t, postID)

	const workers = 8
	const perWorker = 6
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer wg.Done()
			for j := 0; j < perWorker; j++ {
				postLike(t, router, postID)
			}
		}()
	}
	wg.Wait()

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", postID).First(&post).Error; err != nil {
		t.Fatal(err)
	}
	want := workers * perWorker
	if post.Likes != want {
		t.Fatalf("lost update: expected %d likes, stored %d", want, post.Likes)
	}
}

// TestLikePostRepliesWithTheCommittedCount checks the response echoes the value
// actually persisted rather than a locally computed guess, which is what makes
// the number trustworthy to the client.
func TestLikePostRepliesWithTheCommittedCount(t *testing.T) {
	const postID = "post-echo"
	router := likeTestRouter(t, postID)

	for i := 1; i <= 4; i++ {
		if got := postLike(t, router, postID); got != i {
			t.Fatalf("response %d must report the committed count %d", got, i)
		}
	}

	var post models.CommunityPost
	if err := database.DB.Where("id = ?", postID).First(&post).Error; err != nil {
		t.Fatal(err)
	}
	if post.Likes != 4 {
		t.Fatalf("expected 4 stored likes, got %d", post.Likes)
	}
}

// TestLikePostUnknownIdIs404 keeps the existence check so a bad id is a clean
// not-found rather than a no-op "success".
func TestLikePostUnknownIdIs404(t *testing.T) {
	router := likeTestRouter(t, "post-exists")
	body, _ := json.Marshal(map[string]any{"post_id": "does-not-exist"})
	req := httptest.NewRequest(http.MethodPost, "/like", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	if res.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for an unknown post, got %d (%s)", res.Code, res.Body.String())
	}
}

// TestLikePostMissingBodyIsRejected covers the validation branch.
func TestLikePostMissingBodyIsRejected(t *testing.T) {
	router := likeTestRouter(t, "post-exists")
	req := httptest.NewRequest(http.MethodPost, "/like", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	if res.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for a missing post_id, got %d", res.Code)
	}
}
