/**
 * 金额工具：与后端一致的「整数最小单位」约定。
 *
 * 后端所有金额都用整数分存储和返回（`*_cents`），并显式给出 `precision`。
 * 前端只在输入时把用户填写的「元」转成分，展示时再格式化回来；中间不做
 * 浮点乘法，避免 128.50 变成 128.499999… 这类误差。
 */

export const DEFAULT_CURRENCY_PRECISION = 2;

/** 与后端 maxMoneyCents 一致：100 亿元，超出即视为输入错误。 */
export const MAX_MONEY_CENTS = 1_000_000_000_000;

/**
 * 把整数最小单位格式化为固定小数位的字符串。
 * 12850 -> "128.50"（precision=2）。
 */
export function formatCents(cents: number, precision = DEFAULT_CURRENCY_PRECISION): string {
  const p = Number.isInteger(precision) && precision >= 0 ? precision : DEFAULT_CURRENCY_PRECISION;
  if (!Number.isFinite(cents)) return (0).toFixed(p);
  const rounded = Math.round(cents);
  const sign = rounded < 0 ? '-' : '';
  const abs = Math.abs(rounded);
  const base = 10 ** p;
  const major = Math.floor(abs / base);
  const minor = abs % base;
  return `${sign}${p === 0 ? major : `${major}.${String(minor).padStart(p, '0')}`}`;
}

/**
 * 把用户输入的「元」字符串精确转成整数分；非法输入返回 null。
 * 只接受非负数字（允许千分位逗号），小数位不得超过 precision。
 */
export function majorToCents(input: string, precision = DEFAULT_CURRENCY_PRECISION): number | null {
  const p = Number.isInteger(precision) && precision >= 0 ? precision : DEFAULT_CURRENCY_PRECISION;
  const raw = String(input ?? '').trim().replace(/,/g, '');
  if (!raw) return null;

  // 整数部分必填，小数部分可省略；小数位不超过 precision。
  const match = raw.match(/^(\d+)(?:\.(\d*))?$/);
  if (!match) return null;
  const [, intPart, fracRaw = ''] = match;
  if (fracRaw.length > p) return null;

  const frac = fracRaw.padEnd(p, '0');
  const cents = Number(intPart) * 10 ** p + (p === 0 ? 0 : Number(frac));
  if (!Number.isSafeInteger(cents) || cents > MAX_MONEY_CENTS) return null;
  return cents;
}
