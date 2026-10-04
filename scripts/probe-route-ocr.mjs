/**
 * 参考路线**截图识别**真实探针（走网关的真实用户路径，零依赖）。
 *
 * 判据：
 *  1. 真图能识别出文字（这张图是 `scripts/make-route-screenshot.ps1` 现画的，含地名/门票/住宿/节奏）；
 *  2. 识别文字**先给用户核对**：本接口只回文字，不写画像 —— 调用后 GET 参考路线必须是空的；
 *  3. 把识别出的文字交给保存接口，能抽出结构化特征（图 → 文字 → 特征 全链路）；
 *  4. 坏输入（非图片 base64 / GIF）必须**如实报错**，不许返回一段编造的文字。
 *
 * 用法：
 *   powershell -File scripts/make-route-screenshot.ps1 -Out work/route-screenshot.png
 *   node scripts/probe-route-ocr.mjs
 */

import { readFile } from 'node:fs/promises';
import path from 'node:path';

const BASE = process.env.GATEWAY_BASE || 'http://127.0.0.1:8080';
const PASSWORD = 'Probe#2026';
const STAMP = String(Date.now());
const IMAGE = process.env.PROBE_IMAGE || path.join('work', 'route-screenshot.png');
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

async function get(pathname, token) {
  const response = await fetch(`${BASE}${pathname}`, { headers: { Authorization: `Bearer ${token}` } });
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

async function main() {
  console.log('='.repeat(72));
  console.log('参考路线截图识别真实探针');
  console.log('='.repeat(72));

  const account = await register(`probe_ocr_${STAMP}`);
  record('注册并拿到会话', Boolean(account.token), `HTTP ${account.status}`);
  if (!account.token) return;

  // 1) 真图识别
  const raw = await readFile(IMAGE);
  const dataUrl = `data:image/png;base64,${raw.toString('base64')}`;
  console.log(`  图片：${IMAGE}（${Math.round(raw.length / 1024)}KB）`);
  const started = Date.now();
  const ocr = await post('/api/v1/user/preference/reference-route/ocr', account.token, {
    image_base64: dataUrl,
    mime_type: 'image/png',
  });
  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  const text = String(ocr.payload?.data?.text || '');
  record('真图识别成功', ocr.status === 200 && text.length > 0, `HTTP ${ocr.status} / ${seconds}s / ${text.length} 字 / 模型 ${ocr.payload?.data?.model || '?'}`);
  console.log('  ── 识别到的文字 ──');
  text.split('\n').filter(Boolean).forEach((line) => console.log(`    ${line}`));
  console.log('  ──────────────────');

  const keys = ['博斯腾湖', '铁门关', '烤包子', '胡杨林', '民宿', '孔雀河'];
  const hits = keys.filter((key) => text.includes(key));
  record('关键地名/信息被识别出来（≥4/6 即认为可用）', hits.length >= 4, `命中 ${hits.length}/6：${hits.join('、')}`);

  // 2) 识别不落库
  const afterOcr = await get('/api/v1/user/preference/reference-route', account.token);
  const profileAfterOcr = afterOcr.payload?.data?.profile ?? null;
  record(
    '识别本身不写画像（用户核对前不学习）',
    profileAfterOcr === null,
    profileAfterOcr === null ? '调用后参考路线仍为空' : `意外写入了画像：${JSON.stringify(profileAfterOcr).slice(0, 120)}`,
  );

  // 3) 图 → 文字 → 特征
  if (text) {
    const save = await post('/api/v1/user/preference/reference-route', account.token, {
      text,
      source_label: '截图识别探针',
      keep_evidence: false,
    });
    const profile = save.payload?.data?.profile ?? save.payload?.profile ?? null;
    const mix = profile?.features?.category_mix || {};
    record(
      '识别文字能抽成结构化特征（图 → 文字 → 画像）',
      save.status === 200 && Object.keys(mix).length > 0,
      `HTTP ${save.status} 类别=${JSON.stringify(mix)} 节奏=${profile?.features?.pace ?? '?'} 消费=${profile?.features?.budget_tier ?? '?'}`,
    );
  }

  // 4) 坏输入如实报错（本地就能拦掉，不该烧模型调用）
  const badBase64 = await post('/api/v1/user/preference/reference-route/ocr', account.token, {
    image_base64: 'data:image/png;base64,!!!!',
  });
  record(
    '非 base64 图片如实报错',
    badBase64.status === 400 && Boolean(badBase64.payload?.error?.code || badBase64.payload?.code),
    `HTTP ${badBase64.status} ${badBase64.payload?.error?.code || badBase64.payload?.code || ''}`,
  );

  const gif = await post('/api/v1/user/preference/reference-route/ocr', account.token, {
    image_base64: `data:image/gif;base64,${Buffer.from('GIF89a' + '\u0000'.repeat(16)).toString('base64')}`,
  });
  record(
    'GIF 等不支持的格式如实报错并给出替代路径',
    gif.status === 400,
    `HTTP ${gif.status} ${gif.payload?.error?.message || ''}`,
  );
  const gifMessage = String(gif.payload?.error?.message || '');
  record('错误话术里必须提到可用格式或粘贴文字', /PNG|粘贴/.test(gifMessage), gifMessage.slice(0, 80));

  const huge = await post('/api/v1/user/preference/reference-route/ocr', account.token, {
    image_base64: 'A'.repeat(6 * 1024 * 1024 + 16),
  });
  record('超大图片在网关侧被拦（不转发）', huge.status === 413, `HTTP ${huge.status} ${huge.payload?.error?.code || ''}`);

  console.log('');
  const failed = results.filter((item) => !item.ok).length;
  console.log(failed === 0 ? `PASS - 截图识别 ${results.length} 项全部通过` : `FAIL - ${failed}/${results.length} 项失败`);
  results.filter((item) => !item.ok).forEach((item) => console.log(`  未通过：${item.name} — ${item.detail}`));
  process.exitCode = failed === 0 ? 0 : 1;
}

main().catch((error) => {
  console.error('探针异常:', error?.stack || error);
  process.exitCode = 1;
});
