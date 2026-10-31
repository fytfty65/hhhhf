// 风险通知的“人话”说明：等级文本、现在要做什么、影响范围与无需处理的部分。
//
// 诚实口径：
//  · 等级文字只由后端给出的 severity 映射，不自行升级/降级；
//  · 估算信号一律写“建议关注”，不把估算当成已确认事实；
//  · 没有受影响节点就不编节点名；拿不到全量行程就不说“其余不受影响”；
//  · “暂不处理”只在本机生效、当天有效，不改行程，也不向后端伪造处理状态。

export type RiskSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | string;

export const severityLabel = (severity: RiskSeverity): string => {
  if (severity === 'HIGH') return '高风险';
  if (severity === 'MEDIUM') return '需关注';
  if (severity === 'LOW') return '低风险';
  return '待确认';
};

/** 置信/来源措辞：估算或低等级都统一引导成“建议关注”，避免过度焦虑。 */
export function confidenceLabel(input: { estimated: boolean; severity: RiskSeverity }): string {
  if (input.estimated) return '估算信号 · 建议关注';
  if (input.severity === 'LOW') return '实测信号 · 建议关注';
  return '实测信号';
}

/** 一句话“现在需要做什么”：只列当前真能做的事，不承诺自动改方案。 */
export function nextActionText(input: {
  severity: RiskSeverity;
  estimated: boolean;
  hasReplan: boolean;
  affectedCount: number;
}): string {
  const { severity, estimated, hasReplan, affectedCount } = input;
  if (hasReplan) {
    return affectedCount > 0
      ? `先确认这次重排提案：会调整 ${affectedCount} 处，确认前方案不变。`
      : '先确认这次重排提案：提案没给出受影响节点，确认前方案不变。';
  }
  if (affectedCount > 0) {
    return `先核对这些受影响的安排，需要时再按这条提醒重排；不会自动改方案。`;
  }
  if (estimated || severity === 'LOW') {
    return '建议关注，暂不用改行程；有新的实测更新会再提醒。';
  }
  return '暂不用改行程，留意后续更新。';
}

/** 影响范围 + 无需处理的部分；两边都没有就不返回文案（不硬凑）。 */
export function scopeText(affected: string[], unaffected: string[]): string {
  const parts: string[] = [];
  if (affected.length > 0) parts.push(`影响范围：${affected.join('、')}`);
  if (unaffected.length > 0) parts.push(`无需处理：其余 ${unaffected.length} 个安排本次不受影响`);
  return parts.join('；');
}

/** 暂缓到本地当天结束，隔天自然恢复提醒。 */
export function endOfLocalDay(now: number = Date.now()): number {
  const date = new Date(now);
  date.setHours(24, 0, 0, 0);
  return date.getTime();
}

export type SnoozeMap = Record<string, number>;

export function parseSnoozeMap(raw: string | null): SnoozeMap {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const result: SnoozeMap = {};
    Object.entries(parsed as Record<string, unknown>).forEach(([id, until]) => {
      const value = Number(until);
      if (Number.isFinite(value) && value > 0) result[id] = value;
    });
    return result;
  } catch {
    return {};
  }
}

/** 丢掉已经过期的暂缓记录，避免昨天暂缓的条目今天还被压着。 */
export function pruneSnoozes(map: SnoozeMap, now: number = Date.now()): SnoozeMap {
  const result: SnoozeMap = {};
  Object.entries(map).forEach(([id, until]) => {
    if (Number(until) > now) result[id] = Number(until);
  });
  return result;
}

export function isSnoozed(map: SnoozeMap, id: string, now: number = Date.now()): boolean {
  const until = Number(map[id]);
  return Number.isFinite(until) && until > now;
}

export function applySnooze(map: SnoozeMap, id: string, snoozed: boolean, now: number = Date.now()): SnoozeMap {
  const next = { ...map };
  if (snoozed) next[id] = endOfLocalDay(now);
  else delete next[id];
  return next;
}
