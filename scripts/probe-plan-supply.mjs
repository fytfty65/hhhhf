/**
 * 单轮真实推演 · 候选供给探针（零依赖）。
 *
 * 回答的问题（库尔勒实测暴露的）：**这次方案里的地点，有多少真的来自数据源？**
 *   - 数据源限流/关键词太窄时，真实候选会很少，模型改用自己的记忆补地点；
 *   - 用户看到的方案"看起来挺全"，但没有任何地方告诉他这件事。
 *
 * 判据：
 *   1. 方案里必须带 `candidate_supply`（真实候选数 / 需要数 / 是否补检索 / 说明）；
 *   2. 供给不足时必须出现在 `degradations`（界面「遗留问题」直接读它）；
 *   3. 打印路线节点，供人工核对"哪些像模型知识、哪些像数据源"。
 *
 * 用法：
 *   node scripts/probe-plan-supply.mjs
 *   $env:PROBE_CITY='库尔勒'; $env:PROBE_INTENT='去库尔勒玩2天，想看自然风景'; node scripts/probe-plan-supply.mjs
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
const CITY = process.env.PROBE_CITY || '库尔勒';
const INTENT = process.env.PROBE_INTENT || `去${CITY}玩2天，想看自然风景，节奏不要太赶`;
const FINAL_TYPES = ['ai_negotiation_result', 'final_route', 'final_route_ready'];
const TIMEOUT_MS = Number(process.env.PROBE_TIMEOUT_MS || 240000);
const results = [];

function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
}

async function post(pathname, token, body) {
  const response = await fetch(`${BASE}${pathname}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('WS 建连超时 15s')), 15000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });
    ws.addEventListener('close', (event) => {
      clearTimeout(timer);
      reject(new Error(`WS 被关闭 code=${event.code} reason=${event.reason || '(空)'}`));
    });
    ws.addEventListener('error', () => {
      clearTimeout(timer);
      reject(new Error('WS error 事件（握手失败）'));
    });
  });
}

function collectFinal(ws) {
  return new Promise((resolve) => {
    const seen = [];
    const timer = setTimeout(() => {
      ws.removeEventListener('message', onMessage);
      resolve(seen);
    }, TIMEOUT_MS);
    function onMessage(event) {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === 'string' ? event.data : String(event.data));
      } catch {
        return;
      }
      seen.push(msg);
      if (FINAL_TYPES.some((type) => String(msg.type) === type)) {
        clearTimeout(timer);
        ws.removeEventListener('message', onMessage);
        resolve(seen);
      }
    }
    ws.addEventListener('message', onMessage);
  });
}

function finalPayload(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const message = events[index];
    if (!FINAL_TYPES.includes(String(message.type))) continue;
    if (message.payload?.final_data) return message.payload.final_data;
    if (message.payload?.route) return message.payload;
    if (message.payload?.status) return message.payload;
  }
  return null;
}

function nodeNames(payload) {
  const names = [];
  for (const node of payload?.route || payload?.routes || []) {
    if (node && typeof node.name === 'string') names.push(node.name);
  }
  return names;
}

async function main() {
  console.log('='.repeat(72));
  console.log('单轮真实推演 · 候选供给探针');
  console.log('='.repeat(72));
  console.log(`  城市=${CITY} 诉求=「${INTENT}」`);

  const account = await register(`probe_supply_${STAMP}`);
  record('注册并拿到会话', Boolean(account.token), `HTTP ${account.status}`);
  if (!account.token) return;

  const room = await post('/api/v1/room/create', account.token, {
    username: `probe_supply_${STAMP}`,
    room_name: `供给探针房间${STAMP}`,
  });
  const roomId = String(room.payload.room_id || '');
  record('建房', room.status === 200 && Boolean(roomId), `HTTP ${room.status}`);
  if (!roomId) return;

  let ws;
  try {
    ws = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`);
  } catch (error) {
    record('WebSocket 建连', false, error.message);
    return;
  }
  record('WebSocket 建连', true, '房间推演通道已建立');

  const startedAt = Date.now();
  const pending = collectFinal(ws);
  ws.send(
    JSON.stringify({
      type: 'agent_negotiate',
      room_id: roomId,
      user_id: account.userId,
      payload: {
        destinations: [CITY],
        current_request: { destinations: [CITY], city: '' },
        user_preferences: { intent: INTENT, mode: 'coop', role: '寻味探索', members: 2, budget: 4000 },
      },
    }),
  );
  const events = await pending;
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  const payload = finalPayload(events);
  const types = [...new Set(events.map((message) => String(message.type || '?')))];
  console.log(`  ${seconds}s / ${events.length} 条事件 / 类型：${types.join(', ')}`);
  record('收到推演结果', Boolean(payload), payload ? `${nodeNames(payload).length} 个节点` : '未收到终稿');

  const supply = payload?.candidate_supply ?? null;
  record(
    '方案带上了候选供给事实（candidate_supply）',
    Boolean(supply),
    supply
      ? `status=${supply.status} 真实白天候选=${supply.known_daytime}/${supply.needed_daytime} 景点=${supply.attractions} 美食=${supply.foods} 自然=${supply.scenic} 补检索=${JSON.stringify(supply.widened || [])}`
      : '缺字段：用户无法知道方案里的地点有多少来自数据源',
  );
  if (supply?.notes?.length) {
    console.log('  供给说明：');
    supply.notes.forEach((note) => console.log(`    · ${note}`));
  }
  if (supply?.diagnostics) {
    console.log(`  诊断：${JSON.stringify(supply.diagnostics)}`);
  }

  const pool = payload?.candidate_pool ?? null;
  if (pool) console.log(`  candidate_pool=${JSON.stringify(pool)}`);

  const degradations = payload?.degradations?.items || [];
  const supplyDegradation = degradations.filter((item) => String(item.source) === 'candidate_pool');
  if (supply && supply.status !== 'ok') {
    record(
      '供给不足时出现在「遗留问题」里（界面必须能看到）',
      supplyDegradation.length > 0,
      supplyDegradation.map((item) => `${item.status}:${item.reason}`).join(' | ') || '没有对应的降级条目',
    );
  } else {
    record('供给充足时不产生多余的降级条目', supplyDegradation.length === 0, `降级条目 ${degradations.length} 条`);
  }

  // 协商纪要（多智能体协同要看得见）：谁参与、站在什么立场、争了什么、裁决凭什么
  const negotiation = payload?.negotiation ?? null;
  record(
    '方案带上了协商纪要（参与/分歧/裁决/未参与）',
    Boolean(negotiation && Array.isArray(negotiation.agents)),
    negotiation
      ? `参与 ${negotiation.agents?.length ?? 0} 个 · 分歧 ${negotiation.conflicts?.length ?? 0} 条 · ` +
        `裁决 ${negotiation.arbitration?.length ?? 0} 条 · 未参与 ${negotiation.inactive?.length ?? 0} 个` +
        (negotiation.conflicts?.length ? `｜${negotiation.conflicts.map((item) => item.code).join(',')}` : '')
      : '缺 negotiation 字段（用户在界面上看不到协商过程）',
  );
  if (negotiation?.agents?.length) {
    console.log(
      `  协商：${negotiation.agents.slice(0, 6).map((item) => `${item.agent_id}→${item.prefers}`).join(' | ')}`,
    );
  }

  const names = nodeNames(payload);
  console.log(`  路线节点（${names.length}）：${names.join('、')}`);

  // 配比口径（用户 2026-10-04）：默认自然=人文各半并穿插美食/住宿；自然景观只算景区。
  const composition = payload?.composition ?? null;
  if (composition) {
    const actual = composition.actual || {};
    const audit = composition.audit || {};
    record(
      '方案带上了配比对账（目标 vs 实际）',
      Boolean(audit && typeof audit.ok === 'boolean'),
      `目标 自然景区 ${composition.scenic} / 人文 ${composition.cultural} / 美食 ${composition.food} / 住宿 ${composition.hotel}` +
        `（${composition.source === 'default_balanced' ? '默认各半' : '按明确偏好加权'}）` +
        ` → 实际 自然景区 ${actual.landmark ?? '?'} + 城市公园 ${actual.city_park ?? '?'} / 人文 ${actual.cultural ?? '?'} / 美食 ${actual.food ?? '?'} / 住宿 ${actual.hotel ?? '?'}` +
        `｜${audit.ok ? '一致' : (audit.notes || []).join('；')}`,
    );
    record(
      '城市公园没有被算成自然景观',
      Number(actual.landmark ?? 0) === 0 || Number(actual.scenic ?? 0) === Number(actual.landmark ?? 0),
      `landmark=${actual.landmark ?? '?'} scenic计数=${actual.scenic ?? '?'} city_park=${actual.city_park ?? '?'}`,
    );
  } else {
    record('方案带上了配比对账（目标 vs 实际）', false, '缺 composition 字段');
  }

  try {
    ws.close();
  } catch {
    /* 关闭失败不影响结论 */
  }
  console.log('');
  console.log(`USER_ID=${account.userId} ROOM_ID=${roomId}`);
  const failed = results.filter((item) => !item.ok).length;
  console.log('='.repeat(72));
  console.log(failed === 0 ? `PASS - 候选供给 ${results.length} 项全部通过` : `FAIL - ${failed}/${results.length} 项失败`);
  results.filter((item) => !item.ok).forEach((item) => console.log(`  未通过：${item.name} — ${item.detail}`));
  console.log('='.repeat(72));
  // 显式退出：WebSocket 会让事件循环一直活着
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error('探针异常:', error?.stack || error);
  process.exit(1);
});
