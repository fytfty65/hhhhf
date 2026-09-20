package photos

import (
	"errors"
	"os"
	"testing"
	"time"
)

func newTestService(t *testing.T, enabled bool) *Service {
	t.Helper()
	service, err := NewService(t.TempDir(), enabled)
	if err != nil {
		t.Fatalf("NewService 失败: %v", err)
	}
	return service
}

func TestUploadThenListMine(t *testing.T) {
	service := newTestService(t, true)
	photo, err := service.Upload("u1", "博斯腾湖", "trip-1", solidJPEG(t, 16, 12))
	if err != nil {
		t.Fatalf("上传失败: %v", err)
	}
	if photo.Status != StatusPending {
		t.Fatalf("新上传必须是待审，得到 %s", photo.Status)
	}
	mine := service.ListMine("u1")
	if len(mine) != 1 || mine[0].ID != photo.ID {
		t.Fatalf("自己的列表应能看到待审照片: %+v", mine)
	}
	if got := service.ListMine("u2"); len(got) != 0 {
		t.Fatalf("别人的列表不该出现这张照片: %+v", got)
	}
}

func TestPendingPhotoIsNotServedToOthers(t *testing.T) {
	service := newTestService(t, true)
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))

	if _, _, err := service.Open(photo.ID, "u2"); !errors.Is(err, ErrForbidden) {
		t.Fatalf("待审照片对他人必须 ErrForbidden，得到 %v", err)
	}
	if _, _, err := service.Open(photo.ID, ""); !errors.Is(err, ErrForbidden) {
		t.Fatalf("匿名访问必须 ErrForbidden，得到 %v", err)
	}
	// 上传者自己能拿到文件
	_, path, err := service.Open(photo.ID, "u1")
	if err != nil {
		t.Fatalf("上传者应能取到自己的照片: %v", err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("文件应真实存在: %v", err)
	}
}

func TestModerationPublishesPhoto(t *testing.T) {
	service := newTestService(t, true)
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))

	if _, err := service.Moderate(photo.ID, "not-a-status", ""); err == nil {
		t.Fatal("非法状态必须被拒绝")
	}
	// 拒绝必须给理由（理由要回传给上传者，不做黑箱审核）
	if _, err := service.Moderate(photo.ID, StatusRejected, "   "); err == nil {
		t.Fatal("拒绝而不给理由必须被拒绝")
	}
	if _, err := service.Moderate(photo.ID, StatusApproved, ""); err != nil {
		t.Fatalf("审核通过失败: %v", err)
	}
	if _, _, err := service.Open(photo.ID, "u2"); err != nil {
		t.Fatalf("审核通过后他人应可查看: %v", err)
	}
	if _, err := service.Moderate("不存在的id", StatusApproved, ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("不存在的照片应 ErrNotFound，得到 %v", err)
	}
}

func TestRejectCarriesReasonToUploaderOnly(t *testing.T) {
	service := newTestService(t, true)
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))
	if _, err := service.Moderate(photo.ID, StatusRejected, "含他人隐私信息"); err != nil {
		t.Fatalf("拒绝失败: %v", err)
	}
	mine := service.ListMine("u1")
	if len(mine) != 1 || mine[0].RejectReason != "含他人隐私信息" {
		t.Fatalf("上传者应能看到拒绝理由: %+v", mine)
	}
	if _, _, err := service.Open(photo.ID, "u2"); !errors.Is(err, ErrForbidden) {
		t.Fatalf("被拒照片不该被别人看到，得到 %v", err)
	}
}

func TestReportTakesPhotoDownImmediately(t *testing.T) {
	service := newTestService(t, true)
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))
	if _, err := service.Moderate(photo.ID, StatusApproved, ""); err != nil {
		t.Fatalf("先通过: %v", err)
	}
	if _, _, err := service.Open(photo.ID, "u2"); err != nil {
		t.Fatalf("通过后他人可看: %v", err)
	}
	if _, err := service.Report(photo.ID, "u2"); err != nil {
		t.Fatalf("举报失败: %v", err)
	}
	// 举报立刻退回 pending → 其他人看不到（止损），并被记入待复核列表
	if _, _, err := service.Open(photo.ID, "u2"); !errors.Is(err, ErrForbidden) {
		t.Fatalf("被举报后应立刻对他人不可见，得到 %v", err)
	}
	review := service.ListForReview(StatusPending)
	if len(review) != 1 || review[0].ReportedCount != 1 {
		t.Fatalf("被举报的照片应排在待复核列表里: %+v", review)
	}
	// 自己举报自己无意义，不计数
	if _, err := service.Report(photo.ID, "u1"); err != nil {
		t.Fatalf("自举报不该报错: %v", err)
	}
	if service.ListForReview(StatusPending)[0].ReportedCount != 1 {
		t.Fatal("自举报不应计数")
	}
}

func TestListForReviewPutsReportedFirst(t *testing.T) {
	service := newTestService(t, true)
	first, _ := service.Upload("u1", "A", "", solidJPEG(t, 16, 12))
	second, _ := service.Upload("u2", "B", "", solidJPEG(t, 20, 14))
	if _, err := service.Report(second.ID, "u3"); err != nil {
		t.Fatalf("举报失败: %v", err)
	}
	review := service.ListForReview(StatusPending)
	if len(review) != 2 {
		t.Fatalf("应有 2 张待审: %+v", review)
	}
	if review[0].ID != second.ID {
		t.Fatalf("被举报的应排最前（更需要人看）: %+v", review)
	}
	_ = first
}

func TestQuotaBlocksUploads(t *testing.T) {
	service := newTestService(t, true)
	now := time.Date(2026, 9, 19, 12, 0, 0, 0, time.UTC)
	service.now = func() time.Time { return now }
	// 直接预置满额记录，避免真的编码 20 张图
	for i := 0; i < DailyQuota; i++ {
		fake := Photo{ID: hashOf(string(rune('a' + i))), UploaderID: "u1", Status: StatusApproved, CreatedAt: now}
		if err := service.index.Add(fake); err != nil {
			t.Fatalf("预置失败: %v", err)
		}
	}
	if _, err := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 8, 8)); !errors.Is(err, ErrDailyQuota) {
		t.Fatalf("满额后应拒绝上传，得到 %v", err)
	}
	if _, err := service.Upload("u2", "某地", "trip-1", solidJPEG(t, 8, 8)); err != nil {
		t.Fatalf("别的用户不该被牵连: %v", err)
	}
}

func TestDeleteRemovesRecordAndFile(t *testing.T) {
	service := newTestService(t, true)
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))
	_, path, _ := service.Open(photo.ID, "u1")

	if err := service.Delete(photo.ID, "u2", false); !errors.Is(err, ErrForbidden) {
		t.Fatalf("非上传者不能删，得到 %v", err)
	}
	if err := service.Delete(photo.ID, "u1", false); err != nil {
		t.Fatalf("上传者应能删除: %v", err)
	}
	if _, _, err := service.Open(photo.ID, "u1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("删除后应查不到，得到 %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("删除后文件应一并清掉（不留无主占盘文件）: %v", err)
	}
}

func TestDisabledServiceRejectsEverything(t *testing.T) {
	service := newTestService(t, false)
	if service.Enabled() {
		t.Fatal("开关关闭时应报告未启用")
	}
	if _, err := service.Upload("u1", "某地", "", solidJPEG(t, 8, 8)); !errors.Is(err, ErrDisabled) {
		t.Fatalf("关闭时应 ErrDisabled，得到 %v", err)
	}
	if len(service.ListMine("u1")) != 0 {
		t.Fatal("关闭时列表应为空")
	}
	if _, _, err := service.Open("x", "u1"); !errors.Is(err, ErrDisabled) {
		t.Fatalf("关闭时取图应 ErrDisabled，得到 %v", err)
	}
}

func TestIndexPersistsAcrossReopen(t *testing.T) {
	dir := t.TempDir()
	service, err := NewService(dir, true)
	if err != nil {
		t.Fatalf("NewService 失败: %v", err)
	}
	photo, _ := service.Upload("u1", "某地", "trip-1", solidJPEG(t, 16, 12))

	reopened, err := NewService(dir, true)
	if err != nil {
		t.Fatalf("重新打开失败: %v", err)
	}
	restored := reopened.ListMine("u1")
	if len(restored) != 1 || restored[0].ID != photo.ID {
		t.Fatalf("重开后记录应还在: %+v", restored)
	}
}

func hashOf(seed string) string {
	sum := make([]byte, 0, 64)
	for len(sum) < 64 {
		sum = append(sum, []byte(seed+"0123456789abcdef")...)
	}
	return string(sum[:64])
}
