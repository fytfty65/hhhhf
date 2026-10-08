/**
 * 探针：用**无头浏览器**渲染两个"客户端渲染"的官方票价页，看能不能拿到票价（2026-10-07）。
 *
 * 背景：罗布人村寨（xjlbr.com）与巴音布鲁克（byblk.cn）的票价页服务端只回壳页
 * （2490 / 1432 字符），票价在 JS 渲染后才有。用户提供的数字是 门票35/观光车15 与 联票140。
 *
 * 这个探针只回答三件事：
 *   ① 渲染后页面里到底有没有票价（35/15/140）；
 *   ② 每个页面要几秒（决定能不能进取价链路）；
 *   ③ 连续两次结果是否稳定。
 *
 * 用法（在 front 目录下，那边装了 Playwright 与浏览器）：
 *   node ../ai-service/tools/render_official_probe.mjs
 * 或指定 URL：node render_official_probe.mjs https://www.byblk.cn/goumaiyuding/
 */

import { chromium } from 'playwright';

const DEFAULT_TARGETS = [
  { label: '罗布人村寨·在线预订', url: 'https://www.xjlbr.com/ykdp', want: ['门票', '观光车', '35', '15'] },
  { label: '罗布人村寨·首页', url: 'https://www.xjlbr.com/', want: ['门票', '观光车', '35', '15'] },
  { label: '巴音布鲁克·门票预定', url: 'https://www.byblk.cn/goumaiyuding/', want: ['门票', '联票', '区间车', '140'] },
  { label: '巴音布鲁克·首页', url: 'https://www.byblk.cn/', want: ['门票', '联票', '140'] },
];

function snippets(text, want) {
  const out = [];
  for (const word of want) {
    const index = text.indexOf(word);
    if (index >= 0) {
      out.push(`${word}@${index}: ${text.slice(Math.max(0, index - 40), index + 60).replace(/\s+/g, ' ')}`);
    }
  }
  return out;
}

const targets = process.argv.length > 2
  ? [{ label: '自定义', url: process.argv[2], want: ['门票', '票价', '观光车', '区间车'] }]
  : DEFAULT_TARGETS;

const browser = await chromium.launch();
try {
  for (const target of targets) {
    const context = await browser.newContext({
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      viewport: { width: 1280, height: 900 },
    });
    const page = await context.newPage();
    for (let round = 1; round <= 2; round += 1) {
      const started = Date.now();
      try {
        await page.goto(target.url, { waitUntil: 'networkidle', timeout: 30_000 });
      } catch (error) {
        console.log(`✗ ${target.label} 第${round}轮 打开失败：${error.message}`);
        continue;
      }
      const text = await page.evaluate(() => document.body?.innerText || '');
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      const hits = snippets(text, target.want);
      console.log(
        `\n=== ${target.label} 第${round}轮｜${seconds}s｜正文 ${text.length} 字符｜命中 ${hits.length}/${target.want.length}`,
      );
      for (const hit of hits.slice(0, 6)) console.log('   ', hit);
      if (hits.length === 0) console.log('    正文前 200 字：', text.slice(0, 200).replace(/\s+/g, ' '));
    }
    await context.close();
  }
} finally {
  await browser.close();
}
