// 超支替代方案：预算不够时，把"能先动哪里"算成可执行选项。
//
// 诚实口径：
//  · 只用已经拿到的节点报价排序，没有报价的节点不参与（不猜金额）；
//  · 「最多可省」等于该节点当前报价，替代品更贵时会打折，界面写明以新方案报价为准；
//  · 找不到任何可动的报价项时，只剩"按预算重排"一条，不编造省钱清单。

import { nodeCategory, type ScopedCategory, type ScopedRouteNode } from './scopedReplan.ts';

export interface OverageOption {
  id: string;
  kind: 'node' | 'lodging' | 'budget';
  label: string;
  detail: string;
  instruction: string;
  maxSaving?: number;
  /** 针对的具体节点名（node/lodging 项才有），用于合并重排与逐项核对。 */
  target?: string;
}

const money = (value: number) => `¥${Math.round(value).toLocaleString()}`;

function pricedNodes(nodes: ScopedRouteNode[]): Array<{ node: ScopedRouteNode; category: ScopedCategory | 'other'; cost: number }> {
  const result: Array<{ node: ScopedRouteNode; category: ScopedCategory | 'other'; cost: number }> = [];
  (Array.isArray(nodes) ? nodes : []).forEach((node) => {
    const name = String(node?.name || node?.location || '').trim();
    const raw = (node as any)?.cost_estimate ?? (node as any)?.cost;
    const cost = typeof raw === 'number' ? raw : Number(String(raw ?? '').replace(/[^0-9.]/g, ''));
    if (!name || !Number.isFinite(cost) || cost <= 0) return;
    result.push({ node, category: nodeCategory(node), cost });
  });
  return result;
}

/**
 * 生成 1~limit 条"先动哪里"的选项 + 一条"按预算重排"兜底选项。
 * 住宿单独成项：换住宿通常比换单个景点省得多，也不是同一类判断。
 */
export function buildOverageOptions(input: {
  nodes: ScopedRouteNode[];
  budget: number | null;
  shortfall: number;
  limit?: number;
}): OverageOption[] {
  const limit = Math.max(1, Math.round(input.limit ?? 3));
  const priced = pricedNodes(input.nodes);
  const options: OverageOption[] = [];

  const lodging = priced
    .filter((item) => item.category === 'lodging')
    .sort((a, b) => b.cost - a.cost)[0];
  if (lodging) {
    options.push({
      id: 'lodging',
      kind: 'lodging',
      label: '住宿降一档',
      detail: `当前住宿报价 ${money(lodging.cost)}，换更实惠的同类最多可省这么多（实际以新方案报价为准）`,
      instruction: '住宿换成更实惠的同类，景点、交通与餐饮保持原样',
      maxSaving: lodging.cost,
      target: String(lodging.node?.name || lodging.node?.location || '').trim(),
    });
  }

  const others = priced
    .filter((item) => item.category !== 'lodging')
    .sort((a, b) => b.cost - a.cost)
    .slice(0, limit);
  others.forEach((item, index) => {
    const name = String(item.node?.name || item.node?.location || '').trim();
    options.push({
      id: `node-${index}`,
      kind: 'node',
      label: `把「${name}」换成更便宜的同类`,
      detail: `当前报价 ${money(item.cost)}，换成同类最多可省这么多（实际以新方案报价为准）`,
      instruction: `把「${name}」换成更便宜的同类，其它安排保持原样`,
      maxSaving: item.cost,
      target: name,
    });
  });

  const budget = Number.isFinite(Number(input.budget)) && Number(input.budget) > 0 ? Number(input.budget) : null;
  const shortfall = Number.isFinite(input.shortfall) && input.shortfall > 0 ? input.shortfall : 0;
  options.push({
    id: 'budget',
    kind: 'budget',
    label: budget ? `按预算 ${money(budget)} 重排` : '按预算重新压缩',
    detail: shortfall > 0
      ? `当前超出 ${money(shortfall)}，优先动最贵的一两项，其余尽量保持原样`
      : '优先动最贵的一两项，其余尽量保持原样',
    instruction: budget
      ? `总花费请压到 ${money(budget)} 以内，优先把最贵的节点换成更便宜的同类，其它安排尽量不动`
      : '总花费请压到当前预算以内，优先把最贵的节点换成更便宜的同类，其它安排尽量不动',
  });

  return options;
}

export interface OverageResult {
  tone: 'ok' | 'warn';
  text: string;
}

/** 重排回来后的核对：只说后端真给了的预算数字，没给就说无法确认。 */
export function summarizeOverageResult(input: {
  verifiedCost: number | null;
  estimatedCost: number | null;
  budget: number | null;
  shortfall: number | null;
}): OverageResult {
  const total = (Number(input.verifiedCost) || 0) + (Number(input.estimatedCost) || 0);
  const budget = Number.isFinite(Number(input.budget)) && Number(input.budget) > 0 ? Number(input.budget) : null;
  const shortfall = Number.isFinite(Number(input.shortfall)) ? Number(input.shortfall) : null;
  if (total <= 0) {
    return { tone: 'warn', text: '已重新排过，但这次没有返回可用的预算核对，无法确认是否已经压到预算内。' };
  }
  if (budget) {
    if ((shortfall !== null && shortfall > 0) || total > budget) {
      const gap = shortfall !== null && shortfall > 0 ? shortfall : total - budget;
      return { tone: 'warn', text: `已重新排过，但仍超出 ${money(gap)}（当前 ${money(total)} / 预算 ${money(budget)}）。可以再换掉一项，或调高预算。` };
    }
    return { tone: 'ok', text: `已重新排过：当前 ${money(total)}，预算 ${money(budget)}，不再超支。` };
  }
  return { tone: 'warn', text: `已重新排过，当前合计 ${money(total)}；后端没有给出预算数字，无法判断是否压到预算内。` };
}

export type OverageOptionStatus = 'enough' | 'partial' | 'unnecessary';

export interface OverageOptionAssessment {
  option: OverageOption;
  status: OverageOptionStatus;
  /** 只动这一项后还差的金额（status=partial 时才有意义）。 */
  remaining: number;
}

/**
 * 按当前缺口评估每条替代项还成不成立。
 *  · 缺口已归零 → 全部 unnecessary（预算调高后就不该再让用户去换便宜货）；
 *  · 单项最多能省 ≥ 缺口 → enough（单独换它就够）；
 *  · 否则 partial，并给出还差多少，提示需要叠加。
 * "按预算重排"本来就是整单压缩，视为 enough。
 */
export function assessOverageOptions(options: OverageOption[], shortfall: number): OverageOptionAssessment[] {
  const need = Number.isFinite(shortfall) && shortfall > 0 ? shortfall : 0;
  return (Array.isArray(options) ? options : []).map((option) => {
    if (need <= 0) return { option, status: 'unnecessary' as const, remaining: 0 };
    const saving = Number(option.maxSaving) || 0;
    if (option.kind === 'budget' || saving >= need) return { option, status: 'enough' as const, remaining: 0 };
    return { option, status: 'partial' as const, remaining: need - saving };
  });
}

export interface BudgetChangeSummary {
  tone: 'ok' | 'warn';
  text: string;
  /** 后端是否真的按新预算核对（false 时不能宣称预算已改）。 */
  adopted: boolean;
  stillOver: boolean;
}

/**
 * 预算改动后的核对：先说清预算到底改没改，再说清还需要不需要动替代项。
 * 后端没采用新预算 / 没给预算核对时，一律如实说明，不把旧数字当成新结果。
 */
export function summarizeBudgetChange(input: {
  requestedBudget: number;
  previousBudget: number | null;
  newBudget: number | null;
  verifiedCost: number | null;
  estimatedCost: number | null;
  shortfall: number | null;
}): BudgetChangeSummary {
  const requested = Number(input.requestedBudget);
  if (!Number.isFinite(requested) || requested <= 0) {
    return { tone: 'warn', text: '没有拿到有效的预算金额，这次没有改动预算。', adopted: false, stillOver: false };
  }
  const total = (Number(input.verifiedCost) || 0) + (Number(input.estimatedCost) || 0);
  const newBudget = Number.isFinite(Number(input.newBudget)) && Number(input.newBudget) > 0 ? Number(input.newBudget) : null;
  const shortfall = Number.isFinite(Number(input.shortfall)) ? Number(input.shortfall) : null;
  if (!newBudget) {
    return {
      tone: 'warn',
      text: `已重新排过，但没有返回新的预算核对，无法确认预算是否已改成 ${money(requested)}。`,
      adopted: false,
      stillOver: false,
    };
  }
  if (Math.abs(newBudget - requested) > 1) {
    const previous = Number.isFinite(Number(input.previousBudget)) && Number(input.previousBudget) > 0
      ? `仍按 ${money(Number(input.previousBudget))} 核对`
      : `仍按 ${money(newBudget)} 核对`;
    return {
      tone: 'warn',
      text: `这次没有按 ${money(requested)} 重排（后端${previous}），预算没改成；可以在诉求里直接写“预算 ${money(requested)}”。`,
      adopted: false,
      stillOver: shortfall !== null && shortfall > 0,
    };
  }
  if (total <= 0) {
    return {
      tone: 'warn',
      text: `预算已改为 ${money(requested)}，但这次没有返回可用的花费核对，无法确认是否仍超支。`,
      adopted: true,
      stillOver: false,
    };
  }
  if ((shortfall !== null && shortfall > 0) || total > newBudget) {
    const gap = shortfall !== null && shortfall > 0 ? shortfall : total - newBudget;
    return {
      tone: 'warn',
      text: `预算已改为 ${money(newBudget)}：当前 ${money(total)}，仍超出 ${money(gap)}。之前列出的替代项仍成立，可以继续换便宜的同类，或再调一次预算。`,
      adopted: true,
      stillOver: true,
    };
  }
  return {
    tone: 'ok',
    text: `预算已改为 ${money(newBudget)}：当前 ${money(total)}，不再超支；之前列出的替代项不用再执行。`,
    adopted: true,
    stillOver: false,
  };
}

// ================= 多步替代叠加 =================

export const MAX_COMBINED_OPTIONS = 3;

/**
 * 把 2~3 项候选合并成一句明确的增量，一次重排完成。
 * "按预算重排"本身就是整单压缩，不参与叠加；少于 2 项时不走合并（返回空串）。
 */
export function buildCombinedInstruction(options: OverageOption[]): string {
  const picked = (Array.isArray(options) ? options : [])
    .filter((option) => option.kind !== 'budget')
    .slice(0, MAX_COMBINED_OPTIONS);
  if (picked.length < 2) return '';
  const actions = picked.map((option) =>
    option.kind === 'lodging'
      ? '住宿换成更实惠的同类'
      : `把「${String(option.target || option.label).trim()}」换成更便宜的同类`,
  );
  return `同时调整 ${actions.length} 处：${actions.join('；')}。其它安排尽量保持原样`;
}

const nodeNames = (nodes: ScopedRouteNode[]) =>
  (Array.isArray(nodes) ? nodes : [])
    .map((node) => String(node?.name || node?.location || '').trim())
    .filter(Boolean);

function lodgingNameOf(nodes: ScopedRouteNode[]): string | null {
  const found = (Array.isArray(nodes) ? nodes : []).find((node) => nodeCategory(node) === 'lodging');
  return found ? String(found.name || found.location || '').trim() || null : null;
}

function pricedTotal(nodes: ScopedRouteNode[]): number | null {
  const priced = pricedNodes(nodes);
  if (priced.length === 0) return null;
  return priced.reduce((sum, item) => sum + item.cost, 0);
}

export interface CombinedCheck {
  optionId: string;
  label: string;
  applied: boolean;
  note: string;
}

export interface CombinedSummary {
  tone: 'ok' | 'warn';
  text: string;
  checks: CombinedCheck[];
  /** 已计价节点合计的变化：正数表示省了，负数表示反而多了。拿不到两边合计时为 null。 */
  saved: number | null;
}

/**
 * 逐项核对合并重排是否真的落实：节点是否被换掉、住宿是否换了那家，
 * 以及已计价节点的合计变化。只看能对上名字的节点，认不出来就如实说无法核对。
 */
export function verifyCombinedOptions(input: {
  options: OverageOption[];
  before: ScopedRouteNode[];
  after: ScopedRouteNode[];
}): { checks: CombinedCheck[]; saved: number | null } {
  const options = (Array.isArray(input.options) ? input.options : []).filter((option) => option.kind !== 'budget');
  const beforeNames = new Set(nodeNames(input.before));
  const afterNames = new Set(nodeNames(input.after));
  const beforeLodging = lodgingNameOf(input.before);
  const afterLodging = lodgingNameOf(input.after);

  const checks: CombinedCheck[] = options.map((option) => {
    if (option.kind === 'lodging') {
      const label = '住宿';
      if (!beforeLodging) return { optionId: option.id, label, applied: false, note: '调整前没找到住宿，无法核对' };
      if (!afterLodging) return { optionId: option.id, label, applied: false, note: '新方案里没有住宿' };
      const changed = afterLodging !== beforeLodging;
      return { optionId: option.id, label, applied: changed, note: changed ? '已更换' : '没有变化，仍住原来那家' };
    }
    const target = String(option.target || '').trim();
    const label = `「${target || option.label}」`;
    if (!target || !beforeNames.has(target)) {
      return { optionId: option.id, label, applied: false, note: '调整前没有找到这个节点，无法核对' };
    }
    const stillThere = afterNames.has(target);
    return { optionId: option.id, label, applied: !stillThere, note: stillThere ? '没有变化，仍在行程里' : '已更换' };
  });

  const beforeTotal = pricedTotal(input.before);
  const afterTotal = pricedTotal(input.after);
  const saved = beforeTotal != null && afterTotal != null ? beforeTotal - afterTotal : null;
  return { checks, saved };
}

/** 合并重排回来的核对：逐项说落实没落实 + 省钱多少 + 是否压进预算，没新方案就如实说。 */
export function summarizeCombinedOverage(input: {
  options: OverageOption[];
  before: ScopedRouteNode[];
  after: ScopedRouteNode[] | null;
  verifiedCost: number | null;
  estimatedCost: number | null;
  budget: number | null;
  shortfall: number | null;
}): CombinedSummary {
  if (!input.after) {
    return {
      tone: 'warn',
      text: '合并重排没有拿到新方案，无法确认这几项是否落实；可以再选一次，或改预算。',
      checks: [],
      saved: null,
    };
  }
  const { checks, saved } = verifyCombinedOptions({ options: input.options, before: input.before, after: input.after });
  const verdict = summarizeOverageResult({
    verifiedCost: input.verifiedCost,
    estimatedCost: input.estimatedCost,
    budget: input.budget,
    shortfall: input.shortfall,
  });
  const parts = checks.map((check) => `${check.label}：${check.note}`);
  if (saved !== null) {
    parts.push(
      saved > 0
        ? `已计价节点合计省了 ${money(saved)}`
        : saved < 0
          ? `已计价节点合计反而多了 ${money(-saved)}`
          : '已计价节点合计没有变',
    );
  }
  const allApplied = checks.every((check) => check.applied);
  return {
    tone: allApplied && verdict.tone === 'ok' ? 'ok' : 'warn',
    text: `${parts.join('；')}。${verdict.text}`,
    checks,
    saved,
  };
}

/**
 * 合并后没能落实的项：只挑这些出来重试。
 * 已经换好的项不再带上，避免第二次重排把已经达成的结果又动回去。
 * 认不出（没有对应核对记录）的项不参与重试。
 */
export function unappliedOptions(summary: CombinedSummary | null, options: OverageOption[]): OverageOption[] {
  if (!summary || !Array.isArray(summary.checks)) return [];
  return (Array.isArray(options) ? options : []).filter((option) => {
    const check = summary.checks.find((item) => item.optionId === option.id);
    return Boolean(check) && !check!.applied;
  });
}

/**
 * 重试指令：只剩 1 项时用它自己的单步指令，2 项以上仍走合并。
 * 一条都挑不出来时返回空串（不重发无意义的增量）。
 */
export function buildRetryInstruction(options: OverageOption[]): string {
  const picked = (Array.isArray(options) ? options : [])
    .filter((option) => option.kind !== 'budget')
    .slice(0, MAX_COMBINED_OPTIONS);
  if (picked.length === 0) return '';
  if (picked.length === 1) return picked[0].instruction;
  return buildCombinedInstruction(picked);
}
