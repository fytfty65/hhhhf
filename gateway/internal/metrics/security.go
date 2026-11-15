package metrics

import "sync/atomic"

// 安全事件计数（进程内，重启归零）。
//
// 明细仍然逐行写结构化日志（handlers/security_audit.go 的 securityEvent），
// 这里只是给只读运维看板一个「本次启动以来」的即时数字。
// 两者互补：计数用于快速判断"刚才是被扫了还是自己人输错密码"，
// 取证仍然要查日志里的 username / client_ip / request_id。
var (
	securityLoginFailures    atomic.Int64
	securityLockouts         atomic.Int64
	securityRateLimited      atomic.Int64
	securityIdentityMismatch atomic.Int64
)

// 与 security_audit.go 里的 secEvent* 常量保持一致。
const (
	SecurityEventAuthLogin     = "auth_login"
	SecurityEventAuthLockout   = "auth_lockout"
	SecurityEventRateLimit     = "rate_limit"
	SecurityEventIdentityCheck = "identity_check"
)

func RecordSecurityEvent(event string) {
	switch event {
	case SecurityEventAuthLogin:
		securityLoginFailures.Add(1)
	case SecurityEventAuthLockout:
		securityLockouts.Add(1)
	case SecurityEventRateLimit:
		securityRateLimited.Add(1)
	case SecurityEventIdentityCheck:
		securityIdentityMismatch.Add(1)
	}
}

// SecurityEventCounts 返回本次进程启动以来的安全事件计数。
func SecurityEventCounts() map[string]int64 {
	return map[string]int64{
		"login_failures":    securityLoginFailures.Load(),
		"lockouts":          securityLockouts.Load(),
		"rate_limited":      securityRateLimited.Load(),
		"identity_mismatch": securityIdentityMismatch.Load(),
	}
}
