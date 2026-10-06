/**
 * 预热语义缓存：把常用的几个请求先跑一遍，让用户/演示时能**秒回**。
 *
 * 为什么要有它：语义缓存只在"同一个请求第二次来"时才命中，而用户第一次点必是冷的
 * （冷跑 70~90s，其中大头是一次大模型调用）。演示与自测前先把常见场景预热一遍，
 * 之后的同样请求就是 0.3s 级别 —— 这条比抠模型速度的收益更直接。
 *
 * 用法：
 *   node scripts/warm-plan-cache.mjs                 # 跑默认场景
 *   $env:WARM_CITIES='库尔勒,喀什'; node scripts/warm-plan-cache.mjs
 *   $env:WARM_TIMEOUT_MS='240000'; node scripts/warm-plan-cache.mjs
 *
 * 输出每个场景的耗时，并标注"冷跑/命中缓存"（< 3s 视为命中）。
 * 注意：它只发真实请求，不写任何库；网关/ai-service 必须先起来。
 */

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Warm#2026';
const STAMP = String(Date.now());
const TIMEOUT_MS = Number(process.env.WARM_TIMEOUT_MS || 240000);
const FINAL_TYPES = ['ai_negotiation_result', 'final_route', 'final_route_ready'];

const ALL_SCENARIOS = [
  { city: '库尔勒', intent: '去库尔勒玩2天，帮我安排一下就行' },
  { city: '喀什', intent: '去喀什玩3天，想多看人文历史和古城' },
  { city: '伊宁', intent: '去伊宁玩3天，想看自然风景，不介意跑远' },
  { city: '乌鲁木齐', intent: '带孩子去乌鲁木齐玩2天，预算1500，节奏慢一点' },
];

function scenarios() {
  const only = String(process.env.WARM_CITIES || '').trim();
  if (!only) return ALL_SCENARIOS;
  const wanted = only.split(',').map((item) => item.trim()).filter(Boolean);
  return ALL_SCENARIOS.filter((item) => wanted.some((city) => item.city.includes(city)));
}

async function register(username) {
  let response = await fetch(`${BASE}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
  });
  if (response.status === 429) {
    // 预热会连续注册，网关对注册有限流（实测 429）—— 等一等再来
    await new Promise((resolve) => setTimeout(resolve, 4000));
    response = await fetch(`${BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
    });
  }
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

async function post(pathname, token, body) {
  const response = await fetch(`${BASE}${pathname}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  return response.json().catch(() => ({}));
}

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('WS 建连超时 15s')), 15000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });
    ws.addEventListener('error', () => reject(new Error('WS error')));
  });
}

function waitForFinal(ws) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      ws.removeEventListener('message', onMessage);
      resolve(null);
    }, TIMEOUT_MS);
    function onMessage(event) {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === 'string' ? event.data : String(event.data));
      } catch {
        return;
      }
      if (FINAL_TYPES.includes(String(msg.type))) {
        clearTimeout(timer);
        ws.removeEventListener('message', onMessage);
        resolve(msg.payload?.final_data || msg.payload || {});
      }
    }
    ws.addEventListener('message', onMessage);
  });
}

async function warm(scenario) {
  const username = `warm_${STAMP}_${scenario.city}`;
  const account = await register(username);
  if (!account.token) return { ...scenario, seconds: 0, error: '注册失败' };
  const room = await post('/api/v1/room/create', account.token, { username, room_name: `预热${STAMP}` });
  const roomId = String(room.room_id || '');
  if (!roomId) return { ...scenario, seconds: 0, error: '建房失败' };
  const ws = await openSocket(
    `${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`,
  );
  const started = Date.now();
  const pending = waitForFinal(ws);
  ws.send(JSON.stringify({
    type: 'agent_negotiate',
    room_id: roomId,
    user_id: account.userId,
    payload: {
      destinations: [scenario.city],
      current_request: { destinations: [scenario.city], city: '' },
      user_preferences: { intent: scenario.intent, mode: 'coop', role: '寻味探索', members: 2, budget: 3000 },
    },
  }));
  const payload = await pending;
  const seconds = Number(((Date.now() - started) / 1000).toFixed(1));
  try { ws.close(); } catch { /* ignore */ }
  if (!payload) return { ...scenario, seconds, error: '未收到终稿' };
  const nodes = Array.isArray(payload.route) ? payload.route.length : 0;
  return { ...scenario, seconds, nodes };
}

async function main() {
  const list = scenarios();
  console.log('='.repeat(70));
  console.log(`预热语义缓存（${list.length} 个场景）`);
  console.log('='.repeat(70));
  let warm_hits = 0;
  for (const scenario of list) {
    process.stdout.write(`  ▶ ${scenario.city}｜${scenario.intent} … `);
    try {
      const row = await warm(scenario);
      if (row.error) {
        console.log(`❌ ${row.error}`);
        continue;
      }
      const cached = row.seconds < 3;
      if (cached) warm_hits += 1;
      console.log(`${row.seconds}s｜${row.nodes} 节点｜${cached ? '命中缓存（已预热）' : '冷跑（已写入缓存）'}`);
    } catch (error) {
      console.log(`❌ ${error?.message || error}`);
    }
  }
  console.log('');
  console.log(`命中缓存：${warm_hits}/${list.length}（其余已写入缓存，再次请求即可秒回）`);
  console.log('='.repeat(70));
  process.exit(0);
}

main().catch((error) => {
  console.error('预热异常:', error?.stack || error);
  process.exit(1);
});
