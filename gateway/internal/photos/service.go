package photos

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

var (
	ErrDisabled  = errors.New("照片上传功能当前已关闭")
	ErrNotFound  = errors.New("照片不存在")
	ErrForbidden = errors.New("无权查看这张照片")
)

// Index 是照片记录的持久化索引（P1b 用 JSON 文件，够用且零依赖；等 P2 的审核队列需要
// 按状态/时间查询时再迁到 gateway 现有的 SQLite，迁移只需换这一层）。
type Index struct {
	mu      sync.Mutex
	path    string
	records []Photo
}

// OpenIndex 读取（或初始化）索引文件。
func OpenIndex(dir string) (*Index, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("创建照片目录失败: %w", err)
	}
	index := &Index{path: filepath.Join(dir, "index.json")}
	data, err := os.ReadFile(index.path)
	if err == nil && len(data) > 0 {
		if err := json.Unmarshal(data, &index.records); err != nil {
			// 索引坏了不能把整个功能拖垮，也不能假装没有照片：如实报错让调用方决定
			return nil, fmt.Errorf("照片索引损坏: %w", err)
		}
	}
	return index, nil
}

func (i *Index) All() []Photo {
	i.mu.Lock()
	defer i.mu.Unlock()
	out := make([]Photo, len(i.records))
	copy(out, i.records)
	return out
}

func (i *Index) ByID(id string) (Photo, bool) {
	i.mu.Lock()
	defer i.mu.Unlock()
	for _, item := range i.records {
		if item.ID == id {
			return item, true
		}
	}
	return Photo{}, false
}

// Add 追加一条记录并原子落盘（先写临时文件再 rename，避免半截 JSON）。
func (i *Index) Add(photo Photo) error {
	i.mu.Lock()
	defer i.mu.Unlock()
	for _, item := range i.records {
		if item.ID == photo.ID {
			return nil // 内容寻址：同一张图重复上传不重复记账
		}
	}
	i.records = append(i.records, photo)
	return i.flushLocked()
}

// SetStatus 审核状态流转（P2 管理端用；现在就把状态机固定下来）。
func (i *Index) SetStatus(id, status string) (Photo, error) {
	if status != StatusPending && status != StatusApproved && status != StatusRejected {
		return Photo{}, fmt.Errorf("非法状态: %s", status)
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	for position := range i.records {
		if i.records[position].ID == id {
			i.records[position].Status = status
			if err := i.flushLocked(); err != nil {
				return Photo{}, err
			}
			return i.records[position], nil
		}
	}
	return Photo{}, ErrNotFound
}

// Remove 删记录（文件由 Service 负责删，因为只有它知道目录约定）。
func (i *Index) Remove(id string) error {
	i.mu.Lock()
	defer i.mu.Unlock()
	kept := i.records[:0]
	found := false
	for _, item := range i.records {
		if item.ID == id {
			found = true
			continue
		}
		kept = append(kept, item)
	}
	if !found {
		return ErrNotFound
	}
	i.records = kept
	return i.flushLocked()
}

func (i *Index) flushLocked() error {
	payload, err := json.MarshalIndent(i.records, "", "  ")
	if err != nil {
		return err
	}
	tmp := i.path + ".tmp"
	if err := os.WriteFile(tmp, payload, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, i.path)
}

// Service 把核心层与索引串起来，并持有功能开关。
// 开关默认**打开**（功能可用），但环境变量可以一键关掉 —— 出问题时前端会退回"仅本机保存"。
type Service struct {
	dir     string
	index   *Index
	enabled bool
	now     func() time.Time
}

func NewService(dir string, enabled bool) (*Service, error) {
	index, err := OpenIndex(dir)
	if err != nil {
		return nil, err
	}
	return &Service{dir: dir, index: index, enabled: enabled, now: time.Now}, nil
}

func (s *Service) Enabled() bool { return s != nil && s.enabled }

// Upload 走完"配额 → 校验/规范化 → 落盘 → 记账"，返回 pending 记录。
func (s *Service) Upload(uploaderID, nodeName, tripID string, data []byte) (Photo, error) {
	if !s.Enabled() {
		return Photo{}, ErrDisabled
	}
	if uploaderID == "" {
		return Photo{}, errors.New("缺少上传者身份")
	}
	now := s.now().UTC()
	if err := CheckQuota(s.index.All(), uploaderID, now); err != nil {
		return Photo{}, err
	}
	photo, err := Save(s.dir, uploaderID, nodeName, tripID, data, now)
	if err != nil {
		return Photo{}, err
	}
	if err := s.index.Add(photo); err != nil {
		return Photo{}, err
	}
	return photo, nil
}

// ListMine 只返回上传者自己的照片（含待审），按时间倒序。
func (s *Service) ListMine(uploaderID string) []Photo {
	if !s.Enabled() || uploaderID == "" {
		return nil
	}
	var mine []Photo
	for _, item := range s.index.All() {
		if item.UploaderID == uploaderID {
			mine = append(mine, item)
		}
	}
	sort.Slice(mine, func(a, b int) bool { return mine[a].CreatedAt.After(mine[b].CreatedAt) })
	return mine
}

// Open 取某个 id 的可读文件路径：先判可见性，再判文件真的在。
// 返回 (记录, 路径, error)。
func (s *Service) Open(id, viewerID string) (Photo, string, error) {
	if !s.Enabled() {
		return Photo{}, "", ErrDisabled
	}
	photo, ok := s.index.ByID(id)
	if !ok {
		return Photo{}, "", ErrNotFound
	}
	if !VisibleTo(photo, viewerID) {
		// 借"没有权限"回答，不泄漏"这张图存在但你没资格看"
		return Photo{}, "", ErrForbidden
	}
	path, err := PathFor(s.dir, photo.ID)
	if err != nil {
		return Photo{}, "", ErrNotFound
	}
	if _, err := os.Stat(path); err != nil {
		return Photo{}, "", ErrNotFound
	}
	return photo, path, nil
}

// Moderate 审核（P2 管理端调用）：通过/拒绝。
func (s *Service) Moderate(id, status string) (Photo, error) {
	if !s.Enabled() {
		return Photo{}, ErrDisabled
	}
	return s.index.SetStatus(id, status)
}

// Delete 删除照片：先删记录再删文件（反之会留下无主的图，占着磁盘还查不到）。
func (s *Service) Delete(id, requesterID string, isAdmin bool) error {
	if !s.Enabled() {
		return ErrDisabled
	}
	photo, ok := s.index.ByID(id)
	if !ok {
		return ErrNotFound
	}
	if !isAdmin && photo.UploaderID != requesterID {
		return ErrForbidden
	}
	if err := s.index.Remove(id); err != nil {
		return err
	}
	if path, err := PathFor(s.dir, id); err == nil {
		_ = os.Remove(path)
	}
	return nil
}
