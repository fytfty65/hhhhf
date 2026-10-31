import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * 可访问性门禁（CI 中随 e2e 一起跑）。
 *
 * 这里只锁定"不修就等于功能对部分用户不可用"的规则：按钮/链接没有可读名称、
 * 图片没有替代文本、表单控件没有关联标签、aria 属性缺失或写错。颜色对比度与
 * 地标结构涉及视觉设计取舍，单独跟踪，不在本用例里制造噪声。
 */
const CRITICAL_RULES = [
  'button-name',
  'image-alt',
  'label',
  'link-name',
  'aria-required-attr',
  'aria-valid-attr',
  'aria-valid-attr-value',
  'aria-required-children',
  'duplicate-id-aria',
];

test('大厅关键可访问性规则零违规（axe）', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('omni_user', JSON.stringify({
      id: 'a11y-user',
      username: 'a11y-user',
      nickname: 'A11y 用户',
      avatarSeed: 'a11y-user',
      avatarUrl: '',
      signature: '',
      token: 'a11y-token',
      isLoggedIn: true,
    }));
  });
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  const results = await new AxeBuilder({ page }).withRules(CRITICAL_RULES).analyze();
  const violations = results.violations.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    nodes: violation.nodes.map((node) => node.target.join(' ')),
  }));

  expect(violations, `axe 关键规则违规：${JSON.stringify(violations, null, 2)}`).toEqual([]);
});
