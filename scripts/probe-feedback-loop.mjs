/**
 * 「点不喜欢 → 下一次排点真的变」端到端探针（2026-10-05）。
 *
 * 为什么必须真跑：这条链路横跨 网关(FeedbackLog) → 推演(evolution_memory) → 排除/距离口径，
 * 单测只能证明纯函数对，证明不了"点完之后方案里那个点真的没了"。
 *
 * 流程（两轮真实推演）：
 *   第 1 轮：注册 → 建房 → WS 推演 → 拿到路线（节点名 + 坐标）；
 *   反馈：挑**离全部节点重心最远**的白天节点，POST /api/v1/feedback（score=-1，原因"太远了，来回太折腾"）；
 *   第 2 轮：同一个房间、同一句诉求再推演一次 → 检查：
 *     - 被点名的地点不再出现；
 *     - 方案事实里 distance_tolerance 变成 low（远点会被日内可行性修复移出）；
 *     - 方案里带 feedback_applied（排除了谁、为什么）。
 *
 * 用法：node scripts/probe-feedback-loop.mjs
 *      $env:FEEDBACK_CITY='喀什'; $env:FEEDBACK_INTENT='去喀什玩3天，想多看人文历史和古城'; node scripts/probe-feedback-loop.mjs
 */

import path from 'node:path';

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const CITY = process.env.FEEDBACK_CITY || '喀什';
const INTENT = process.env.FEEDBACK_INTENT || `去${CITY}玩3天，想多看人文历史和古城`;
const REASON = process.env.FEEDBACK_REASON || '太远了，来回太折腾';
const TIMEOUT_MS = Number(process.env.MATRIX_TIMEOUT_MS || 300000);
const FINAL_TYPES = ['ai_negotiation_result', 'final_route', 'final_route_ready'];

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
  return { status: response.status, payload };
}

async function register(username) {
  let response = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    response = await fetch(`${BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
    });
    if (response.status !== 429) break;
    await new Promise((resolve) => setTimeout(resolve, 1500 * (attempt + 1)));
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

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const timer = setTimeout(() => reject(new Error('WS 建连超时 15s')), 15000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve(ws);
    });
    ws.addEventListener('error', () => {
      clearTimeout(timer);
      reject(new Error('WS error'));
    });
    ws.addEventListener('close', (event) => {
      clearTimeout(timer);
      reject(new Error(`WS 被关闭 code=${event.code}`));
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

function routeNodes(plan) {
  return (plan?.route || []).filter((node) => node && typeof node.name === 'string');
}

function coordsOf(node) {
  const value = node.lnglat || node.location;
  if (Array.isArray(value) && value.length >= 2) return [Number(value[0]), Number(value[1])];
  if (typeof value === 'string' && value.includes(',')) {
    const [lng, lat] = value.split(',').map(Number);
    return [lng, lat];
  }
  return null;
}

function kmBetween(a, b) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** 离"全部节点重心"最远的白天节点 —— 就是那个"来回折腾"的点。 */
function farthestNode(plan) {
  const points = routeNodes(plan)
    .map((node) => ({ node, position: coordsOf(node) }))
    .filter((item) => item.position && !item.position.some(Number.isNaN));
  if (!points.length) return null;
  const center = points.reduce((acc, item) => [acc[0] + item.position[0] / points.length, acc[1] + item.position[1] / points.length], [0, 0]);
  let best = null;
  for (const item of points) {
    const km = kmBetween(center, item.position);
    if (!best || km > best.km) best = { name: String(item.node.name), km: Number(km.toFixed(1)), day: item.node.day };
  }
  return best;
}

async function negotiate(ws, roomId, userId) {
  const started = Date.now();
  const pending = collectFinal(ws);
  ws.send(JSON.stringify({
    type: 'agent_negotiate',
    room_id: roomId,
    user_id: userId,
    payload: {
      destinations: [CITY],
      current_request: { destinations: [CITY], city: '' },
      user_preferences: { intent: INTENT, mode: 'coop', role: '寻味探索', members: 2, budget: 3000 },
    },
  }));
  const events = await pending;
  return { plan: finalPayload(events), seconds: Number(((Date.now() - started) / 1000).toFixed(1)), eventCount: events.length };
}

function factsOf(plan) {
  const facts = plan?.facts && typeof plan.facts === 'object' ? plan.facts : {};
  const value = (path) => path.reduce((node, key) => (node && typeof node === 'object' ? node[key] : undefined), facts)?.value;
  return {
    tolerance: value(['constraints', 'distance_tolerance']),
    days: value(['request', 'trip_days']),
  };
}

async function main() {
  const stamp = String(Date.now());
  const username = `feedback_${stamp}_${CITY}`;
  const account = await register(username);
  if (!account.token) throw new Error('注册失败');
  const room = await post('/api/v1/room/create', account.token, { username, room_name: `反馈闭环${stamp}` });
  const roomId = String(room.payload.room_id || '');
  if (!roomId) throw new Error('建房失败');

  const ws = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`);

  console.log('='.repeat(72));
  console.log(`第 1 轮推演：${CITY}｜${INTENT}`);
  const first = await negotiate(ws, roomId, account.userId);
  if (!first.plan) throw new Error('第 1 轮未收到终稿');
  const before = routeNodes(first.plan).map((node) => String(node.name));
  const beforeFacts = factsOf(first.plan);
  console.log(`  ${first.seconds}s｜节点 ${before.length}｜距离口径 ${beforeFacts.tolerance}`);
  console.log(`  路线：${before.join('、')}`);

  const target = farthestNode(first.plan);
  if (!target) throw new Error('第 1 轮方案里没有带坐标的节点，无法挑"最折腾的点"');
  console.log(`\n点「不喜欢」：${target.name}（第 ${target.day} 天，离全部节点重心约 ${target.km}km）｜原因：${REASON}`);

  const feedback = await post('/api/v1/feedback', account.token, {
    room_id: roomId,
    user_id: account.userId,
    target: target.name,
    score: -1,
    reason: REASON,
  });
  console.log(`  反馈接口：HTTP ${feedback.status} ${JSON.stringify(feedback.payload)}`);
  if (feedback.status !== 200) throw new Error('反馈提交失败');

  console.log(`\n第 2 轮推演（同一个房间、同一句诉求）…`);
  const second = await negotiate(ws, roomId, account.userId);
  if (!second.plan) throw new Error('第 2 轮未收到终稿');
  try { ws.close(); } catch { /* ignore */ }
  const after = routeNodes(second.plan).map((node) => String(node.name));
  const afterFacts = factsOf(second.plan);
  const applied = second.plan.feedback_applied || null;
  console.log(`  ${second.seconds}s｜节点 ${after.length}｜距离口径 ${afterFacts.tolerance}`);
  console.log(`  路线：${after.join('、')}`);

  const stillThere = after.find((name) => name.includes(target.name) || target.name.includes(name));
  const report = {
    city: CITY,
    target: target.name,
    reason: REASON,
    round1: { seconds: first.seconds, nodes: before.length, tolerance: beforeFacts.tolerance },
    round2: { seconds: second.seconds, nodes: after.length, tolerance: afterFacts.tolerance, feedback_applied: applied },
    disliked_still_present: stillThere || null,
    removed: before.filter((name) => !after.includes(name)),
    added: after.filter((name) => !before.includes(name)),
  };
  console.log('\n' + '='.repeat(72));
  console.log('结论：');
  console.log(`  被点名地点是否还在方案里：${stillThere ? `❌ 还在（${stillThere}）` : '✅ 已移出'}`);
  console.log(`  距离口径：${beforeFacts.tolerance} → ${afterFacts.tolerance}${afterFacts.tolerance === 'low' ? '（嫌远的意图读到了）' : ''}`);
  console.log(`  feedback_applied：${applied ? JSON.stringify(applied) : '（方案里没有这一项）'}`);
  console.log(`  移除：${report.removed.join('、') || '无'}`);
  console.log(`  新增：${report.added.join('、') || '无'}`);
  console.log('='.repeat(72));

  const outPath = path.join('work', `feedback-loop-${CITY}-${stamp}.json`);
  const { writeFile, mkdir } = await import('node:fs/promises');
  await mkdir('work', { recursive: true });
  await writeFile(outPath, JSON.stringify(report, null, 2), 'utf8');
  console.log(`报告已写入 ${outPath}`);
  return stillThere ? 1 : 0;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error('反馈闭环探针异常:', error?.stack || error);
    process.exit(1);
  });
