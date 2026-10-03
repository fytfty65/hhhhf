/**
 * 跨服务事件审计冒烟（真实 HTTP + 真实 Redis，零依赖）：验证"用户动作真的进了事件流"。
 *
 * 判据：
 *  1. 保存一份**带 correlation_id 的行程**（模拟前端把 ai-service 盖的关联键一起存进去）；
 *  2. 标记节点"已到访" → 网关应发 `trip.visited`，且关联键来源为 plan_content；
 *  3. 保存并采纳一套方案 → 网关应发 `user.accepted`；
 *  4. 打印关联键，供 `ai-service/tools/trace_plan.py` 回读核对（脚本本身不解析 Redis）。
 *
 * 用法：
 *   $env:SMOKE_CORRELATION='smoke-xxx'; node scripts/smoke-plan-audit.mjs
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
const CORRELATION = process.env.SMOKE_CORRELATION || `smoke-${STAMP}`;
const results = [];

function record(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
}

async function register(username) {
  let response = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
  });
  if (response.status !== 200) {
    response = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD }),
    });
  }
  const payload = await response.json().catch(() => ({}));
  return { token: String(payload.token || ''), userId: String(payload.user?.id || ''), status: response.status };
}

async function post(path, token, body) {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = JSON.parse(text);
  } catch {
    payload = {};
  }
  return { status: response.status, payload, text };
}

async function main() {
  const username = `smoke_audit_${STAMP}`;
  const account = await register(username);
  record('注册并拿到会话', Boolean(account.token && account.userId), `HTTP ${account.status}`);
  if (!account.token || !account.userId) return;

  const room = await post('/api/v1/room/create', account.token, {
    username,
    room_name: `审计冒烟房间${STAMP}`,
  });
  const roomId = String(room.payload.room_id || '');
  record('建房', room.status === 200 && Boolean(roomId), `HTTP ${room.status}`);
  if (!roomId) return;

  // 1) 带关联键的行程（关联来源应为 plan_content）
  const content = JSON.stringify({
    correlation_id: CORRELATION,
    routes: [
      { day: 1, name: '探针节点甲', cost_estimate: '¥0' },
      { day: 1, name: '探针节点乙', cost_estimate: '¥0' },
    ],
  });
  // 注意：行程接口挂在 /api/user 组下（不是 /api/v1/user），写错路径会 404
  const trip = await post('/api/user/trip', account.token, {
    user_id: account.userId,
    title: `审计冒烟行程${STAMP}`,
    dest_city: '杭州',
    content,
    days: 1,
    travelers: 1,
  });
  const tripId = String(trip.payload.trip_id || '');
  record('保存带关联键的行程', trip.status === 200 && Boolean(tripId), `HTTP ${trip.status} trip=${tripId.slice(0, 8)}…`);
  if (!tripId) return;

  // 2) 标记到访 → trip.visited
  const visited = await post('/api/v1/planning/execution', account.token, {
    trip_id: tripId,
    node_key: '1:探针节点甲',
    status: 'visited',
  });
  record('标记节点已到访', visited.status === 200, `HTTP ${visited.status} ${visited.text.slice(0, 120)}`);

  // 3) 存方案 + 采纳 → user.accepted（带上真实 trip_id，关联键才取得到）
  const variant = await post('/api/v1/plan/variant', account.token, {
    room_id: roomId,
    name: `审计冒烟方案${STAMP}`,
    style: 'relaxed',
    route: JSON.stringify([{ day: 1, name: '探针节点甲', cost_estimate: '¥0' }]),
  });
  const variantId = String(variant.payload?.variant?.id || '');
  record('保存方案', variant.status === 200 && Boolean(variantId), `HTTP ${variant.status}`);
  if (variantId) {
    const adopt = await post('/api/v1/plan/variant/adopt', account.token, {
      variant_id: variantId,
      user_id: account.userId,
      trip_id: tripId,
    });
    record('采纳方案', adopt.status === 200, `HTTP ${adopt.status} ${adopt.text.slice(0, 120)}`);
  }

  console.log('');
  console.log(`CORRELATION=${CORRELATION}`);
  console.log(`TRIP_ID=${tripId}`);
  console.log(`VARIANT_ID=${variantId}`);
  console.log(`ROOM_ID=${roomId}`);

  const failed = results.filter((item) => !item.ok).length;
  console.log(failed === 0 ? `PASS - 动作链路全部成功（${results.length} 项）` : `FAIL - ${failed}/${results.length} 项失败`);
  process.exitCode = failed === 0 ? 0 : 1;
}

main().catch((error) => {
  console.error('冒烟异常:', error?.message || error);
  process.exitCode = 1;
});
