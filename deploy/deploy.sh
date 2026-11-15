#!/usr/bin/env bash
# OmniRoute 一键部署（在服务器上执行，由 deploy.ps1 调用）
# 用法: bash deploy.sh [源码包路径]     默认 /tmp/omniroute-src.tgz
#
# 前提（一次性，已在本机完成）：/opt/omniroute 下的 .env、systemd unit、Nginx
# 站点配置、venv314 都已就位。这个脚本只负责「重装依赖 + 重建 + 重启 + 验活」。
set -euo pipefail

APP=/opt/omniroute
SRC=$APP/srv
ARCHIVE=${1:-/tmp/omniroute-src.tgz}
GO=/usr/local/go/bin/go
PY=$APP/venv314/bin/python

log()  { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { echo "部署失败：$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || fail "需要 root 权限运行"
[ -f "$ARCHIVE" ] || fail "找不到源码包 $ARCHIVE"
[ -x "$GO" ] || fail "找不到 Go：$GO"
[ -x "$PY" ] || fail "找不到 AI 服务解释器：$PY"

# 2G 内存的机器上，裸跑构建会把 sshd 一起饿死（已踩过）。所有构建都放进
# 有内存上限的 cgroup：超限只会杀掉构建本身，不会拖垮整机。
capped() {
  local mem=$1; shift
  systemd-run --scope -q --collect \
    -p MemoryMax="$mem" -p MemorySwapMax=3000M -p CPUQuota=180% \
    -E PATH=/usr/local/bin:/usr/bin:/bin \
    -- bash -lc "$*"
}

# 部署前留一份数据库快照，保留最近 5 份
if [ -f "$APP/data/omniroute.db" ]; then
  cp "$APP/data/omniroute.db" "$APP/backups/predeploy-$(date +%Y%m%dT%H%M%S).db"
  ls -1t "$APP"/backups/predeploy-*.db 2>/dev/null | tail -n +6 | xargs -r rm -f
  log "已备份数据库到 $APP/backups"
fi

log "解包源码 → $SRC"
tar -xzf "$ARCHIVE" -C "$SRC"
[ -d "$SRC/front" ] || fail "源码包结构不对：缺少 front/"
mkdir -p "$SRC/gateway/uploads/avatars"

log "编译网关"
capped 1200M "export GOMAXPROCS=1 GOGC=40 GOFLAGS=; cd $SRC/gateway && $GO build -p 1 -trimpath -ldflags '-s -w' -o $APP/bin/gateway ./cmd/server"
[ -x "$APP/bin/gateway" ] || fail "网关编译产物缺失"

# 依赖只在 lockfile 变化时重装，省掉每次几分钟的 npm ci
if [ ! -d "$SRC/front/node_modules" ] || [ "$SRC/front/package-lock.json" -nt "$SRC/front/node_modules/.package-lock.json" ]; then
  log "安装前端依赖（lockfile 有变化）"
  capped 1400M "cd $SRC/front && npm ci --no-audit --no-fund"
else
  log "前端依赖未变化，跳过 npm ci"
fi

[ -f "$SRC/front/.env.local" ] || fail "缺少 $SRC/front/.env.local（NEXT_PUBLIC_* 是构建期注入的，必须存在）"
# 构建到独立的 .next-build，成功后用 rename 整体切换：
#   1) 构建期间线上仍在读旧的 .next，站点保持可用（以前直接写 .next，构建期整站 500）；
#   2) 构建失败不会破坏线上产物；
#   3) OMNI_SKIP_TYPECHECK=1 让服务器只做编译 —— 这台 1.7G 的机器跑一次 tsc 要 20 分钟，
#      类型检查已挪到 deploy.ps1 在本机执行作为发布闸门。
log "构建前端（API 基址取自 .env.local，产物 → .next-build）"
rm -rf "$SRC/front/.next-build" "$SRC/front/.next-old"
capped 1400M "cd $SRC/front && NODE_OPTIONS=--max-old-space-size=900 NEXT_TELEMETRY_DISABLED=1 OMNI_SKIP_TYPECHECK=1 OMNI_DIST_DIR=.next-build npm run build"
[ -f "$SRC/front/.next-build/BUILD_ID" ] || fail "前端构建产物缺失（.next-build/BUILD_ID）"

log "切换前端产物 .next-build → .next"
if [ -d "$SRC/front/.next" ]; then mv "$SRC/front/.next" "$SRC/front/.next-old"; fi
mv "$SRC/front/.next-build" "$SRC/front/.next"
rm -rf "$SRC/front/.next-old"

REQ_HASH=$(sha256sum "$SRC/ai-service/requirements.txt" | cut -d' ' -f1)
if [ "$REQ_HASH" != "$(cat "$APP/data/.ai-req-hash" 2>/dev/null || true)" ]; then
  log "安装 AI 服务依赖（requirements.txt 有变化）"
  capped 1000M "cd $SRC/ai-service && $APP/venv314/bin/pip install -q -r requirements.txt"
  echo "$REQ_HASH" > "$APP/data/.ai-req-hash"
else
  log "AI 依赖未变化，跳过 pip install"
fi

log "重启服务"
systemctl restart omniroute-gateway omniroute-ai omniroute-front
systemctl reload nginx

wait_url() {
  local name=$1 url=$2 unit=$3
  for _ in $(seq 1 40); do
    if curl -fsS -m 3 -o /dev/null "$url"; then
      log "$name 就绪"
      return 0
    fi
    sleep 2
  done
  echo "---- $name 启动超时，最近日志 ----" >&2
  journalctl -u "$unit" -n 25 --no-pager >&2 || true
  return 1
}

wait_url "网关" "http://127.0.0.1:8080/readyz" omniroute-gateway
wait_url "AI 服务" "http://127.0.0.1:8000/health" omniroute-ai
wait_url "前端" "http://127.0.0.1:3000/" omniroute-front

log "完成"
for s in nginx redis-server omniroute-gateway omniroute-ai omniroute-front; do
  printf '  %-22s %s\n' "$s" "$(systemctl is-active "$s")"
done
printf '  内存: %s\n' "$(free -m | awk '/^Mem:/ {print $3"MB used / "$7"MB available"}')"
