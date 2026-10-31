'use client';

import { useState } from 'react';
import type { PlanGovernance } from '../../components/PlanGovernancePanel';

// 规划结果 Store：一次推演产出的所有「结果类」状态。
// WebSocket 流式消息只负责往里写，展示组件按域读取，避免页面组件同时
// 持有路线、预算、治理、公平性、仿真、策略臂等二十多个互不相干的 useState。
export function usePlanResultStore() {
  // 当前展示的路线节点（主方案或用户切换后的方案）
  const [dynamicRoutes, setDynamicRoutes] = useState<any[]>([]);
  // 方案台账：缺餐补餐 / 换住宿 / 分天重排 / 官方档案等结构性调整记录
  const [planLedgers, setPlanLedgers] = useState<any>({});
  // 多方案：主方案 + 后端差异化方案，供用户切换
  const [planVariants, setPlanVariants] = useState<any[]>([]);
  const [activePlanId, setActivePlanId] = useState<string>('primary');
  const [planSwitchNotice, setPlanSwitchNotice] = useState('');
  // 本次规划使用的 bandit 臂与选择理由：满意度回传要靠它归因
  const [banditArmId, setBanditArmId] = useState<string>('');
  const [banditDecision, setBanditDecision] = useState<{ reason?: string; explored?: boolean; propensity?: number } | null>(null);
  // 目的地是「认出来的」还是「兜底默认的」，必须展示，否则用户会被悄悄换城市
  const [targetCityInfo, setTargetCityInfo] = useState<{ name: string; lnglat: [number, number]; resolution?: string } | null>(null);
  const [budgetData, setBudgetData] = useState<any>(null);
  // 团队满意度：唯一来源是后端按真实路线算出的 team_satisfaction
  const [teamSatisfaction, setTeamSatisfaction] = useState<Record<string, number>>({});
  const [fairnessAudit, setFairnessAudit] = useState<any>(null);
  const [memberNegotiation, setMemberNegotiation] = useState<any>(null);
  const [agentContractAudits, setAgentContractAudits] = useState<any[]>([]);
  const [specialistRuntime, setSpecialistRuntime] = useState<any | null>(null);
  const [optimizationAudit, setOptimizationAudit] = useState<any | null>(null);
  // 概率化数字孪生：P50/P90 时长、超预算概率、成员满意度分布
  const [simulation, setSimulation] = useState<{
    total_minutes?: { p50?: number; p90?: number; mean?: number };
    cost?: { known_p50?: number; known_p90?: number; complete_probability?: number };
    budget_overrun_probability?: number | null;
    per_day_minutes_p90?: Record<string, number>;
    member_satisfaction?: Record<string, { satisfaction_p50?: number; satisfaction_floor_p10?: number }>;
    assumptions?: { unpriced_nodes?: number; defaulted_dwell?: number; defaulted_travel?: number; sample_count?: number };
  } | null>(null);
  const [arbitrationRecords, setArbitrationRecords] = useState<string[]>([
    '针对【餐饮与预算诉求】: 精选特色老字号正餐，全程无重复排布',
    '针对【节奏分歧】: 午后采取分合流调度，傍晚在统一地点汇合用餐',
  ]);
  const [consensusSummary, setConsensusSummary] = useState('');
  const [isFallbackRoute, setIsFallbackRoute] = useState(false);
  // 规划核对信息（预算三级模型 / 兜底调整说明 / 超长行程建议 / 门禁问题）
  const [planGovernance, setPlanGovernance] = useState<PlanGovernance | null>(null);
  // 本次规划的关联键：让网关侧事件与 ai-service 推演事件归到同一条轨迹
  const [planCorrelationId, setPlanCorrelationId] = useState('');

  return {
    dynamicRoutes, setDynamicRoutes,
    planLedgers, setPlanLedgers,
    planVariants, setPlanVariants,
    activePlanId, setActivePlanId,
    planSwitchNotice, setPlanSwitchNotice,
    banditArmId, setBanditArmId,
    banditDecision, setBanditDecision,
    targetCityInfo, setTargetCityInfo,
    budgetData, setBudgetData,
    teamSatisfaction, setTeamSatisfaction,
    fairnessAudit, setFairnessAudit,
    memberNegotiation, setMemberNegotiation,
    agentContractAudits, setAgentContractAudits,
    specialistRuntime, setSpecialistRuntime,
    optimizationAudit, setOptimizationAudit,
    simulation, setSimulation,
    arbitrationRecords, setArbitrationRecords,
    consensusSummary, setConsensusSummary,
    isFallbackRoute, setIsFallbackRoute,
    planGovernance, setPlanGovernance,
    planCorrelationId, setPlanCorrelationId,
  };
}
