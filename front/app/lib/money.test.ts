import test from 'node:test';
import assert from 'node:assert/strict';
import { formatCents, majorToCents, MAX_MONEY_CENTS } from './money.ts';

// 金额精度是 #6 整改的核心：读取与写入都必须走整数分，不能借道浮点。

test('formatCents 固定小数位渲染整数分', () => {
  assert.equal(formatCents(12850), '128.50');
  assert.equal(formatCents(5), '0.05');
  assert.equal(formatCents(0), '0.00');
  assert.equal(formatCents(-12850), '-128.50');
  assert.equal(formatCents(1, 0), '1');
});

test('majorToCents 精确解析元输入', () => {
  assert.equal(majorToCents('128.5'), 12850);
  assert.equal(majorToCents('128.50'), 12850);
  assert.equal(majorToCents('0.05'), 5);
  assert.equal(majorToCents('1,280.00'), 128000);
  assert.equal(majorToCents('100'), 10000);
});

test('majorToCents 拒绝非法或超精度输入', () => {
  for (const raw of ['', '   ', 'abc', '-1', '1.234', '1..2', '1e3', '.5']) {
    assert.equal(majorToCents(raw), null, `"${raw}" 必须被拒绝`);
  }
  assert.equal(majorToCents(String(MAX_MONEY_CENTS)), null);
});

test('majorToCents 与 formatCents 往返一致', () => {
  for (const raw of ['0.01', '0.10', '9.99', '128.50', '999999.99']) {
    const cents = majorToCents(raw);
    assert.ok(cents !== null, `${raw} 应可解析`);
    assert.equal(formatCents(cents), raw);
  }
});
