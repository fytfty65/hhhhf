/**
 * 「换住这家」端到端探针（2026-10-05）：
 *   第 1 轮出一个方案 → 从 lodging_supply 里挑一家**有实价**的酒店 →
 *   第 2 轮把"住宿换成 <那一家>"作为增量发回去 → 检查：
 *     · 方案里的住宿是不是真的换成了那一家（而不是"随便换个同类"）；
 *     · 时段/天数有没有被改动（就地换的定义）；
 *     · 方案里有没有 node_swap 台账（换成了谁、原来是谁）。
 *
 * 为什么必须真跑：这条链路横跨 意图解析（parse_increment）→ 候选池 → 就地替换 →
 * 收口（不能被"一天跑不了两个远景区"之类的自动优化再改掉）。
 *
 * 用法：node scripts/probe-named-swap.mjs
 *      $env:SWAP_CITY='库尔勒'; node scripts/probe-named-swap.mjs
 */

import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const CITY = process.env.SWAP_CITY || '库尔勒';
const INTENT = process.env.SWAP_INTENT || `去${CITY}玩2天，帮我安排一下就行`;
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
  }
  return null;
}

function lodgingNodes(plan) {
  return (plan?.route || []).filter(
    (node) => node && (node.is_hotel || String(node.type || '').includes('住宿') || String(node.name || '').includes('酒店')),
  );
}

async function negotiate(ws, roomId, userId, intent, extra = {}) {
  const started = Date.now();
  const pending = collectFinal(ws);
  ws.send(
    JSON.stringify({
      type: 'agent_negotiate',
      room_id: roomId,
      user_id: userId,
      payload: {
        destinations: [CITY],
        current_request: { destinations: [CITY], city: '' },
        user_preferences: {
          intent,
          mode: 'coop',
          role: '寻味探索',
          members: 2,
          budget: 3000,
          // ⚠️ 第二轮必须带上"既有路线 + 历史"，否则 ai-service 不认这是**增量**
          //    （`is_refinement = 有历史 and 有既有路线`）—— 探针漏了这两个字段时，
          //    第二轮会被当成全新规划：换点逻辑压根不参与（真跑踩过）。
          history_sequence: extra.historySequence || [],
          current_existing_route: extra.existingRoute || [],
        },
      },
    }),
  );
  const events = await pending;
  return { plan: finalPayload(events), seconds: Number(((Date.now() - started) / 1000).toFixed(1)) };
}

async function main() {
  const stamp = String(Date.now());
  const username = `swap_${stamp}_${CITY}`;
  const account = await register(username);
  if (!account.token) throw new Error('注册失败');
  const room = await post('/api/v1/room/create', account.token, { username, room_name: `换住探针${stamp}` });
  const roomId = String(room.payload.room_id || '');
  if (!roomId) throw new Error('建房失败');
  const ws = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`);

  console.log('='.repeat(72));
  console.log(`第 1 轮：${CITY}｜${INTENT}`);
  const first = await negotiate(ws, roomId, account.userId, INTENT);
  if (!first.plan) throw new Error('第 1 轮未收到终稿');
  const before = lodgingNodes(first.plan).map((node) => `${node.name}(第${node.day}天 ${node.time || '—'})`);
  const supply = (first.plan.lodging_supply || {}).hotels || [];
  console.log(`  ${first.seconds}s｜住宿：${before.join('、') || '（无）'}`);
  console.log(`  有实价的候选 ${supply.length} 家：${supply.slice(0, 4).map((h) => `${h.name} ¥${h.price ?? '—'}`).join('、') || '（无）'}`);

  const target = supply.find((hotel) => hotel?.name && !before.some((item) => String(item).includes(hotel.name)));
  if (!target) {
    console.log('\n⚠️ 候选中没有"与当前住宿不同"的一家，无法验证换住（如实记录，不算失败）');
    try { ws.close(); } catch { /* ignore */ }
    return 0;
  }
  console.log(`\n第 2 轮：增量「住宿换成 ${target.name}」`);

  const second = await negotiate(ws, roomId, account.userId, `${INTENT}（住宿换成 ${target.name}）`, {
    existingRoute: first.plan.route || [],
    historySequence: [`${username}: ${INTENT}`, `${username}: 住宿换成 ${target.name}`],
  });
  if (!second.plan) throw new Error('第 2 轮未收到终稿');
  try { ws.close(); } catch { /* ignore */ }
  const afterNodes = lodgingNodes(second.plan);
  const after = afterNodes.map((node) => `${node.name}(第${node.day}天 ${node.time || '—'})`);
  const swapAudit = second.plan.node_swap || null;
  const swapped = afterNodes.some((node) => String(node.name || '') === target.name);
  const targetNode = afterNodes.find((node) => String(node.name || '') === target.name);

  console.log(`  ${second.seconds}s｜住宿：${after.join('、') || '（无）'}`);
  console.log(`  node_swap：${swapAudit ? JSON.stringify(swapAudit) : '（方案里没有这一项）'}`);
  console.log('\n' + '='.repeat(72));
  console.log(`结论：换成了指定的那一家：${swapped ? '✅ 是' : '❌ 否'}`);
  if (targetNode) {
    const beforeNode = lodgingNodes(first.plan)[0];
    console.log(`  时段是否保留：${targetNode.time === beforeNode?.time ? '✅ 保留（' + targetNode.time + '）' : `⚠️ 变了（${beforeNode?.time} → ${targetNode.time}）`}`);
  }
  console.log(`  换点台账：${swapAudit?.actions?.length ? '有（' + JSON.stringify(swapAudit.actions[0]) + '）' : '无'}`);
  console.log('='.repeat(72));

  const report = {
    city: CITY,
    intent: INTENT,
    target: target.name,
    round1: { seconds: first.seconds, lodging: before, supply: supply.slice(0, 6) },
    round2: { seconds: second.seconds, lodging: after, node_swap: swapAudit },
    swapped,
  };
  const outPath = path.join('work', `named-swap-${CITY}-${stamp}.json`);
  await mkdir('work', { recursive: true });
  await writeFile(outPath, JSON.stringify(report, null, 2), 'utf8');
  console.log(`报告已写入 ${outPath}`);
  return swapped ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error('换住探针异常:', error?.stack || error);
    process.exit(1);
  });
