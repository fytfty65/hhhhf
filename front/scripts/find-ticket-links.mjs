/**
 * 探针：在渲染后的页面里找"在线购票/门票"这类链接，定位票价子页（2026-10-07）。
 * 用法（front 目录）：node scripts/find-ticket-links.mjs <url>
 */

import { chromium } from 'playwright';

const url = process.argv[2];
if (!url) {
  console.log('缺少 URL');
  process.exit(0);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForTimeout(2500);
  const links = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a')).map((a) => ({ text: (a.innerText || '').trim(), href: a.href })),
  );
  const wanted = links.filter((item) =>
    /购票|预订|订票|门票|ticket|piao|booking|youhui|menpiao/i.test(`${item.text} ${item.href}`),
  );
  console.log(JSON.stringify(wanted.slice(0, 25), null, 2));
} finally {
  await browser.close();
}
