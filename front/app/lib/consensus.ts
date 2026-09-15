export interface ConsensusMemberPreference {
  id: string;
  name?: string;
  budgetWeight?: number;
  paceWeight?: number;
  riskWeight?: number;
  interestTags?: string[];
}

export interface ConsensusRouteNode {
  name?: string;
  location?: string;
  tags?: string[];
  cost?: string | number;
  cost_estimate?: string | number;
  time?: string;
  riskScore?: number;
  risk_score?: number;
}

export interface ConsensusDimension {
  score: number;
  reason: string;
}

export interface ConsensusScore {
  overall: number;
  dimensions: {
    budget: ConsensusDimension;
    pace: ConsensusDimension;
    risk: ConsensusDimension;
    interest: ConsensusDimension;
  };
  reasons: string[];
}

export interface ConsensusConstraints {
  /** Per-person budget ceiling used by the what-if simulator. */
  budgetLimit?: number;
  /** Preferred maximum number of nodes per day. */
  paceLimit?: number;
  /** Maximum tolerated average risk score. */
  riskTolerance?: number;
}

function numericCost(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const match = String(value ?? '').replace(/,/g, '').match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}

function clamp(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function average(values: number[], fallback = 50) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : fallback;
}

export function scoreRouteForConsensus(
  route: ConsensusRouteNode[],
  members: ConsensusMemberPreference[] = [],
  constraints: ConsensusConstraints = {},
): ConsensusScore {
  const nodes = Array.isArray(route) ? route : [];
  const prefs = Array.isArray(members) ? members : [];
  const totalCost = nodes.reduce((sum, node) => sum + numericCost(node.cost ?? node.cost_estimate), 0);
  const budgetTarget = constraints.budgetLimit ?? Math.max(1, prefs.length || 1) * 180;
  const budgetScore = clamp(100 - Math.max(0, totalCost - budgetTarget) / budgetTarget * 100);
  const paceLimit = constraints.paceLimit ?? 3;
  const paceScore = clamp(100 - Math.max(0, nodes.length - paceLimit) * 12);
  const riskValues = nodes.map((node) => Number(node.riskScore ?? node.risk_score)).filter(Number.isFinite);
  const riskTolerance = constraints.riskTolerance ?? 10;
  const riskScore = clamp(100 - Math.max(0, average(riskValues, 10) - riskTolerance) * 2);
  const wantedTags = new Set(prefs.flatMap((member) => member.interestTags || []).map((tag) => tag.toLowerCase()));
  const matchedTags = nodes.flatMap((node) => node.tags || []).filter((tag) => wantedTags.has(String(tag).toLowerCase()));
  const interestScore = wantedTags.size ? clamp(matchedTags.length / Math.max(1, nodes.length) * 100) : 60;

  const weights = {
    budget: average(prefs.map((member) => member.budgetWeight ?? 1)),
    pace: average(prefs.map((member) => member.paceWeight ?? 1)),
    risk: average(prefs.map((member) => member.riskWeight ?? 1)),
    interest: 1,
  };
  const totalWeight = Object.values(weights).reduce((sum, value) => sum + value, 0);
  const overall = clamp((budgetScore * weights.budget + paceScore * weights.pace + riskScore * weights.risk + interestScore) / totalWeight);

  const dimensions = {
    budget: { score: budgetScore, reason: totalCost ? `预计人均花费约 ¥${Math.round(totalCost)}，预算匹配度 ${budgetScore}%` : '尚未拿到完整价格，预算分采用中性估计' },
    pace: { score: paceScore, reason: `每日节点数量为 ${nodes.length} 个，节奏匹配度 ${paceScore}%` },
    risk: { score: riskScore, reason: riskValues.length ? `已纳入 ${riskValues.length} 个节点风险信号` : '节点暂未提供风险信号，按低风险中性值计算' },
    interest: { score: interestScore, reason: wantedTags.size ? `命中 ${matchedTags.length} 个成员兴趣标签` : '成员兴趣标签尚未同步，暂使用中性分' },
  };
  const reasons = [
    dimensions.budget.reason,
    dimensions.pace.reason,
    dimensions.risk.reason,
    dimensions.interest.reason,
  ];
  return { overall, dimensions, reasons };
}
