package photos

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
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
// 命中已有内容（同 sha256）时**把新上传者追加进 UploaderIDs**，而不是丢弃 ——
// 内容只存一份（去重收益保留），但"谁上传过"必须记全，否则第二个人会被锁在"自己"的照片外面。
// 返回最终生效的记录（调用方要用它，不能再用入参那份）。
func (i *Index) Add(photo Photo) (Photo, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	for position := range i.records {
		if i.records[position].ID != photo.ID {
			continue
		}
		existing := &i.records[position]
		if photo.UploaderID != "" {
			known := false
			for _, owner := range existing.OwnerIDs() {
				if owner == photo.UploaderID {
					known = true
					break
				}
			}
			if !known {
				existing.UploaderIDs = append(existing.OwnerIDs(), photo.UploaderID)
				if existing.UploaderID == "" {
					existing.UploaderID = photo.UploaderID
				}
				if err := i.flushLocked(); err != nil {
					return Photo{}, err
				}
			}
		}
		return *existing, nil
	}
	if photo.UploaderID != "" && len(photo.UploaderIDs) == 0 {
		photo.UploaderIDs = []string{photo.UploaderID}
	}
	i.records = append(i.records, photo)
	return photo, i.flushLocked()
}

// SetStatus 审核状态流转（P2 管理端用）。`reason` 只在拒绝时有意义，会回传给上传者。
func (i *Index) SetStatus(id, status, reason string) (Photo, error) {
	if status != StatusPending && status != StatusApproved && status != StatusRejected {
		return Photo{}, fmt.Errorf("非法状态: %s", status)
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	for position := range i.records {
		if i.records[position].ID == id {
			i.records[position].Status = status
			if status == StatusRejected {
				i.records[position].RejectReason = strings.TrimSpace(reason)
			} else {
				i.records[position].RejectReason = ""
			}
			if err := i.flushLocked(); err != nil {
				return Photo{}, err
			}
			return i.records[position], nil
		}
	}
	return Photo{}, ErrNotFound
}

// Report 举报：**立刻退回 pending**（等于先下架），并累加举报计数等人工复核。
// 这样即便审核有疏漏，其他用户也有一个立刻生效的止损手段。
func (i *Index) Report(id string) (Photo, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	for position := range i.records {
		if i.records[position].ID == id {
			i.records[position].Status = StatusPending
			i.records[position].ReportedCount++
			if err := i.flushLocked(); err != nil {
				return Photo{}, err
			}
			return i.records[position], nil
		}
	}
	return Photo{}, ErrNotFound
}

// SetOwners 改写拥有者列表（删除时"某个上传者退出"用）。
func (i *Index) SetOwners(id string, owners []string) (Photo, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	for position := range i.records {
		if i.records[position].ID == id {
			i.records[position].UploaderIDs = owners
			if len(owners) > 0 {
				i.records[position].UploaderID = owners[0]
			}
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
	stored, err := s.index.Add(photo)
	if err != nil {
		return Photo{}, err
	}
	return stored, nil
}

// ListMine 只返回上传者自己的照片（含待审），按时间倒序。
func (s *Service) ListMine(uploaderID string) []Photo {
	if !s.Enabled() || uploaderID == "" {
		return nil
	}
	var mine []Photo
	for _, item := range s.index.All() {
		for _, owner := range item.OwnerIDs() {
			if owner == uploaderID {
				mine = append(mine, item)
				break
			}
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

// Moderate 审核（P2 管理端调用）：通过 / 拒绝（拒绝必须给理由，理由会回传给上传者）。
func (s *Service) Moderate(id, status, reason string) (Photo, error) {
	if !s.Enabled() {
		return Photo{}, ErrDisabled
	}
	if status == StatusRejected && strings.TrimSpace(reason) == "" {
		return Photo{}, errors.New("拒绝必须给出理由（要回传给上传者）")
	}
	return s.index.SetStatus(id, status, reason)
}

// Report 用户举报：先把照片退回 pending（立刻对其他用户不可见），再等人工复核。
// 上传者自己举报自己无意义，直接忽略。
func (s *Service) Report(id, reporterID string) (Photo, error) {
	if !s.Enabled() {
		return Photo{}, ErrDisabled
	}
	photo, ok := s.index.ByID(id)
	if !ok {
		return Photo{}, ErrNotFound
	}
	if reporterID != "" && reporterID == photo.UploaderID {
		return photo, nil
	}
	return s.index.Report(id)
}

// ListForReview 管理端待复核列表（默认只看 pending）。
func (s *Service) ListForReview(status string) []Photo {
	if !s.Enabled() {
		return nil
	}
	if status == "" {
		status = StatusPending
	}
	var items []Photo
	for _, item := range s.index.All() {
		if item.Status == status {
			items = append(items, item)
		}
	}
	sort.Slice(items, func(a, b int) bool {
		// 被举报过的排前面（更需要人看），再按时间
		if items[a].ReportedCount != items[b].ReportedCount {
			return items[a].ReportedCount > items[b].ReportedCount
		}
		return items[a].CreatedAt.Before(items[b].CreatedAt)
	})
	return items
}

// Delete 删除照片。语义（内容可能被多个人上传过）：
//   - 上传者删除 = **自己退出**：从拥有者列表移除自己；若还有别的上传者，记录与文件都保留
//     （否则一个人就能把别人上传的图一起删掉 —— 这是实测抓出来的 bug）；
//   - 最后一个拥有者（或管理员）删除 = 记录与文件一起清掉，不留"占着磁盘但查不到"的无主文件。
func (s *Service) Delete(id, requesterID string, isAdmin bool) error {
	if !s.Enabled() {
		return ErrDisabled
	}
	photo, ok := s.index.ByID(id)
	if !ok {
		return ErrNotFound
	}
	owners := photo.OwnerIDs()
	isOwner := false
	for _, owner := range owners {
		if owner == requesterID {
			isOwner = true
			break
		}
	}
	if !isAdmin && !isOwner {
		return ErrForbidden
	}

	// 管理员且不是上传者 → 整条下架（内容治理需要）
	if isAdmin && !isOwner {
		return s.removeEverywhere(id)
	}

	remaining := make([]string, 0, len(owners))
	for _, owner := range owners {
		if owner != requesterID {
			remaining = append(remaining, owner)
		}
	}
	if len(remaining) > 0 {
		_, err := s.index.SetOwners(id, remaining)
		return err
	}
	return s.removeEverywhere(id)
}

func (s *Service) removeEverywhere(id string) error {
	if err := s.index.Remove(id); err != nil {
		return err
	}
	if path, err := PathFor(s.dir, id); err == nil {
		_ = os.Remove(path)
	}
	return nil
}
