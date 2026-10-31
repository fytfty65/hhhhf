/**
 * 给「当地文化 / 无障碍通行」两块面板截图，供人工审阅排版（2026-10-08）。
 *
 * 为什么要单独截：E2E 只能断言"某段文字在 DOM 里"，看不出**好不好看**。
 * 排版问题必须用眼睛看，所以这里按真实桌面宽度截两张整块图。
 *
 * 用法（front 目录）：node scripts/shot-specialist-panels.mjs
 */

import { chromium } from 'playwright';

const base = process.env.E2E_BASE_URL || 'http://localhost:3001';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

// 先访问一次拿到同源，再用真实 E2E 的 mock 方式喂数据太重；
// 这里直接把组件数据注入到页面里已有的 __SHOT__ 钩子（见下方 addInitScript）。
await page.addInitScript(() => {
  const route = [
    {
      day: 1, location: '喀什古城', name: '喀什古城', time: '09:00', type: '文化',
      cost_estimate: '¥29', tags: ['文化'], data_sources: { cost_estimate: 'platform' },
      trust: 'estimated', trust_label: '估算', source_type: 'platform', freshness: 'fresh',
    },
    {
      day: 1, location: '香妃园', name: '香妃园', time: '14:00', type: '文化',
      cost_estimate: '¥30', tags: ['文化'], data_sources: { cost_estimate: 'web_page' },
      trust: 'verified', trust_label: '已核实', source_type: 'official_page', freshness: 'fresh',
    },
  ];
  const proposals = [
    {
      agent_id: 'culture-agent', domain: 'culture', status: 'ok', confidence: 0.6,
      candidates: [
        {
          name: '喀什古城',
          background:
            '喀什古城是我国唯一保存完整的以伊斯兰文化为特色的迷宫式城市街区，街巷以艾提尕尔清真寺为中心向外放射，生土建筑依地势层层叠叠。老城民居的「过街楼」「半街楼」是应对狭窄巷道的传统做法。',
          etiquette: '进入居民区拍照前先征得同意（不少院落是住家）；清真寺周边着装宜端庄；当地人递茶递食物用双手接更得体。',
          recommended_stay: '半天到一天',
          sources: [
            { label: '喀什地区行政公署', url: 'http://www.kashi.gov.cn/' },
            { label: '新疆维吾尔自治区文化和旅游厅', url: 'http://wlt.xinjiang.gov.cn/' },
          ],
          reviewed_at: '2026-10-08',
          source_type: 'knowledge_base',
        },
        { name: '喀什玉石文化博物馆', background: '', sources: [], note: '暂无可靠来源，未提供背景说明。' },
        { name: '百年喀什馆', background: '', sources: [], note: '暂无可靠来源，未提供背景说明。' },
      ],
    },
    {
      agent_id: 'accessibility-agent', domain: 'accessibility', status: 'ok', confidence: 0,
      candidates: [
        { name: '喀什古城', verified: false, status_text: '无障碍信息：未核实', facilities: { 入口坡度: 'unknown', 电梯: 'unknown', 无障碍卫生间: 'unknown', 接驳车: 'unknown' }, checklist: ['入口坡度与台阶（有无坡道/无障碍通道）', '电梯（是否有、是否可用、轿厢尺寸）', '无障碍卫生间（位置与开放情况）', '接驳车/区间车（能否放轮椅、是否需预约）'] },
        { name: '香妃园', verified: false, status_text: '无障碍信息：未核实', facilities: {}, checklist: ['入口坡度与台阶（有无坡道/无障碍通道）', '电梯（是否有、是否可用、轿厢尺寸）', '无障碍卫生间（位置与开放情况）', '接驳车/区间车（能否放轮椅、是否需预约）'] },
      ],
    },
  ];

  const OriginalWebSocket = window.WebSocket;
  // @ts-ignore
  window.WebSocket = class extends OriginalWebSocket {
    constructor(url) {
      super(url);
      setTimeout(() => {
        this.onmessage?.({
          data: JSON.stringify({ type: 'target_city', payload: { name: '喀什', lnglat: [75.99, 39.47] } }),
        });
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: 'stream_token',
              payload:
                '[FINAL_JSON]' +
                JSON.stringify({
                  status: 'degraded_fallback',
                  negotiation_summary: '已按预算与偏好完成协商',
                  route,
                  team_satisfaction: { 摄影师: 62 },
                  specialist_runtime: { proposals },
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

const textarea = page.locator('textarea').first();
if (await textarea.count()) {
  await textarea.fill('去喀什玩3天，看喀什古城和香妃园');
  const startBtn = page.getByRole('button', { name: /开始|推演|生成/ }).first();
  if (await startBtn.count()) await startBtn.click();
}
await page.waitForTimeout(4000);

const planTab = page.getByTestId('workspace-layer-plan');
if (await planTab.count()) {
  await planTab.click();
  await page.waitForTimeout(1200);
}

const culture = page.getByTestId('culture-panel');
const access = page.getByTestId('accessibility-panel');
console.log('culture-panel 出现:', await culture.count());
console.log('accessibility-panel 出现:', await access.count());

if (await culture.count()) {
  await culture.screenshot({ path: '../work/shot-culture-panel.png', animations: 'disabled' });
}
if (await access.count()) {
  await access.screenshot({ path: '../work/shot-accessibility-panel.png', animations: 'disabled' });
}

await browser.close();
