// Package photos 实现"用户实拍照片"的存储与合规核心层（P1）。
//
// 设计要点（都是踩过的坑，写在这里免得后面被改坏）：
//  1. **按 magic bytes 判类型**，不看扩展名也不信 Content-Type —— 上传方说什么都不算数；
//  2. **解码后重新编码**（jpeg 质量 85、最长边 1600）—— 这一步顺手把 EXIF 全部丢掉：
//     手机照片常带 GPS 定位，那是隐私泄漏的高频点；
//  3. **默认待审（pending）**：上传后只有上传者自己看得见，审核通过才进公共图库；
//  4. 配额与速率由纯函数判定，方便单测（也方便以后换存储实现）；
//  5. 文件名用内容的 sha256 —— 天然去重，且不暴露原始文件名（原始名里常有个人信息）。
package photos

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/png" // 注册 PNG 解码器（image.Decode 靠它认 PNG）
	"os"
	"path/filepath"
	"strings"
	"time"
)

// 允许的图片类型（只收能在标准库里解出来的两种：jpeg/png）。
// webp 需要 golang.org/x/image，暂不引入依赖；如实拒绝，比默默失败好。
const (
	MimeJPEG = "image/jpeg"
	MimePNG  = "image/png"
)

const (
	MaxUploadBytes  = 5 << 20 // 单张上限 5MB
	MaxDimension    = 1600    // 规范化后最长边
	JPEGQuality     = 85
	DailyQuota      = 20 // 每个上传者每天最多 20 张
	TotalQuota      = 200
	StatusPending   = "pending"
	StatusApproved  = "approved"
	StatusRejected  = "rejected"
)

var (
	ErrUnsupportedType = errors.New("只支持 JPEG/PNG 图片")
	ErrTooLarge        = errors.New("图片超过 5MB")
	ErrNotAnImage      = errors.New("文件不是可解码的图片")
	ErrDailyQuota      = errors.New("今天上传的照片已达上限")
	ErrTotalQuota      = errors.New("照片总量已达上限，先删掉一些旧照片")
)

// Photo 是一条照片记录。P1 只落文件，DB 落库在下一批接。
type Photo struct {
	ID         string    `json:"id"`
	NodeName   string    `json:"node_name"`
	TripID     string    `json:"trip_id,omitempty"`
	UploaderID string    `json:"uploader_id"`
	Sha256     string    `json:"sha256"`
	Mime       string    `json:"mime"`
	Bytes      int       `json:"bytes"`
	Width      int       `json:"width"`
	Height     int       `json:"height"`
	Status     string    `json:"status"`
	// UploaderIDs 所有上传过这份内容的人。内容寻址去重时**别的用户上传同一张图就追加进来**，
	// 而不是被当成"不存在"丢给第一个上传者 —— 否则第二个上传者既看不到也取不到"自己"的照片
	// （实测 2026-09-19：同一张图两次上传得到同一个 id，第二个人的 /my/photos 是空的、取图 403）。
	UploaderIDs []string `json:"uploader_ids,omitempty"`
	// RejectReason 拒绝理由**必须回传给上传者**（不做黑箱审核）；举报会先退回复审。
	RejectReason  string `json:"reject_reason,omitempty"`
	ReportedCount int    `json:"reported_count,omitempty"`
	CreatedAt     time.Time `json:"created_at"`
}

// OwnerIDs 返回这份内容的所有上传者（兼容只有 UploaderID 的旧记录）。
func (p Photo) OwnerIDs() []string {
	if len(p.UploaderIDs) > 0 {
		return p.UploaderIDs
	}
	if p.UploaderID != "" {
		return []string{p.UploaderID}
	}
	return nil
}

// DetectMime 按文件头判断类型；不是受支持的图片就返回 ErrUnsupportedType。
func DetectMime(data []byte) (string, error) {
	if len(data) < 12 {
		return "", ErrUnsupportedType
	}
	switch {
	case data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF:
		return MimeJPEG, nil
	case bytes.HasPrefix(data, []byte{0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A}):
		return MimePNG, nil
	case bytes.HasPrefix(data, []byte("RIFF")) && bytes.Equal(data[8:12], []byte("WEBP")):
		// 明确告知不支持，而不是当成坏文件
		return "", fmt.Errorf("%w（webp 请先转成 JPG/PNG）", ErrUnsupportedType)
	}
	return "", ErrUnsupportedType
}

// Normalize 校验并规范化图片：解码 → 限制最长边 → 重新编码为 JPEG（EXIF 随之被丢弃）。
// 返回规范化后的字节、宽高与内容 sha256。
func Normalize(data []byte) (normalized []byte, width, height int, digest string, err error) {
	if len(data) > MaxUploadBytes {
		return nil, 0, 0, "", ErrTooLarge
	}
	if _, err := DetectMime(data); err != nil {
		return nil, 0, 0, "", err
	}
	img, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, 0, 0, "", ErrNotAnImage
	}
	bounds := img.Bounds()
	w, h := bounds.Dx(), bounds.Dy()
	if w <= 0 || h <= 0 {
		return nil, 0, 0, "", ErrNotAnImage
	}
	scaled := img
	if w > MaxDimension || h > MaxDimension {
		scaled = nearestScale(img, MaxDimension)
		w, h = scaled.Bounds().Dx(), scaled.Bounds().Dy()
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, scaled, &jpeg.Options{Quality: JPEGQuality}); err != nil {
		return nil, 0, 0, "", fmt.Errorf("图片重新编码失败: %w", err)
	}
	sum := sha256.Sum256(buf.Bytes())
	return buf.Bytes(), w, h, hex.EncodeToString(sum[:]), nil
}

// nearestScale 用最近邻缩放（标准库够用，不引依赖）。
func nearestScale(src image.Image, maxSide int) image.Image {
	bounds := src.Bounds()
	w, h := bounds.Dx(), bounds.Dy()
	ratio := float64(maxSide) / float64(w)
	if h > w {
		ratio = float64(maxSide) / float64(h)
	}
	newW, newH := int(float64(w)*ratio), int(float64(h)*ratio)
	if newW < 1 {
		newW = 1
	}
	if newH < 1 {
		newH = 1
	}
	dst := image.NewRGBA(image.Rect(0, 0, newW, newH))
	for y := 0; y < newH; y++ {
		for x := 0; x < newW; x++ {
			sx := bounds.Min.X + x*w/newW
			sy := bounds.Min.Y + y*h/newH
			dst.Set(x, y, src.At(sx, sy))
		}
	}
	return dst
}

// CheckQuota 配额判定（纯函数，方便单测）：按上传者统计当天张数与总量。
func CheckQuota(existing []Photo, uploaderID string, now time.Time) error {
	daily, total := 0, 0
	day := now.UTC().Format("2006-01-02")
	for _, item := range existing {
		if item.UploaderID != uploaderID {
			continue
		}
		total++
		if item.CreatedAt.UTC().Format("2006-01-02") == day {
			daily++
		}
	}
	if daily >= DailyQuota {
		return ErrDailyQuota
	}
	if total >= TotalQuota {
		return ErrTotalQuota
	}
	return nil
}

// VisibleTo 可见性：**审核通过的对所有人可见；pending/rejected 只有上传者自己看得见**。
// 这是"默认待审"的核心保证 —— 未审核内容不可能被别的用户看到。
func VisibleTo(photo Photo, viewerID string) bool {
	if photo.Status == StatusApproved {
		return true
	}
	if viewerID == "" {
		return false
	}
	for _, owner := range photo.OwnerIDs() {
		if owner == viewerID {
			return true
		}
	}
	return false
}

// Save 规范化并落盘，返回记录（状态固定为 pending）。filename 用 sha256，天然去重。
func Save(dir, uploaderID, nodeName, tripID string, data []byte, now time.Time) (Photo, error) {
	normalized, w, h, digest, err := Normalize(data)
	if err != nil {
		return Photo{}, err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return Photo{}, fmt.Errorf("创建照片目录失败: %w", err)
	}
	path := filepath.Join(dir, digest+".jpg")
	if err := os.WriteFile(path, normalized, 0o644); err != nil {
		return Photo{}, fmt.Errorf("照片写入失败: %w", err)
	}
	return Photo{
		ID:         digest,
		NodeName:   strings.TrimSpace(nodeName),
		TripID:     tripID,
		UploaderID: uploaderID,
		Sha256:     digest,
		Mime:       MimeJPEG,
		Bytes:      len(normalized),
		Width:      w,
		Height:     h,
		Status:     StatusPending,
		CreatedAt:  now.UTC(),
	}, nil
}

// PathFor 返回某个 id 对应的文件路径（id 必须是纯 sha256，防止路径穿越）。
func PathFor(dir, id string) (string, error) {
	clean := strings.ToLower(strings.TrimSpace(id))
	if len(clean) != 64 {
		return "", errors.New("照片 id 不合法")
	}
	for _, r := range clean {
		if !strings.ContainsRune("0123456789abcdef", r) {
			return "", errors.New("照片 id 不合法")
		}
	}
	return filepath.Join(dir, clean+".jpg"), nil
}
