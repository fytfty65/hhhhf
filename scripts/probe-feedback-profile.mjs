/**
 * 「嫌远 ≠ 不喜欢山」网关侧验证探针（2026-10-05）。
 *
 * 验证的是 gateway/internal/service/preferences.go 的口径修正：
 * 负反馈原因能解释这次安排（太远/太贵/太赶）时，**不把地点名的细标签记成类别级不喜欢**。
 *
 * 做法（两个对照用户，各提交一条真实反馈，再读回画像）：
 *   A：Target=慕士塔格峰（名字命中「山岳」），Score=-1，Reason="太远了，来回太折腾"
 *      → 期望 disliked_tags **不含** 山岳；含"距离"意图由 ai-service 侧读取原因文本处理。
 *   B：Target=慕士塔格峰，Score=-1，Reason="人太多了"
 *      → 期望 disliked_tags **含** 山岳（原因说不清，确实是这一类不满意）。
 *   C：Target=某高档餐厅，Score=-1，Reason="太贵了，不值"
 *      → 期望 disliked_tags 不含「地方菜」，且 budget_tendency=low（省钱信号不能被改动丢掉）。
 *
 * 画像从**网关数据库**读回（不靠接口自述）：`user_preferences` 表。
 *
 * 用法：node scripts/probe-feedback-profile.mjs
 *      $env:GATEWAY_DB='gateway\data\omniroute.db'; node scripts/probe-feedback-profile.mjs
 */

import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const PASSWORD = 'Probe#2026';
const DB = process.env.GATEWAY_DB || path.join('gateway', 'data', 'omniroute.db');

const CASES = [
  { key: 'A', target: '慕士塔格峰', reason: '太远了，来回太折腾', expectTag: '山岳', expectPresent: false },
  { key: 'B', target: '慕士塔格峰', reason: '人太多了', expectTag: '山岳', expectPresent: true },
  { key: 'C', target: '某高档餐厅', reason: '太贵了，不值', expectTag: '地方菜', expectPresent: false, expectBudget: 'low' },
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

/** 直接读网关数据库里的画像行（接口自述不算证据）。 */
function readProfileRow(userId) {
  const script = [
    'import json,sqlite3,sys',
    'con = sqlite3.connect(sys.argv[1])',
    'con.row_factory = sqlite3.Row',
    'row = con.execute("select liked_tags, disliked_tags, budget_tendency, feedback_count from user_preferences where user_id = ?", (sys.argv[2],)).fetchone()',
    'print(json.dumps(dict(row) if row else {}, ensure_ascii=False))',
  ].join('\n');
  const out = execFileSync(process.env.PYTHON_BIN || 'python', ['-c', script, DB, userId], {
    encoding: 'utf8',
    // 子进程必须显式声明 utf-8：Windows 默认 stdout 编码会把「山岳」变成乱码，
    // 于是一个**行为正确**的用例会因为字符串比对失败被判失败（实测踩过）。
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  return JSON.parse(out.trim() || '{}');
}

function parseTags(raw) {
  const text = String(raw || '').trim();
  if (!text) return [];
  try {
    const value = JSON.parse(text);
    return Array.isArray(value) ? value.map(String) : [text];
  } catch {
    return text.split(',').map((item) => item.replace(/["\[\]]/g, '').trim()).filter(Boolean);
  }
}

async function main() {
  const stamp = String(Date.now());
  console.log('='.repeat(72));
  console.log('网关侧验证：嫌远/嫌贵/太赶 ≠ 不喜欢那一类地点');
  console.log('='.repeat(72));
  const results = [];
  for (const item of CASES) {
    const username = `pref_${item.key}_${stamp}`;
    const account = await register(username);
    if (!account.token) throw new Error(`${item.key} 注册失败`);
    const room = await post('/api/v1/room/create', account.token, { username, room_name: `画像${item.key}${stamp}` });
    const roomId = String(room.payload.room_id || '');
    if (!roomId) throw new Error(`${item.key} 建房失败`);
    const feedback = await post('/api/v1/feedback', account.token, {
      room_id: roomId, user_id: account.userId, target: item.target, score: -1, reason: item.reason,
    });
    if (feedback.status !== 200) throw new Error(`${item.key} 反馈失败 HTTP ${feedback.status}`);
    const row = readProfileRow(account.userId);
    const disliked = parseTags(row.disliked_tags);
    const present = disliked.includes(item.expectTag);
    const ok = present === item.expectPresent && (!item.expectBudget || row.budget_tendency === item.expectBudget);
    results.push({ ...item, disliked, budget: row.budget_tendency, ok });
    console.log(
      `${ok ? '✅' : '❌'} ${item.key}｜${item.target}｜原因「${item.reason}」\n` +
        `     disliked_tags=${JSON.stringify(disliked)}（期望${item.expectPresent ? '含' : '不含'}「${item.expectTag}」）` +
        `｜budget=${row.budget_tendency}${item.expectBudget ? `（期望 ${item.expectBudget}）` : ''}`,
    );
  }
  const failed = results.filter((item) => !item.ok);
  console.log('='.repeat(72));
  console.log(failed.length ? `❌ ${failed.length} 个用例不符合预期` : '✅ 全部符合预期：安排类原因不再污染类别偏好');
  console.log('='.repeat(72));
  return failed.length ? 1 : 0;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error('画像验证探针异常:', error?.stack || error);
    process.exit(1);
  });
