/**
 * 探针：用无头浏览器把罗布人村寨的**公众号文章**与站内其它页渲染一遍，
 * 看票价（门票 35 / 观光车 15）到底在哪儿公示（2026-10-07）。
 *
 * 匿名抓 `mp.weixin.qq.com` 只拿到 480 字符壳页（"在小说阅读器读本章"），但**浏览器里能看**——
 * 公众号正文是服务端渲染 + JS 补全，用 Playwright 打开通常能拿到全文。
 *
 * 用法（front 目录）：node ../ai-service/tools/render_loburen_probe.mjs
 */

import { chromium } from 'playwright';

const TARGETS = [
  ['公众号·区间车公告', 'https://mp.weixin.qq.com/s/QlsSPaoPEvnjl-t8cYccWg'],
  ['公众号·最大承载量', 'https://mp.weixin.qq.com/s/4mhGQ9EWKqKeHh2rpTmzDg'],
  ['站内·景区公告', 'https://www.xjlbr.com/jqgg.html'],
  ['站内·游客须知', 'https://www.xjlbr.com/ykxz'],
  ['站内·攻略服务', 'https://www.xjlbr.com/lxtj'],
  ['站内·景点中心', 'https://www.xjlbr.com/jdzx'],
];

const browser = await chromium.launch();
try {
  for (const [label, url] of TARGETS) {
    const context = await browser.newContext({
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      viewport: { width: 1280, height: 900 },
    });
    const page = await context.newPage();
    const started = Date.now();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      await page.waitForTimeout(3000);
      const text = await page.evaluate(() => document.body?.innerText || '');
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      const hits = text
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line && /门票|票价|观光车|区间车|35|15元/.test(line));
      console.log(`\n=== ${label}｜${seconds}s｜正文 ${text.length} 字符｜相关行 ${hits.length}`);
      for (const hit of hits.slice(0, 10)) console.log('   ', hit.slice(0, 120));
      if (hits.length === 0) console.log('    正文前 160 字：', text.slice(0, 160).replace(/\s+/g, ' '));
    } catch (error) {
      console.log(`\n=== ${label}｜失败：${error.name}: ${String(error.message).slice(0, 80)}`);
    }
    await context.close();
  }
} finally {
  await browser.close();
}
