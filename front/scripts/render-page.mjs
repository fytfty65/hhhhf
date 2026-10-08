/**
 * 把**客户端渲染**的官方页面渲染成正文文本（供 ai-service 取价用）。
 *
 * 为什么放在 front/ 下：Playwright 与浏览器都装在这一侧
 * （`PLAYWRIGHT_BROWSERS_PATH=<repo>/work/ms-playwright`），ai-service 那边没有 Node 依赖。
 *
 * 契约（Python 侧 `core/rendered_page.py` 按这个解析）：
 *   · 成功 → stdout 打印一行 JSON：{"ok":true,"url":...,"text":"<正文>","seconds":1.2}
 *   · 失败 → 打印 {"ok":false,"url":...,"error":"..."}，退出码仍是 0（让调用方按 JSON 判断）
 *
 * 用法：
 *   node front/scripts/render-page.mjs https://www.byblk.cn/goumaiyuding/ [--wait 2000]
 *
 * 只做"打开页面 + 取正文"，不做点击/登录/购票动作 —— 我们只要页面公示的票价文本。
 */

import { chromium } from 'playwright';

const args = process.argv.slice(2);
const url = args.find((item) => item.startsWith('http'));
const waitIndex = args.indexOf('--wait');
const extraWait = waitIndex >= 0 ? Number(args[waitIndex + 1]) || 0 : 0;

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

if (!url) {
  emit({ ok: false, url: '', error: '缺少 URL 参数' });
  process.exit(0);
}

const started = Date.now();
let browser = null;
try {
  browser = await chromium.launch();
  const context = await browser.newContext({
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  // ⚠️ 不能用 `networkidle`：罗布人村寨的首页挂着长轮询接口（`/api/parking/remain-space` 等），
  //    实测 30s 都等不到"网络空闲"，直接超时。改成 domcontentloaded + 固定等待。
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForTimeout(extraWait > 0 ? extraWait : 2500);
  // 正文 + 所有 iframe 的正文（有些官方票务页把票档放在 iframe 里）
  const own = await page.evaluate(() => document.body?.innerText || '');
  const frames = [];
  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    try {
      const frameText = await frame.evaluate(() => document.body?.innerText || '');
      if (frameText && frameText.trim()) frames.push(frameText);
    } catch {
      // 跨域 iframe 取不到正文就跳过（不报错）
    }
  }
  const text = [own, ...frames].filter(Boolean).join('\n');
  await context.close();
  emit({ ok: true, url, text, frames: frames.length, seconds: Number(((Date.now() - started) / 1000).toFixed(1)) });
} catch (error) {
  emit({ ok: false, url, error: `${error.name}: ${String(error.message).slice(0, 160)}` });
} finally {
  if (browser) await browser.close();
}
