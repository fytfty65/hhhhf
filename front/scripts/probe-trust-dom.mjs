/**
 * 调试：在真实 E2E 流程里把"面板拿到的节点"打出来（2026-10-08）。
 *
 * 复制 plan-governance 的 mock，走到方案页后：
 *   ① 打印 DOM 里所有 data-testid 的计数；
 *   ② 打印第一个节点卡片区域的文字，看 trust 徽章到底有没有出现。
 *
 * 用法（front 目录）：node scripts/probe-trust-dom.mjs
 */

import { chromium } from 'playwright';

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:3001';

const browser = await chromium.launch();
const page = await browser.newPage();

// 与 E2E 相同的 mock：只保留能让方案页出现的部分
await page.addInitScript(() => {
  const route = [
    {
      day: 1,
      location: '钟楼酒店',
      name: '钟楼酒店',
      time: '09:00',
      type: '住宿',
      cost_estimate: '¥300',
      tags: ['住宿'],
      data_sources: { cost_estimate: 'rollinggo' },
      trust: 'verified',
      trust_label: '已核实',
      source_type: 'rollinggo',
      freshness: 'fresh',
      derived_confidence: 0.75,
    },
    {
      day: 1,
      location: '陕西历史博物馆',
      name: '陕西历史博物馆',
      time: '10:30',
      type: '博物馆',
      cost_estimate: '¥0',
      tags: ['文化'],
      data_sources: { cost_estimate: 'web_page' },
      trust: 'stale',
      trust_label: '信息较旧',
      source_type: 'official_page',
      freshness: 'stale',
      derived_confidence: 0.45,
    },
  ];

  const OriginalWebSocket = window.WebSocket;
  // @ts-ignore
  window.WebSocket = class extends OriginalWebSocket {
    constructor(url) {
      super(url);
      setTimeout(() => {
        this.onmessage?.({
          data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }),
        });
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: 'stream_token',
              payload:
                '[FINAL_JSON]' +
                JSON.stringify({
                  status: 'degraded_fallback',
                  negotiation_summary: '调试用',
                  route,
                  team_satisfaction: { 'E2E 旅行者': 62 },
                  correlation_id: 'probe-1',
                }),
            }),
          });
        }, 40);
      }, 20);
    }
  };
});

await page.goto(base, { waitUntil: 'commit', timeout: 60000 });
await page.waitForTimeout(3000);

// 输入意图并开始推演
const textarea = page.locator('textarea').first();
if (await textarea.count()) {
  await textarea.fill('去西安玩2天');
  const startBtn = page.getByRole('button', { name: /开始|推演|生成/ }).first();
  if (await startBtn.count()) await startBtn.click();
}

await page.waitForTimeout(4000);

const counts = await page.evaluate(() => {
  const ids = ['trust-badge', 'trust-summary', 'price-provenance'];
  const out = {};
  for (const id of ids) out[id] = document.querySelectorAll(`[data-testid="${id}"]`).length;
  const badges = Array.from(document.querySelectorAll('[data-testid="trust-badge"]')).map((el) => ({
    trust: el.getAttribute('data-trust'),
    text: (el.textContent || '').trim(),
  }));
  const bodyText = (document.body && document.body.innerText) || '';
  return { counts: out, badges, bodyLength: bodyText.length, head: bodyText.slice(0, 200) };
});

console.log('counts:', JSON.stringify(counts.counts));
console.log('badges:', JSON.stringify(counts.badges));
console.log('正文长度:', counts.bodyLength);
console.log('正文开头:', String(counts.head || '').replace(/\s+/g, ' ').slice(0, 160));

await browser.close();
