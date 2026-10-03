/**
 * 旅中风险告警 / 最小扰动重规划的**纯解析层**（可离线测试，不碰网络与 React）。
 *
 * 数据来源（两段链路）：
 *  1. gateway 的房间广播 `risk_subscription_alert`：
 *     `{ city, snapshot, changes: [...], minimum_perturbation?: {...} }`
 *  2. `minimum_perturbation` 是 ai-service `/api/v1/risk/replan-scope` 的原样回包：
 *     `{ correlation_id, status, proposal: {status, candidates:[{action, affected_nodes,
 *       preserved_nodes, preserved_ratio, requires_user_confirmation, risk}], failure?}, events, note }`
 *     失败时是 gateway 如实标注的对象：`{status: unavailable|rejected|unparsable|needs_user_context, reason?}`。
 *
 * 诚实口径（本文件存在的理由）：
 *  - 算不出来就写"没算出来 + 为什么"，**绝不**编出一份"影响 3 个节点"的假范围；
 *  - 保留率低于 70% 必须点明"这不是最小扰动"，不能含糊成"已为你优化"；
 *  - 未知的动作码原样露出，不硬翻成人话骗人；
 *  - 确认前不改方案这件事必须写在文案里（和"用户确认前不自动覆盖原方案"的验收口径对齐）。
 */

export type PerturbationView = {
  status: 'ready' | 'degraded' | 'unavailable';
  headline: string;
  actionLabel?: string;
  actionCode?: string;
  affected: string[];
  preservedCount?: number;
  preservedRatio?: number;
  /** 保留率是否满足"最小扰动"口径（≥70%） */
  keepsEnough?: boolean;
  needsConfirmation: boolean;
  disclosure: string;
  reason?: string;
  correlationId?: string;
  riskLabel?: string;
};

export type RiskAlertView = {
  city: string;
  changes: string[];
  perturbation: PerturbationView | null;
};

/** 最低保留率口径：未受影响节点保留率 ≥ 70% 才算"最小扰动"。低于它就必须说实话。 */
export const MIN_PRESERVED_RATIO = 0.7;

const ACTION_LABELS: Record<string, string> = {
  replace_affected_nodes: '只在受影响的那几个节点里替换，其余行程不动',
  replace_outdoor_activity: '把受影响的户外活动换成室内同类安排',
  shift_day: '把受影响这天的安排整体顺延或挪动',
  reorder_day: '只重排当天的顺序',
  no_action: '不需要改动',
};

const SEVERITY_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  moderate: '中',
  high: '高',
  severe: '严重',
  extreme: '极高',
};

const FAILURE_LABELS: Record<string, string> = {
  unavailable: '这次没算出最小扰动范围',
  rejected: '重规划服务拒绝了这次请求',
  unparsable: '重规划服务返回的内容看不懂',
  needs_user_context: '缺少必要上下文，无法判断影响范围',
};

const REASON_LABELS: Record<string, string> = {
  ai_service_unreachable: '重规划服务没连上',
  request_build_failed: '请求没能拼出来（服务内部错误）',
  payload_encode_failed: '请求没能拼出来（服务内部错误）',
  trip_not_found: '还没找到这个城市的行程',
  scope_unknown: '风险事件没给出影响范围（哪几天/哪些节点）',
  empty_route: '当前没有可用的路线',
};

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => text(item)).filter(Boolean);
}

/** 一条风险变化的人话：优先用后端给的说明，其次按类型/严重度拼，都没有就如实说"未提供说明"。 */
export function describeRiskChange(change: unknown): string {
  if (typeof change === 'string') return change.trim();
  if (!change || typeof change !== 'object') return '';
  const row = change as Record<string, unknown>;
  const explicit = text(row.summary) || text(row.description) || text(row.detail) || text(row.message) || text(row.title);
  if (explicit) return explicit;
  const type = text(row.type) || text(row.event) || text(row.code);
  const severityRaw = text(row.severity) || text(row.level);
  const severity = severityRaw ? (SEVERITY_LABELS[severityRaw.toLowerCase()] || severityRaw) : '';
  const days = asStringList(row.affected_days);
  const nodes = asStringList(row.affected_nodes);
  const parts: string[] = [];
  if (type) parts.push(type);
  if (severity) parts.push(`严重度 ${severity}`);
  if (days.length) parts.push(`影响第 ${days.join('、')} 天`);
  if (nodes.length) parts.push(`点名节点 ${nodes.join('、')}`);
  return parts.length ? parts.join(' · ') : '风险发生变化（后端未提供说明）';
}

function unavailableView(raw: Record<string, unknown>): PerturbationView {
  const statusKey = text(raw.status) || 'unavailable';
  const reasonKey = text(raw.reason);
  const reason = reasonKey ? (REASON_LABELS[reasonKey] || reasonKey) : '';
  const httpStatus = raw.http_status;
  let headline = FAILURE_LABELS[statusKey] || '重规划范围没算出来';
  if (statusKey === 'rejected' && httpStatus != null) headline = `重规划服务拒绝了这次请求（HTTP ${httpStatus}）`;
  else if (reason) headline = `${headline}：${reason}`;
  return {
    status: 'unavailable',
    headline,
    affected: [],
    needsConfirmation: true,
    disclosure: '这次没有算出"最小扰动范围"，所以不会改动你现在的方案；可以稍后重试，或直接手动调整。',
    reason: reason || undefined,
    correlationId: text(raw.correlation_id) || undefined,
  };
}

/**
 * 把 `minimum_perturbation` 解析成可直接渲染的视图。
 * 任何缺字段的情况都走"没算出来"分支，而不是补 0 或补空数组冒充结果。
 */
export function describePerturbation(raw: unknown): PerturbationView {
  if (!raw || typeof raw !== 'object') {
    return {
      status: 'unavailable',
      headline: '这次没有拿到重规划范围',
      affected: [],
      needsConfirmation: true,
      disclosure: '没有收到重规划范围的回包，因此不会改动你现在的方案。',
    };
  }
  const payload = raw as Record<string, unknown>;
  const correlationId = text(payload.correlation_id) || undefined;
  const statusKey = text(payload.status);

  // gateway 的如实失败标注：unavailable / rejected / unparsable / needs_user_context
  if (['unavailable', 'rejected', 'unparsable', 'needs_user_context'].includes(statusKey)) {
    return unavailableView(payload);
  }

  const proposal = (payload.proposal || {}) as Record<string, unknown>;
  // 没有 proposal 字段说明这根本不是一次正常的范围回包（空对象、数组、乱码…）——
  // 归到"没拿到范围"，而不是说成"服务明确拒绝了"（那是在替服务编理由）。
  if (!payload.proposal || typeof payload.proposal !== 'object') {
    return {
      status: 'unavailable',
      headline: '这次没有拿到重规划范围',
      affected: [],
      needsConfirmation: true,
      disclosure: '没有收到重规划范围的正常回包，因此不会改动你现在的方案。',
      correlationId,
    };
  }
  const candidates = Array.isArray(proposal.candidates) ? proposal.candidates : [];
  const candidate = (candidates[0] || null) as Record<string, unknown> | null;
  const failure = (proposal.failure || null) as Record<string, unknown> | null;

  if (proposal.status === 'degraded' || !candidate) {
    const code = failure ? text(failure.code) : '';
    const message = failure ? text(failure.message) : '';
    return {
      status: 'degraded',
      headline: message || FAILURE_LABELS[code] || '这次拒绝给出重规划范围',
      affected: [],
      needsConfirmation: true,
      disclosure: '范围不可判定时不会整条路线重生成，也不会改动你现在的方案，交给你确认。',
      reason: code ? REASON_LABELS[code] || code : undefined,
      correlationId,
    };
  }

  const actionCode = text(candidate.action);
  const affected = asStringList(candidate.affected_nodes);
  const preserved = asStringList(candidate.preserved_nodes);
  const ratioRaw = candidate.preserved_ratio;
  const preservedRatio = typeof ratioRaw === 'number' && Number.isFinite(ratioRaw) ? ratioRaw : undefined;
  const keepsEnough = preservedRatio === undefined ? undefined : preservedRatio >= MIN_PRESERVED_RATIO;
  const risk = (candidate.risk || {}) as Record<string, unknown>;
  const severityRaw = text(risk.severity);
  const riskLabel = [text(risk.event), severityRaw ? `严重度 ${SEVERITY_LABELS[severityRaw.toLowerCase()] || severityRaw}` : '']
    .filter(Boolean)
    .join(' · ');

  const ratioText = preservedRatio === undefined ? '保留率未给出' : `未受影响节点保留率 ${Math.round(preservedRatio * 100)}%`;
  const headline = keepsEnough === false
    ? `${ratioText}（低于 ${Math.round(MIN_PRESERVED_RATIO * 100)}%，属于大改，不是最小扰动）`
    : ratioText;

  return {
    status: 'ready',
    headline,
    actionLabel: actionCode ? ACTION_LABELS[actionCode] || `动作：${actionCode}（未收录的动作码）` : undefined,
    actionCode: actionCode || undefined,
    affected,
    preservedCount: preserved.length || undefined,
    preservedRatio,
    keepsEnough,
    needsConfirmation: candidate.requires_user_confirmation !== false,
    disclosure: keepsEnough === false
      ? '这次改动幅度不小（保留率低于口径），确认前不会覆盖你现在的方案；建议先看看替代安排再决定。'
      : '只会动受影响的那部分，其余安排保留；确认前不会覆盖你现在的方案。',
    correlationId,
    riskLabel: riskLabel || undefined,
  };
}

/** 广播 payload → 视图。没有 `minimum_perturbation` 时不渲染该区块（而不是渲染一个空壳）。 */
export function normalizeRiskAlert(payload: unknown): RiskAlertView | null {
  if (!payload || typeof payload !== 'object') return null;
  const row = payload as Record<string, unknown>;
  const changes = (Array.isArray(row.changes) ? row.changes : []).map(describeRiskChange).filter(Boolean);
  const city = text(row.city);
  if (!city && changes.length === 0 && !row.minimum_perturbation) return null;
  return {
    city,
    changes,
    perturbation: row.minimum_perturbation ? describePerturbation(row.minimum_perturbation) : null,
  };
}
