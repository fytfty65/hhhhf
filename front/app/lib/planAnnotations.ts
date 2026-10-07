// 方案**台账**→ 行程上的可见标记（纯函数）。
//
// 为什么需要（2026-10-07，用户口径"没渲染出来的'已做'等于没做"）：
//   后端这几轮为了让方案"吃得着、住得近"，做了不少**结构性改动**，而且都留了台账：
//     · `meal_topup`         某天缺餐 → 补/挪了一个饭点（带距离）；或**宁可不塞**（带原因）
//     · `lodging_geo_adjust` 住得太远 → 换了一家（从 46.9km 换到 2.1km）
//     · `meal_geo_adjust`    饭点排太远 → 换了一家（从 31.5km 换到 1.9km）
//     · `day_rebalance`      按地理就近重排了分天（换天）
//   这些只落在 JSON 台账/遗留问题里，行程正文上看不出来 ——
//   于是"第 3 天没有晚餐"和"酒店被换了"这两件用户最该知道的事，在正文里是隐形的。

export interface NodeAnnotation {
  kind: 'meal_swap' | 'lodging_swap' | 'meal_added' | 'meal_moved' | 'day_rebalanced';
  /** 展示文案（一句话，含"从多远换到多远"这类量化信息） */
  text: string;
}

export interface DayNotice {
  kind: 'meal_missing' | 'meal_added' | 'day_rebalanced';
  text: string;
}

function asArray(value: any): any[] {
  return Array.isArray(value) ? value : [];
}

function km(value: any): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${number}km` : '';
}

/** 这个节点身上发生过什么（换过酒店/餐馆、被补进来、被挪过来、换过天） */
export function nodeAnnotations(plan: any, nodeName: unknown): NodeAnnotation[] {
  const name = String(nodeName ?? '').trim();
  if (!plan || !name) return [];
  const out: NodeAnnotation[] = [];

  for (const action of asArray(plan?.lodging_geo_adjust?.actions)) {
    if (String(action?.to ?? '').trim() !== name) continue;
    out.push({
      kind: 'lodging_swap',
      text: `住宿按"离当天玩点最近"换成了这家：${action?.from ?? '原酒店'}（${km(action?.from_km)}）→ 这里（${km(action?.to_km)}）`,
    });
  }
  for (const action of asArray(plan?.meal_geo_adjust?.actions)) {
    if (String(action?.to ?? '').trim() !== name) continue;
    out.push({
      kind: 'meal_swap',
      text: `餐饮按"离当天玩点最近"换成了这家：${action?.from ?? '原餐馆'}（${km(action?.from_km)}）→ 这里（${km(action?.to_km)}）`,
    });
  }
  for (const action of asArray(plan?.meal_topup?.actions)) {
    if (String(action?.node ?? '').trim() !== name) continue;
    const code = String(action?.code ?? '');
    const distance = km(action?.distance_km);
    if (code === 'add_meal') {
      out.push({ kind: 'meal_added', text: `这天原本没有餐饮，从候选里补了这家${distance ? `（距当天玩点 ${distance}）` : ''}` });
    } else {
      out.push({
        kind: 'meal_moved',
        text: `这天原本没有餐饮，从第 ${action?.from_day ?? '?'} 天挪来了这家${distance ? `（距当天玩点 ${distance}）` : ''}`,
      });
    }
  }
  const moved = asArray(plan?.day_rebalance?.moved).map((item) => String(item ?? '').trim());
  if (plan?.day_rebalance?.applied && moved.includes(name)) {
    out.push({
      kind: 'day_rebalanced',
      text: `按地理就近重排换到了这一天（最赶的一天 ${plan.day_rebalance.before_max_km ?? '?'}km → ${plan.day_rebalance.after_max_km ?? '?'}km）`,
    });
  }
  return out;
}

/** 按天要给用户看的提醒（重点是"这天没有饭点"这种硬缺口） */
export function dayNotices(plan: any): Record<number, DayNotice[]> {
  const out: Record<number, DayNotice[]> = {};
  if (!plan) return out;
  const push = (day: any, notice: DayNotice) => {
    const key = Number(day);
    if (!Number.isInteger(key) || key <= 0) return;
    out[key] = [...(out[key] || []), notice];
  };

  // ① 缺餐：后端算过但**宁可不塞**（最近候选太远）→ 正文必须说，否则用户以为我们忘了
  for (const item of asArray(plan?.meal_topup?.skipped)) {
    const reason = String(item?.reason ?? '').trim();
    push(item?.day, {
      kind: 'meal_missing',
      text: `这天没有安排餐饮：${reason || '周边没有合适的餐馆'}${reason.includes('说一声') ? '' : '（想吃的话说一声就补）'}`,
    });
  }
  // ② 补/挪进来的饭点：让用户知道这天原来缺
  for (const action of asArray(plan?.meal_topup?.actions)) {
    const code = String(action?.code ?? '');
    const node = String(action?.node ?? '');
    if (code === 'add_meal') {
      push(action?.day, { kind: 'meal_added', text: `原本没有餐饮，已补：${node}` });
    } else if (code === 'move_meal') {
      push(action?.day, { kind: 'meal_added', text: `原本没有餐饮，已从第 ${action?.from_day ?? '?'} 天挪来：${node}` });
    }
  }
  // ③ 地理重排动了分天：整条行程级提醒，挂在已经有提醒的那几天上（避免刷屏）
  if (plan?.day_rebalance?.applied) {
    const before = Number(plan?.day_rebalance?.before_total_km ?? plan?.day_rebalance?.before_max_km);
    const after = Number(plan?.day_rebalance?.after_total_km ?? plan?.day_rebalance?.after_max_km);
    const text = `分天按地理就近重排过（${Number.isFinite(before) && Number.isFinite(after) ? `最赶或全程约 ${before}km → ${after}km` : '里程更省'}）`;
    for (const key of Object.keys(out)) push(key, { kind: 'day_rebalanced', text });
  }
  return out;
}

/** 官方登记资料（文旅部名录）：简介/地址/官网/预约入口 —— 用于"地点资料" */
export function officialDetailFor(
  plan: any,
  nodeName: unknown,
): { name: string; intro: string; address: string; site: string; booking: string } | null {
  const profiles = plan && typeof plan === 'object' ? plan.official_profiles : null;
  if (!profiles || typeof profiles !== 'object') return null;
  const entry = (profiles as Record<string, any>)[String(nodeName ?? '').trim()];
  if (!entry || typeof entry !== 'object') return null;
  const detail = {
    name: String(entry.official_name ?? '').trim(),
    intro: String(entry.official_intro ?? '').trim(),
    address: String(entry.official_address ?? '').trim(),
    site: String(entry.official_site ?? '').trim(),
    booking: String(entry.official_booking ?? '').trim(),
  };
  return detail.name || detail.intro || detail.address || detail.site || detail.booking ? detail : null;
}
