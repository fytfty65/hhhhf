/**
 * 闭环中段冒烟：**用户确认 → 局部重规划 → 新版本**（真实 HTTP + 真实库，零依赖）。
 *
 * 为什么单独做这一条：这是用户最初的验收口径 ——
 *   "用户确认前不自动覆盖原方案"、"未受影响节点保留率 ≥70%"、"只改受影响的节点"。
 * 此前这一段只有 Go 单测覆盖，没有走过真实网关 + 真实库 + 真实事件流。
 *
 * 判据：
 *   1. 造一份行程（含 6 个节点、带 correlation_id）；
 *   2. 由用户动作造出"待确认提案"（replan_triggered / pending_confirmation）；
 *   3. **确认之前**：行程内容必须与基线**逐字节一致**（不自动覆盖）；
 *   4. 确认之后：只有被点名的节点变了，其余节点与原内容**逐字段一致**；
 *   5. 保留率 ≥ 70%；重复确认被拒（409，不能改两次）。
 *
 * 用法：node scripts/smoke-replan-confirm.mjs
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const PASSWORD = 'Probe#2026';
const STAMP = Date.now();
const CORRELATION = `replan-smoke-${STAMP}`;
const results = [];

function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
}

async function call(method, path, token, body) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
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

async function register(username) {
  let response = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-6) }),
  });
  if (response.status !== 200) {
    response = await fetch(`${BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD }),
    });
  }
  const payload = await response.json().catch(() => ({}));
  return { token: String(payload.token || ''), userId: String(payload.user?.id || '') };
}

/** 从行程接口里取出这份行程的 route 数组（接口形状有几种可能，逐一兜住）。 */
function routeOf(trip) {
  if (!trip) return null;
  const content = trip.content ?? trip.Content;
  if (typeof content === 'string') {
    try {
      const parsed = JSON.parse(content);
      return Array.isArray(parsed?.route) ? parsed.route : Array.isArray(parsed?.routes) ? parsed.routes : null;
    } catch {
      return null;
    }
  }
  if (content && typeof content === 'object') {
    return Array.isArray(content.route) ? content.route : Array.isArray(content.routes) ? content.routes : null;
  }
  return null;
}

async function loadTrip(token, userId, tripId) {
  const list = await call('GET', `/api/user/trips?user_id=${encodeURIComponent(userId)}`, token);
  const trips = list.payload?.trips || list.payload?.data?.trips || [];
  const found = (Array.isArray(trips) ? trips : []).find((item) => String(item.id || item.ID || '') === tripId);
  return found || null;
}

async function main() {
  const username = `replan_probe_${STAMP}`;
  const account = await register(username);
  record('注册并拿到会话', Boolean(account.token && account.userId));
  if (!account.token || !account.userId) return;

  // 1) 造一份 6 节点的行程（点名要替换的是第 2 天第 2 个节点）
  const affectedNode = 'D2-景点B';
  const route = [
    { day: 1, name: 'D1-景点A', type: '景点', cost_estimate: '¥0', coordinates: '120.10,30.10' },
    { day: 1, name: 'D1-餐厅', type: '餐饮', cost_estimate: '¥60', coordinates: '120.11,30.11' },
    { day: 2, name: 'D2-景点A', type: '景点', cost_estimate: '¥40', coordinates: '120.12,30.12' },
    { day: 2, name: affectedNode, type: '景点', cost_estimate: '¥40', coordinates: '120.13,30.13' },
    { day: 3, name: 'D3-景点A', type: '景点', cost_estimate: '¥0', coordinates: '120.14,30.14' },
    { day: 3, name: 'D3-餐厅', type: '餐饮', cost_estimate: '¥80', coordinates: '120.15,30.15' },
  ];
  const saved = await call('POST', '/api/user/trip', account.token, {
    user_id: account.userId,
    title: `闭环冒烟行程${STAMP}`,
    dest_city: '杭州',
    content: JSON.stringify({ correlation_id: CORRELATION, routes: route }),
    days: 3,
    travelers: 1,
  });
  const tripId = String(saved.payload?.trip_id || '');
  record('保存基线行程（6 个节点）', saved.status === 200 && Boolean(tripId), `HTTP ${saved.status}`);
  if (!tripId) return;

  const before = await loadTrip(account.token, account.userId, tripId);
  const beforeRoute = routeOf(before);
  record('能读回基线路线', Array.isArray(beforeRoute) && beforeRoute.length === route.length, `节点 ${beforeRoute?.length ?? 0} 个`);
  const baseline = JSON.stringify(beforeRoute);

  // 2) 造"待确认提案"（真实接口，不碰数据库）
  const proposalId = `risk-${STAMP}`;
  const created = await call('POST', '/api/v1/planning/events', account.token, {
    user_id: account.userId,
    trip_id: tripId,
    event_type: 'replan_triggered',
    payload: {
      proposal_id: proposalId,
      status: 'pending_confirmation',
      reason: 'weather_change',
      affected_nodes: [affectedNode],
      expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    },
  });
  record('造出待确认的重规划提案', created.status === 200, `HTTP ${created.status} proposal=${proposalId}`);

  // 3) 关键验收：**确认之前**原方案一个字都不能变
  const untouched = await loadTrip(account.token, account.userId, tripId);
  record(
    '确认前原方案未被覆盖（逐字节一致）',
    JSON.stringify(routeOf(untouched)) === baseline,
    '这条就是"用户确认前不自动覆盖原方案"',
  );

  // 4) 用户确认 → 只改点名节点
  const confirmed = await call('POST', '/api/v1/planning/replan/confirm', account.token, {
    trip_id: tripId,
    proposal_id: proposalId,
    affected_nodes: [affectedNode],
    confirmation: 'user_confirmed',
    candidate: { name: '替代点-新景点', source: { provider: 'amap' } },
  });
  const applied = confirmed.payload?.data || confirmed.payload;
  record('确认后返回 applied', confirmed.status === 200 && applied?.status === 'applied', `HTTP ${confirmed.status} ${JSON.stringify(applied).slice(0, 160)}`);

  const preserved = Number(applied?.preserved_nodes ?? 0);
  const changed = Array.isArray(applied?.changed_nodes) ? applied.changed_nodes.length : 0;
  const ratio = preserved + changed > 0 ? preserved / (preserved + changed) : 0;
  record('未受影响节点保留率 ≥ 70%', ratio >= 0.7, `保留 ${preserved} / 变更 ${changed} = ${(ratio * 100).toFixed(0)}%`);

  const after = await loadTrip(account.token, account.userId, tripId);
  const afterRoute = routeOf(after);
  const changedNames = [];
  const drifted = [];
  if (Array.isArray(afterRoute) && afterRoute.length === route.length) {
    for (let index = 0; index < route.length; index += 1) {
      if (route[index].name === affectedNode) {
        if (afterRoute[index].name !== affectedNode) changedNames.push(`${route[index].name} → ${afterRoute[index].name}`);
        continue;
      }
      // 其余节点必须逐字段一致（除了时间线可能被重算——这里只比对这些稳定字段）
      for (const field of ['name', 'day', 'type']) {
        if (JSON.stringify(route[index][field]) !== JSON.stringify(afterRoute[index][field])) {
          drifted.push(`#${index}.${field}: ${JSON.stringify(route[index][field])} → ${JSON.stringify(afterRoute[index][field])}`);
        }
      }
    }
  }
  record('只有点名的节点被替换', changedNames.length === 1, changedNames.join('; ') || '没有任何节点变化');
  record('其余节点未被扰动', drifted.length === 0, drifted.slice(0, 3).join('; '));

  // 5) 不能确认两次
  const again = await call('POST', '/api/v1/planning/replan/confirm', account.token, {
    trip_id: tripId,
    proposal_id: proposalId,
    affected_nodes: [affectedNode],
    confirmation: 'user_confirmed',
    candidate: { name: '再来一次', source: { provider: 'amap' } },
  });
  record('重复确认被拒（不能改两次）', again.status === 409, `HTTP ${again.status}`);

  console.log('');
  console.log(`CORRELATION=${CORRELATION}`);
  console.log(`TRIP_ID=${tripId}`);
  console.log(`USER_ID=${account.userId}`);
  const failed = results.filter((item) => !item.ok).length;
  console.log(failed === 0 ? `PASS - 闭环中段 ${results.length}/${results.length} 项通过` : `FAIL - ${failed}/${results.length} 项失败`);
  process.exitCode = failed === 0 ? 0 : 1;
}

main().catch((error) => {
  console.error('冒烟异常:', error?.message || error);
  process.exitCode = 1;
});
