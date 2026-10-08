/**
 * 探针：巴楚县政府网里**红海景区**的页面到底有没有全价票（2026-10-07）。
 *
 * 用户说：该域名公示了优惠政策（半票群体），但"如果页面缺少全价票字段，需要确认是否另有票务子页"。
 * 这个探针就回答这一件事：
 *   ① 首页/栏目里有哪些含"红海"的链接；
 *   ② 常见的站内搜索 URL 能不能搜出红海相关页面；
 *   ③ 这些页面渲染后有没有**门票/票价 + 数字**（尤其"全价票"）。
 *
 * 用法（front 目录）：node scripts/probe-bachu-honghai.mjs
 */

import { chromium } from 'playwright';

const HOME = 'https://www.bachu.gov.cn/';
const SEARCH_PATTERNS = [
  'https://www.bachu.gov.cn/search?searchWord=%E7%BA%A2%E6%B5%B7',
  'https://www.bachu.gov.cn/searchList.html?searchWord=%E7%BA%A2%E6%B5%B7',
  'https://www.bachu.gov.cn/bcx/search.shtml?q=%E7%BA%A2%E6%B5%B7',
];
const PRICE_LINE = /门票|票价|全价|半票|观光车|区间车|优惠/;

const browser = await chromium.launch();
const context = await browser.newContext({
  userAgent:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  viewport: { width: 1280, height: 900 },
});
const page = await context.newPage();

try {
  await page.goto(HOME, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForTimeout(2000);
  const links = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a')).map((a) => ({ text: (a.innerText || '').trim(), href: a.href })),
  );
  const honghai = links.filter((item) => /红海/.test(`${item.text} ${item.href}`));
  const travel = links.filter((item) => /旅游|景区|文化|文广|文旅/.test(item.text)).slice(0, 12);
  console.log(`首页链接 ${links.length} 个｜含"红海" ${honghai.length} 个｜疑似旅游栏目 ${travel.length} 个`);
  for (const item of honghai.slice(0, 10)) console.log('  红海:', item.text.slice(0, 30), item.href.slice(0, 100));
  for (const item of travel) console.log('  栏目:', item.text.slice(0, 20), item.href.slice(0, 100));

  // 候选页：栏目页 + 站内搜索
  const candidates = [...new Set([...travel.map((item) => item.href), ...SEARCH_PATTERNS])];
  for (const url of candidates.slice(0, 12)) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 25_000 });
      await page.waitForTimeout(1500);
    } catch (error) {
      console.log(`\n=== ${url.slice(0, 90)}｜打开失败 ${error.name}`);
      continue;
    }
    const text = await page.evaluate(() => document.body?.innerText || '');
    const lines = text
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && PRICE_LINE.test(line));
    console.log(`\n=== ${url.slice(0, 90)}｜正文 ${text.length} 字符｜相关行 ${lines.length}`);
    for (const line of lines.slice(0, 8)) console.log('   ', line.slice(0, 120));
  }
} finally {
  await context.close();
  await browser.close();
}
