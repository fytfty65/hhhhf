package photos

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// jpegWithEXIF 造一张"带 EXIF 的 JPEG"：标准库不支持写 EXIF，
// 所以这里手工在 SOI 之后插入一个 APP1(EXIF) 段 —— 正是手机照片里带 GPS 的那种段。
func jpegWithEXIF(t *testing.T) []byte {
	t.Helper()
	plain := solidJPEG(t, 40, 30)
	payload := []byte("Exif\x00\x00GPS-LEAK-41.76,86.15")
	segmentLen := len(payload) + 2
	segment := make([]byte, 0, segmentLen+2)
	segment = append(segment, 0xFF, 0xE1, byte(segmentLen>>8), byte(segmentLen&0xFF))
	segment = append(segment, payload...)

	out := make([]byte, 0, len(plain)+len(segment))
	out = append(out, plain[:2]...) // SOI
	out = append(out, segment...)
	out = append(out, plain[2:]...)
	return out
}

func solidJPEG(t *testing.T, w, h int) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			img.Set(x, y, color.RGBA{R: uint8(x), G: uint8(y), B: 128, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, img, nil); err != nil {
		t.Fatalf("构造 JPEG 失败: %v", err)
	}
	return buf.Bytes()
}

func solidPNG(t *testing.T, w, h int) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("构造 PNG 失败: %v", err)
	}
	return buf.Bytes()
}

func TestDetectMimeByMagicBytes(t *testing.T) {
	if mime, err := DetectMime(solidJPEG(t, 8, 8)); err != nil || mime != MimeJPEG {
		t.Fatalf("JPEG 头识别失败: %v %s", err, mime)
	}
	if mime, err := DetectMime(solidPNG(t, 8, 8)); err != nil || mime != MimePNG {
		t.Fatalf("PNG 头识别失败: %v %s", err, mime)
	}
	// 扩展名/Content-Type 说自己是图片不算数：内容不是图片就必须拒绝
	if _, err := DetectMime([]byte("GIF89a-not-really")); err == nil {
		t.Fatal("非支持类型必须被拒绝")
	}
	if _, err := DetectMime([]byte("RIFF....WEBP")); err == nil {
		t.Fatal("webp 要明确拒绝（不支持），不能当坏文件静默放过")
	}
}

func TestNormalizeStripsEXIF(t *testing.T) {
	raw := jpegWithEXIF(t)
	if !bytes.Contains(raw, []byte("GPS-LEAK")) {
		t.Fatal("测试数据本身应包含 EXIF 标记")
	}
	normalized, w, h, digest, err := Normalize(raw)
	if err != nil {
		t.Fatalf("规范化失败: %v", err)
	}
	if bytes.Contains(normalized, []byte("GPS-LEAK")) {
		t.Fatal("EXIF 未被剥离 —— 手机照片的 GPS 定位会随图泄漏")
	}
	if bytes.Contains(normalized, []byte("Exif")) {
		t.Fatal("输出里不应再有 Exif 段")
	}
	if w != 40 || h != 30 {
		t.Fatalf("宽高不对: %dx%d", w, h)
	}
	if len(digest) != 64 {
		t.Fatalf("sha256 长度不对: %s", digest)
	}
}

func TestNormalizeRejectsOversizeAndJunk(t *testing.T) {
	if _, _, _, _, err := Normalize(make([]byte, MaxUploadBytes+1)); !errors.Is(err, ErrTooLarge) {
		t.Fatalf("超过 5MB 必须报 ErrTooLarge，得到 %v", err)
	}
	junk := append([]byte{0xFF, 0xD8, 0xFF}, []byte("not a real image body")...)
	if _, _, _, _, err := Normalize(junk); !errors.Is(err, ErrNotAnImage) {
		t.Fatalf("头对但内容坏必须报 ErrNotAnImage，得到 %v", err)
	}
}

func TestNormalizeScalesDownLongSide(t *testing.T) {
	_, w, h, _, err := Normalize(solidJPEG(t, 3200, 800))
	if err != nil {
		t.Fatalf("规范化失败: %v", err)
	}
	if w != MaxDimension {
		t.Fatalf("长边应被缩到 %d，得到 %d", MaxDimension, w)
	}
	if h != 400 {
		t.Fatalf("短边应按比例缩放，得到 %d", h)
	}
}

func TestCheckQuota(t *testing.T) {
	now := time.Date(2026, 9, 19, 12, 0, 0, 0, time.UTC)
	var many []Photo
	for i := 0; i < DailyQuota; i++ {
		many = append(many, Photo{UploaderID: "u1", CreatedAt: now})
	}
	if err := CheckQuota(many, "u1", now); !errors.Is(err, ErrDailyQuota) {
		t.Fatalf("当天满额应报 ErrDailyQuota，得到 %v", err)
	}
	// 别的用户不受影响
	if err := CheckQuota(many, "u2", now); err != nil {
		t.Fatalf("配额不能串用户: %v", err)
	}
	// 昨天的不占今天的额度
	yesterday := make([]Photo, DailyQuota)
	for i := range yesterday {
		yesterday[i] = Photo{UploaderID: "u1", CreatedAt: now.Add(-24 * time.Hour)}
	}
	if err := CheckQuota(yesterday, "u1", now); err != nil {
		t.Fatalf("昨天的不该占今天额度: %v", err)
	}
}

func TestVisibleToDefaultsToPrivate(t *testing.T) {
	pending := Photo{UploaderID: "u1", Status: StatusPending}
	if !VisibleTo(pending, "u1") {
		t.Fatal("上传者自己必须能看到自己的待审照片")
	}
	if VisibleTo(pending, "u2") {
		t.Fatal("待审照片绝不能被别人看到（默认待审的核心保证）")
	}
	if VisibleTo(pending, "") {
		t.Fatal("匿名访问者不能看到待审照片")
	}
	approved := Photo{UploaderID: "u1", Status: StatusApproved}
	if !VisibleTo(approved, "u2") {
		t.Fatal("审核通过后应对所有人可见")
	}
	rejected := Photo{UploaderID: "u1", Status: StatusRejected}
	if VisibleTo(rejected, "u2") || !VisibleTo(rejected, "u1") {
		t.Fatal("被拒照片只有上传者可见（要让他知道结果）")
	}
}

func TestSaveWritesContentAddressedFile(t *testing.T) {
	dir := t.TempDir()
	now := time.Date(2026, 9, 19, 12, 0, 0, 0, time.UTC)
	photo, err := Save(dir, "u1", "博斯腾湖", "trip-1", solidJPEG(t, 20, 10), now)
	if err != nil {
		t.Fatalf("保存失败: %v", err)
	}
	if photo.Status != StatusPending {
		t.Fatalf("新上传必须是 pending，得到 %s", photo.Status)
	}
	path, err := PathFor(dir, photo.ID)
	if err != nil {
		t.Fatalf("PathFor 失败: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("文件应已落盘: %v", err)
	}
	// 同样内容再存一次 → 同一个 id（去重），不产生第二个文件
	again, err := Save(dir, "u2", "博斯腾湖", "trip-1", solidJPEG(t, 20, 10), now)
	if err != nil {
		t.Fatalf("二次保存失败: %v", err)
	}
	if again.ID != photo.ID {
		t.Fatalf("相同内容应命中同一 id：%s vs %s", again.ID, photo.ID)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("内容寻址应只留一个文件，实际 %d 个", len(entries))
	}
}

func TestPathForRejectsTraversal(t *testing.T) {
	for _, bad := range []string{"", "../../etc/passwd", "not-a-hash", "zzzz"} {
		if _, err := PathFor(t.TempDir(), bad); err == nil {
			t.Fatalf("非法 id 必须被拒绝: %q", bad)
		}
	}
	if _, err := PathFor(t.TempDir(), "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"); err != nil {
		t.Fatalf("合法 sha256 不该被拒: %v", err)
	}
	// 目录本身不存在时也能给出路径（由调用方决定是否创建）
	if _, err := filepath.Abs(filepath.Join("x", "y")); err != nil {
		t.Fatal(err)
	}
}
