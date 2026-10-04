// push-smoke —— 真实发一条 FCM 推送，验证"服务账号 / 项目 / 投递链路"到底通不通。
//
// 为什么需要它：FCM 的失败模式大多是**静默**的（配置缺失、私钥无效、token 过期、
// 项目不匹配），只看日志很难分清。这个命令把每一步都单独报出来：
//   1) 服务账号 JSON 能否加载 + 私钥能否签 JWT；
//   2) 能否向 Google 换到 OAuth access token（这一步能过，说明凭据是有效的）；
//   3) 真正投递到该用户的 device token，并如实报出成功/失败条数与失败原因。
//
// 用法（在 gateway 目录下，需先有 .env）：
//   go run ./cmd/push-smoke -list
//   go run ./cmd/push-smoke -user <user_id> [-title "..."] [-body "..."]
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"gateway/internal/database"
	"gateway/internal/push"

	"github.com/joho/godotenv"
)

func main() {
	_ = godotenv.Load()

	list := flag.Bool("list", false, "只列出已注册 device token 的用户，不发送")
	userID := flag.String("user", "", "目标用户 id（该用户需已注册 device token）")
	title := flag.String("title", "OmniRoute 推送自检", "通知标题")
	body := flag.String("body", "如果你在浏览器看到这条通知，说明推送链路是通的。", "通知正文")
	flag.Parse()

	if err := database.InitDB(); err != nil {
		log.Fatalf("数据库初始化失败: %v", err)
	}
	fmt.Printf("数据库已连接（DB_PATH=%s）\n", os.Getenv("DB_PATH"))

	if *list {
		var rows []push.DeviceToken
		if err := database.DB.Find(&rows).Error; err != nil {
			log.Fatalf("读取 device token 失败: %v", err)
		}
		counts := map[string]int{}
		for _, row := range rows {
			counts[row.UserID]++
		}
		fmt.Printf("已注册 device token 的用户：%d 个（共 %d 条）\n", len(counts), len(rows))
		for user, count := range counts {
			fmt.Printf("  - %s  %d 条\n", user, count)
		}
		return
	}

	// 这一步同时校验：JSON 能加载、私钥能签 JWT、项目 id 有来源
	sender, err := push.NewSenderFromEnv()
	if err != nil {
		log.Fatalf("❌ FCM 发送端不可用（如实报告，不假装成功）: %v", err)
	}
	fmt.Printf("✅ 服务账号校验通过（project=%s, json=%s）\n", os.Getenv("FCM_PROJECT_ID"), os.Getenv("FCM_SERVICE_ACCOUNT_JSON_PATH"))

	if *userID == "" {
		log.Fatal("缺少 -user（或先用 -list 看有哪些用户已注册 token）")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	started := time.Now()
	result, err := sender.SendToUser(ctx, *userID, push.Message{Title: *title, Body: *body})
	elapsed := time.Since(started).Milliseconds()
	if err != nil {
		// 取 token 失败 / 网络失败等：如实报错（不是"已发送"）
		log.Fatalf("❌ 投递失败（耗时 %dms）: %v", elapsed, err)
	}
	fmt.Printf("投递结果（耗时 %dms）: %+v\n", elapsed, *result)
}
