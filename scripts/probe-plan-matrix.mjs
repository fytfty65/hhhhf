/**
 * 规划质量横向对照探针：用**真实推演**跑多个城市/意图，把各机制的实际效果铺成一张表。
 *
 * 为什么要有它：前面把机制一块块建起来了（统一事实/协商/硬约束修复/公园限量/取价），
 * 但"这些机制在不同城市、不同意图下到底管不管用"只能用**真样本**回答，
 * 靠单个城市的反复调参会把项目带偏（库尔勒的池子薄、喀什人文多、伊宁景点散）。
 *
 * 每个场景产出：目标 vs 实际配比（含真景区/城市公园）、候选供给与补检索、周边县市景区、
 * 各类硬约束修复动作、协商（参与/分歧/轮次/收敛）、取价（查了几个/补到几条/来源可达）、
 * 路线节点、耗时。结果同时写入 work/plan-quality-matrix.md。
 *
 * 用法：node scripts/probe-plan-matrix.mjs
 *      $env:MATRIX_ONLY='喀什'; node scripts/probe-plan-matrix.mjs   # 只跑匹配的场景
 */

import { writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const WS_BASE = process.env.GATEWAY_WS || BASE.replace(/^http/, 'ws');
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
const TIMEOUT_MS = Number(process.env.MATRIX_TIMEOUT_MS || 240000);
const FINAL_TYPES = ['ai_negotiation_result', 'final_route', 'final_route_ready'];

const SCENARIOS = [
  { city: '库尔勒', intent: '去库尔勒玩2天，帮我安排一下就行' },
  { city: '喀什', intent: '去喀什玩3天，想多看人文历史和古城' },
  { city: '伊宁', intent: '去伊宁玩3天，想看自然风景，不介意跑远' },
  { city: '乌鲁木齐', intent: '带孩子去乌鲁木齐玩2天，预算1500，节奏慢一点' },
  // 同一座城市、**不同诉求**：语义缓存不会命中（诉求变了），但候选池缓存应该命中 ——
  // 这一条专门用来验证"换成另一个诉求时，取数还要不要再花一次高德配额与时间"。
  { city: '库尔勒', intent: '去库尔勒玩2天，想多吃地道美食和夜市' },
];

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
  // 注册会被网关限流（实测：连续跑矩阵时 /api/auth/register 回 429，
  // 于是后面几个场景直接"注册失败"、整轮探针白跑）。这里对 429 退避重试。
  let response = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    response = await fetch(`${BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: PASSWORD, nickname: username.slice(-4) }),
    });
    if (response.status !== 429) break;
    const waitMs = 1500 * (attempt + 1);
    console.log(`  （注册被限流 429，等 ${waitMs}ms 重试）`);
    await new Promise((resolve) => setTimeout(resolve, waitMs));
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
    ws.addEventListener('close', (event) => {
      clearTimeout(timer);
      reject(new Error(`WS 被关闭 code=${event.code}`));
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

/** 带天数的路线（查"住宿到底落在哪一晚"这类问题用；纯名字看不出日期分布）。 */
function nodeDays(payload) {
  return (payload?.route || payload?.routes || [])
    .filter((node) => node && typeof node.name === 'string')
    .map((node) => `D${node.day ?? '?'}:${node.name}`);
}

function fmt(value, fallback = '—') {
  return value === undefined || value === null || value === '' ? fallback : value;
}

async function runScenario(scenario) {
  const username = `matrix_${STAMP}_${scenario.city}`;
  const account = await register(username);
  if (!account.token) throw new Error('注册失败');
  const room = await post('/api/v1/room/create', account.token, { username, room_name: `矩阵房间${STAMP}` });
  const roomId = String(room.payload.room_id || '');
  if (!roomId) throw new Error('建房失败');
  const ws = await openSocket(`${WS_BASE}/api/v1/room/${roomId}/ws?access_token=${account.token}&user_id=${account.userId}`);
  const started = Date.now();
  const pending = collectFinal(ws);
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
  const events = await pending;
  const seconds = Number(((Date.now() - started) / 1000).toFixed(1));
  try { ws.close(); } catch { /* ignore */ }
  const payload = finalPayload(events);
  if (!payload) return { ...scenario, seconds, error: '未收到终稿', eventCount: events.length };

  const supply = payload.candidate_supply || {};
  const composition = payload.composition || {};
  const actual = composition.actual || {};
  const audit = composition.audit || {};
  const negotiation = payload.negotiation || {};
  const revision = negotiation.revision || {};
  const price = payload.price_audit || {};
  const names = nodeNames(payload);
  return {
    ...scenario,
    // 原始方案（落盘给人工抽查表用）：机器判不了"地点好不好"，必须有人读
    finalData: payload,
    seconds,
    eventCount: events.length,
    nodes: names.length,
    names,
    routeByDay: nodeDays(payload),
    supply: {
      status: supply.status,
      attractions: supply.attractions,
      foods: supply.foods,
      scenic: supply.scenic,
      widened: supply.widened || [],
      outOfTown: supply.out_of_town_scenic,
      farthest: supply.farthest_scenic_km,
    },
    composition: {
      targets: `${composition.scenic}/${composition.cultural}/${composition.food}/${composition.hotel}`,
      feasible: composition.feasible_targets
        ? `${composition.feasible_targets.scenic}/${composition.feasible_targets.cultural}/${composition.feasible_targets.food}/${composition.feasible_targets.hotel}`
        : '—',
      actual: `${actual.landmark ?? '?'} 景区 + ${actual.city_park ?? '?'} 公园 / ${actual.cultural ?? '?'} 人文 / ${actual.food ?? '?'} 美食 / ${actual.hotel ?? '?'} 住宿`,
      source: composition.source,
      ok: audit.ok,
      notes: audit.notes || [],
      supplyLimited: Object.entries(composition.supply_limited || {}).map(([key, item]) => {
        const label = { scenic: '自然景区', cultural: '人文', food: '特色美食', hotel: '住宿' };
        return `${label[key] || key}候选 ${item.available} 个 < 目标 ${item.target} 个`;
      }),
      recomputed: Boolean(composition.recomputed_after_closeout),
    },
    repairs: {
      park: (payload.park_trim?.actions || []).map((item) => `${item.from}→${item.to || '移出'}`),
      travel: (payload.travel_repair?.moved_out || []).map((item) => item.name),
      exclusion: (payload.exclusion_repair?.actions || []).map((item) => `${item.from}→${item.to || '移出'}`),
      lodging: (payload.lodging_repair?.actions || []).map((item) => `第${item.day}晚→${item.to}`),
      lodgingMissing: payload.lodging_repair?.missing_days || [],
      lodgingError: payload.lodging_repair?.error || '',
      lodgingTrimmed: (payload.lodging_trim?.actions || []).map((item) => `${item.from}(第${item.day}天)`),
      closeout: payload.hard_constraint_closeout?.actions || [],
      closeoutError: payload.hard_constraint_closeout?.error || '',
    },
    negotiation: {
      agents: (negotiation.agents || []).length,
      conflicts: (negotiation.conflicts || []).map((item) => item.code),
      rounds: (revision.rounds || []).length,
      convergence: revision.convergence?.reason || '',
      inactive: (negotiation.inactive || []).map((item) => item.agent_id),
    },
    price: {
      lookedUp: price.looked_up,
      filled: price.filled_from_web,
      notLookedUp: price.not_looked_up,
      reachable: price.provider_reachable,
      timedOut: price.timed_out,
      skipped: price.skipped_reason,
      error: price.error,
      attemptError: price.provider_status?.error,
    },
  };
}

function toMarkdown(rows) {
  const lines = [
    '# 规划质量横向对照（真实推演）',
    '',
    `生成时间：${new Date().toISOString()}｜场景数：${rows.length}`,
    '',
  ];
  for (const row of rows) {
    lines.push(`## ${row.city}｜${row.intent}`);
    if (row.error) {
      lines.push('', `- ❌ ${row.error}（事件 ${fmt(row.eventCount)} 条 / ${row.seconds}s）`, '');
      continue;
    }
    lines.push(
      '',
      `- 耗时 ${row.seconds}s｜节点 ${row.nodes} 个｜事件 ${row.eventCount} 条`,
      `- 候选供给：${fmt(row.supply.status)}（景点 ${fmt(row.supply.attractions)} / 餐饮 ${fmt(row.supply.foods)} / 自然 ${fmt(row.supply.scenic)}）；` +
        `补检索 ${row.supply.widened.length ? row.supply.widened.join('、') : '无'}；` +
        `周边县市景区 ${fmt(row.supply.outOfTown)} 个（最远 ${fmt(row.supply.farthest)}km）`,
      `- 配比目标 ${row.composition.targets}（${fmt(row.composition.source)}；可做到 ${fmt(row.composition.feasible)}）→ 实际 ${row.composition.actual}｜对账 ${row.composition.ok ? '一致' : '有偏差'}${row.composition.recomputed ? '（收口后重算）' : ''}`,
      row.composition.notes.length ? `- 配比偏差：${row.composition.notes.join('；')}` : '',
      `- 硬约束修复：公园 ${row.repairs.park.length} 处｜日内 ${row.repairs.travel.length} 处｜排除项 ${row.repairs.exclusion.length} 处｜住宿兜底 ${row.repairs.lodging.length} 晚`,
      row.repairs.park.length ? `  - 公园：${row.repairs.park.join('、')}` : '',
      row.repairs.travel.length ? `  - 日内移出：${row.repairs.travel.join('、')}` : '',
      row.repairs.lodging.length ? `  - 住宿兜底：${row.repairs.lodging.join('、')}` : '',
      row.repairs.lodgingMissing.length ? `  - 住宿仍缺：第 ${row.repairs.lodgingMissing.join('、')} 晚（候选池里没有可用住宿）` : '',
      row.repairs.lodgingError ? `  - 住宿兜底失败：${row.repairs.lodgingError}` : '',
      row.repairs.lodgingTrimmed.length ? `  - 住宿超夜数移出：${row.repairs.lodgingTrimmed.join('、')}` : '',
      row.composition.supplyLimited.length ? `  - 供给缺口（不是没安排）：${row.composition.supplyLimited.join('；')}` : '',
      row.repairs.closeout.length ? `  - 出稿前收口：${row.repairs.closeout.join('；')}` : '',
      row.repairs.closeoutError ? `  - ⚠️ 收口失败：${row.repairs.closeoutError}` : '',
      `- 协商：参与 ${row.negotiation.agents} 个｜分歧 ${row.negotiation.conflicts.length ? row.negotiation.conflicts.join('、') : '无'}｜` +
        `轮次 ${row.negotiation.rounds}｜${fmt(row.negotiation.convergence)}`,
      `- 未参与：${row.negotiation.inactive.length ? row.negotiation.inactive.join('、') : '无'}`,
      `- 联网取价：查 ${fmt(row.price.lookedUp)} 个 / 补到 ${fmt(row.price.filled)} 条｜来源可达 ${fmt(row.price.reachable)}` +
        (row.price.timedOut ? `｜超预算 ${row.price.timedOut} 个` : '') +
        (row.price.skipped ? `｜未跑：${row.price.skipped}` : '') +
        (row.price.error ? `｜整段失败：${row.price.error}` : '') +
        (row.price.attemptError ? `｜取价源失败：${row.price.attemptError}` : ''),
      `- 路线：${row.names.join('、')}`,
      `- 按天：${row.routeByDay.join('｜')}`,
      '',
    );
  }
  return lines.filter((line) => line !== '').join('\n') + '\n';
}

async function main() {
  const only = process.env.MATRIX_ONLY || '';
  const scenarios = only ? SCENARIOS.filter((item) => item.city.includes(only) || item.intent.includes(only)) : SCENARIOS;
  console.log('='.repeat(72));
  console.log(`规划质量横向对照（${scenarios.length} 个真实场景）`);
  console.log('='.repeat(72));
  const rows = [];
  for (const scenario of scenarios) {
    process.stdout.write(`  ▶ ${scenario.city}｜${scenario.intent} … `);
    try {
      const row = await runScenario(scenario);
      rows.push(row);
      if (row.error) {
        console.log(`❌ ${row.error}`);
      } else {
        console.log(
          `${row.seconds}s｜${row.nodes} 节点｜配比 ${row.composition.actual}｜` +
            `修复 ${row.repairs.park.length + row.repairs.travel.length + row.repairs.exclusion.length} 处｜` +
            `住宿兜底 ${row.repairs.lodging.length} 晚｜` +
            (row.repairs.closeout.length ? `收口 ${row.repairs.closeout.length} 项｜` : '') +
            `分歧 ${row.negotiation.conflicts.length}｜取价 查${fmt(row.price.lookedUp)}/补${fmt(row.price.filled)}`,
        );
      }
    } catch (error) {
      rows.push({ ...scenario, seconds: 0, error: error?.message || String(error) });
      console.log(`❌ ${error?.message || error}`);
    }
  }
  const markdown = toMarkdown(rows);
  const outPath = path.join('work', 'plan-quality-matrix.md');
  await mkdir('work', { recursive: true });
  await writeFile(outPath, markdown, 'utf8');

  // 落盘原始方案 JSON：人工抽查表（ai-service/tools/plan_review_sheet.py）要用它，
  // 而"地点好不好"机器判不了、必须有人读。文件名带模型名，方便 plus/turbo 并排。
  const modelTag = process.env.MODEL_TAG || 'default';
  const plansDir = path.join('work', 'plans');
  await mkdir(plansDir, { recursive: true });
  for (const row of rows) {
    if (!row.finalData) continue;
    const safeCity = String(row.city || 'city').replace(/[\\/:*?"<>|]/g, '_');
    const file = path.join(plansDir, `${modelTag}-${safeCity}-${String(row.intent || '').length}.json`);
    await writeFile(file, JSON.stringify(row.finalData, null, 2), 'utf8');
  }
  console.log(`方案 JSON 已写入 ${plansDir}（模型标签 ${modelTag}）`);
  console.log('');
  console.log(`报告已写入 ${outPath}`);
  console.log('='.repeat(72));
  process.exit(0);
}

main().catch((error) => {
  console.error('矩阵探针异常:', error?.stack || error);
  process.exit(1);
});
