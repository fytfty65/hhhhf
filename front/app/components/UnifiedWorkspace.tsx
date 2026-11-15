'use client';

import dynamic from 'next/dynamic';
import { useState, useRef, useEffect, useMemo } from 'react';
import {
  Camera, PiggyBank, Check,
  TrendingDown, RefreshCw,
  X, BrainCircuit, Wand2, ExternalLink,
  ChevronDown, ChevronRight,
  Save, Shuffle,
  Star, BookOpen,
  Share2, Download, MoreHorizontal,
  AlertTriangle, Users
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { WS_BASE, apiFetch, apiJson, getAuthToken, fetchWsTicket } from '../lib/utils';
import { tryExtractJson, normalizeLnglat, extractStreamingRoutes } from '../lib/lobbyUtils';
import { voteCountsByNode, type RoomVoteState } from '../lib/collaborationVotes';
import { officialFieldsFor } from '../lib/priceProvenance';
import { trackPlanningEvent } from '../lib/planningEvents';
import { usePlanResultStore } from '../lib/stores/planResult';
import { useTripExecutionStore } from '../lib/stores/tripExecution';
import { readWorkspaceSnapshot, writeWorkspaceSnapshot } from '../lib/workspaceSnapshot';
import { useFocusTrap } from '../lib/useFocusTrap';
import type { Phase, RoomMember, UserProfile } from '../types';

// 底部行程节点横向卡片条（自 ContextualLobby 拆分出来的展示型组件）
import BottomMapCarousel from './BottomMapCarousel';

// 多智能体推演进行中/失败面板（自 ContextualLobby 拆分出来的展示型组件）
import DeductionPanel from './DeductionPanel';
import RoomConnectionStatus, { type RoomConnectionState } from './RoomConnectionStatus';

// 诉求打磨面板（自 ContextualLobby 拆分出来；TRAVEL_MODES/USER_ROLES 随它搬走）
import DraftingPanel from './DraftingPanel';

// 马蜂窝风行程面板（自 ContextualLobby 拆分出来的展示型组件）
import MafengwoStylePanel from './MafengwoStylePanel';
import SpecialistInsightsPanel from './SpecialistInsightsPanel';
// 房主建议箱：聚合成员在节点上提的修改建议
import RoomSuggestionInbox from './RoomSuggestionInbox';
import type { RoomComment } from '../lib/stores/roomCollaboration';

// 预算核对 / 已作调整 / 行程规模 说明面板（字段来自 final_route.payload）。
// 按需加载：只在决策阶段出现，没必要进首屏包（实测直接 import 会让首屏 JS +6.5KB）。
const PlanGovernancePanel = dynamic(() => import('./PlanGovernancePanel'), { ssr: false });
import NegotiationDiagnostics from './NegotiationDiagnostics';
import PlanFactsPanel from './PlanFactsPanel';

// 一键分享行程海报
import ItineraryPoster from './ItineraryPoster';

// 旅中风险推送中心：轮询实时风险快照，检测天气/路况/安全事件变更并推送
import RiskPushCenter from './RiskPushCenter';

// 碳足迹与绿色交通评估（P5）
import CarbonFootprintPanel from './CarbonFootprintPanel';
import TripDiaryModal from './TripDiaryModal';
import BudgetPanel from './BudgetPanel';
import ExpenseReportModal from './ExpenseReportModal';
import SatisfactionModal from './SatisfactionModal';
import { buildIcsCalendar, buildDiaryMarkdown } from '../lib/tripExport';
import SecondaryPlanner from './SecondaryPlanner';
import PreTripPlanningPanel from './PreTripPlanningPanel';
import FieldAssistPanel from './FieldAssistPanel';
import BookingHub from './BookingHub';
import TripExecutionPanel from './TripExecutionPanel';
import ReplanProposalPanel from './ReplanProposalPanel';
import RiskPerturbationPanel from './RiskPerturbationPanel';
import ReferenceRoutePanel from './ReferenceRoutePanel';
// 只重算某一天 / 只替换交通住宿餐饮
import ScopedAdjustPanel from './ScopedAdjustPanel';
import { buildScopedInstruction, summarizeScopedChange } from '../lib/scopedReplan';
import type { ScopedReport, ScopedRouteNode, ScopedScope } from '../lib/scopedReplan';
// 超支替代方案：超出预算时给出可执行的替代组合，并在重排后核对是否真的压进预算
import OverageAlternativesPanel from './OverageAlternativesPanel';
import { buildRetryInstruction, summarizeBudgetChange, summarizeCombinedOverage, summarizeOverageResult, unappliedOptions } from '../lib/overageAlternatives';
import type { BudgetChangeSummary, CombinedSummary, OverageOption, OverageResult } from '../lib/overageAlternatives';
import { normalizeRiskAlert } from '../lib/riskPerturbation';
// 普通模式首屏的「今日概览」：只回答今天去哪、多少钱、路上多久、有没有风险。
import TripOverviewCard, { type BudgetBreakdown } from './TripOverviewCard';

type WorkspaceLayer = 'plan' | 'evidence' | 'execution' | 'professional';
type ExperienceMode = 'simple' | 'pro';
// 地图抢不抢主体，取决于用户当下在做什么：默认文档优先，需要看空间关系时再切到平衡/地图。
type MapLayout = 'doc' | 'balanced' | 'map';
const MAP_LAYOUT_KEY = 'omni_map_layout';
const MAP_LAYOUT_LABELS: Record<MapLayout, string> = {
  doc: '文档优先',
  balanced: '平衡',
  map: '地图优先',
};

// 普通模式只保留"路线、预算、交通、风险、保存"；治理、模型评估、供应商质量
// 这类专业信息收进专业模式的高级抽屉，避免一屏术语把普通用户挡在门外。
const EXPERIENCE_MODE_KEY = 'omni_experience_mode';

// 后端金额字段可能是数字、带符号字符串或缺失：缺失返回 null，不把空值当成 0 金额。
const numberOrNull = (value: any) => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

// 全域路由调度器（3D 地图 + WebGL 重型依赖，按需懒加载避免首屏同步解析卡死）
const FullRouteVisualizer = dynamic(
  () => import('../FullRouteVisualizer'),
  {
    ssr: false,
    loading: () => (
      <div className="relative w-full h-full bg-slate-100 flex flex-col items-center justify-center gap-3">
        <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
        <span className="text-sm font-bold text-slate-400 tracking-wider">全域路由调度器装载中...</span>
      </div>
    )
  }
);

// ================= 工作区区域 =================
export default function UnifiedWorkspace({ mode, role, roomCode, roomMembers, currentUser, initialDestination, initialIntent, onBack, roomVotes, onRoomVote, roomConnection = 'idle', roomRetryAttempt = 0, roomRetryDelayMs = 0, onRetryRoom, isHost = true, followHost = true, onFollowHostChange, onBroadcastPlan, roomComments = [], onNodeComment, onAckNodeComment }: { mode: string, role: string, roomCode: string, roomMembers: RoomMember[], currentUser: UserProfile, initialDestination?: string | null, initialIntent?: string | null, onBack: () => void, roomVotes: RoomVoteState, onRoomVote: (nodeName: string, vote: 'up' | 'down' | null) => void, roomConnection?: RoomConnectionState, roomRetryAttempt?: number, roomRetryDelayMs?: number, onRetryRoom?: () => void, isHost?: boolean, followHost?: boolean, onFollowHostChange?: (next: boolean) => void, onBroadcastPlan?: (payload: any) => void, roomComments?: RoomComment[], onNodeComment?: (nodeName: string, day: number | undefined, text: string) => boolean, onAckNodeComment?: (id: string, status: 'adopted' | 'dismissed') => void }) {
  // 刷新恢复：上一轮已经出过方案时，直接用快照进入决策页，不再退回诉求页重来。
  // 快照只在本标签页内有效，且目的地对不上时由 readWorkspaceSnapshot 丢弃。
  const [restoredSnapshot] = useState(() =>
    readWorkspaceSnapshot(currentUser?.id, roomCode, initialDestination ?? null),
  );
  const [phase, setPhase] = useState<Phase>(restoredSnapshot ? 'decision' : 'drafting');
  // Keep the decision workspace focused. Advanced evidence and diagnostics are
  // still available, but are no longer rendered as a wall of panels by default.
  const [workspaceLayer, setWorkspaceLayer] = useState<WorkspaceLayer>('plan');
  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  // 默认普通模式：专业信息不消失，只是收进"专业"里按需展开。
  const [experienceMode, setExperienceMode] = useState<ExperienceMode>('simple');
  // 普通用户默认「文档优先」：先读路线，地图需要时再展开。
  const [mapLayout, setMapLayout] = useState<MapLayout>(() => {
    try {
      const saved = localStorage.getItem(MAP_LAYOUT_KEY);
      if (saved === 'doc' || saved === 'balanced' || saved === 'map') return saved;
    } catch {}
    return 'doc';
  });
  const changeMapLayout = (next: MapLayout) => {
    setMapLayout(next);
    try { localStorage.setItem(MAP_LAYOUT_KEY, next); } catch {}
  };
  // 房间里还有别人 = 房主在场：成员不单独跑推演，方案统一由房主生成后同步过来。
  // 房主离线时（房间里只有自己）才允许成员自己规划，避免把人锁死在一个空房间里。
  const hostInRoom = roomMembers.some((member) => String(member.id) !== String(currentUser.id));
  const memberFollowsHost = !isHost && hostInRoom;
  // 普通模式默认只留今日概览；「查看依据」展开质量指标，「展开专业信息」展开行前清单等。
  const [showRouteEvidence, setShowRouteEvidence] = useState(false);
  const [showPlanDetails, setShowPlanDetails] = useState(false);
  const [agentCapabilities, setAgentCapabilities] = useState<any[]>([]);
  const [agentEvaluation, setAgentEvaluation] = useState<Record<string, any>>({});
  const [providerRegistry, setProviderRegistry] = useState<any[]>([]);
  const [providerQuality, setProviderQuality] = useState<any | null>(null);
  const [providerQualityTrend, setProviderQualityTrend] = useState<any | null>(null);
  const [channelMetrics, setChannelMetrics] = useState<any | null>(null);
  const [featureSnapshot, setFeatureSnapshot] = useState<any | null>(null);
  const [qualitySummary, setQualitySummary] = useState<any | null>(null);
  // 效果指标（近 30 天累计）：采纳率、预算超支率、风险响应时间、用户修改次数。
  // 由网关从已落库事件/记账/通知聚合，算不出来时后端返回 insufficient_data。
  const [effectMetrics, setEffectMetrics] = useState<any | null>(null);
  const [runtimeHealth, setRuntimeHealth] = useState<any | null>(null);
  const [evaluationAudit, setEvaluationAudit] = useState<any | null>(null);
  const [auditCleanupHealth, setAuditCleanupHealth] = useState<any | null>(null);
  const [auditSummary, setAuditSummary] = useState<any | null>(null);
  const [auditComparison, setAuditComparison] = useState<any | null>(null);
  const [auditVersionFilter, setAuditVersionFilter] = useState('');
  const [auditSinceFilter, setAuditSinceFilter] = useState('');
  const [auditLoadingMore, setAuditLoadingMore] = useState(false);
  const [agentDiagnosticsLoading, setAgentDiagnosticsLoading] = useState(false);
  const [savedTripId, setSavedTripId] = useState('');
  const planningResourceId = savedTripId || roomCode;
  useEffect(() => {
    if (phase !== 'decision') setWorkspaceLayer('plan');
  }, [phase]);

  // 记住上次选择的模式；切回普通模式时把专业层收起来，避免留下无法关闭的高级面板。
  useEffect(() => {
    try {
      const saved = localStorage.getItem(EXPERIENCE_MODE_KEY);
      if (saved === 'pro' || saved === 'simple') setExperienceMode(saved);
    } catch {}
  }, []);
  const changeExperienceMode = (next: ExperienceMode) => {
    setExperienceMode(next);
    try { localStorage.setItem(EXPERIENCE_MODE_KEY, next); } catch {}
    if (next === 'simple') {
      setWorkspaceLayer((layer) => (layer === 'evidence' || layer === 'professional' ? 'plan' : layer));
      setShowWorkspaceMenu(false);
    }
  };
  // 普通模式只留"主方案 + 旅中执行"；证据与方案检查进专业模式。
  const layerTabs: Array<[WorkspaceLayer, string, string]> = [
    ['plan', '主方案', '路线、天数与预算'],
    ...(experienceMode === 'pro'
      ? [['evidence', '证据', '来源、时效与约束'] as [WorkspaceLayer, string, string]]
      : []),
    ['execution', '旅中执行', '状态、风险与重规划'],
  ];
  useEffect(() => {
    if (phase !== 'decision' || workspaceLayer !== 'professional') return;
    let active = true;
    setAgentDiagnosticsLoading(true);
    Promise.all([
      apiJson<any>('/api/v1/planning/agents/capabilities'),
      apiJson<any>(`/api/v1/planning/agents/evaluation?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>('/api/v1/planning/providers/registry'),
      apiJson<any>('/api/v1/planning/providers/quality?window_days=7'),
      apiJson<any>('/api/v1/planning/providers/quality/trend?window_days=14'),
      apiJson<any>(`/api/v1/planning/features?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>(`/api/v1/planning/quality?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>('/api/v1/planning/effect-metrics?window_days=30'),
      apiJson<any>(`/api/v1/planning/channels/metrics?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>('/api/v1/planning/models/runtime-health'),
      apiJson<any>(`/api/v1/planning/models/evaluation-audit?limit=8${auditVersionFilter ? `&version=${encodeURIComponent(auditVersionFilter)}` : ''}${auditSinceFilter ? `&since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
      apiJson<any>('/api/v1/planning/models/audit-cleanup-health'),
      apiJson<any>(`/api/v1/planning/models/evaluation-audit/summary?${auditVersionFilter ? `version=${encodeURIComponent(auditVersionFilter)}&` : ''}${auditSinceFilter ? `since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
      apiJson<any>(`/api/v1/planning/models/evaluation-audit/compare${auditSinceFilter ? `?since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
    ]).then(([capabilityPayload, evaluationPayload, registryPayload, providerQualityPayload, providerQualityTrendPayload, featurePayload, qualityPayload, effectMetricsPayload, channelMetricsPayload, runtimeHealthPayload, evaluationAuditPayload, auditCleanupPayload, auditSummaryPayload, auditComparisonPayload]) => {
      if (!active) return;
      const capabilities = capabilityPayload?.data?.agents || capabilityPayload?.agents;
      const evaluation = evaluationPayload?.data?.agents || evaluationPayload?.agents;
      setAgentCapabilities(Array.isArray(capabilities) ? capabilities : []);
      setAgentEvaluation(evaluation && typeof evaluation === 'object' ? evaluation : {});
      const registry = registryPayload?.data?.providers || registryPayload?.providers;
      setProviderRegistry(Array.isArray(registry) ? registry : []);
      setProviderQuality(providerQualityPayload?.data || providerQualityPayload || null);
      setProviderQualityTrend(providerQualityTrendPayload?.data || providerQualityTrendPayload || null);
      setFeatureSnapshot(featurePayload?.data || featurePayload || null);
      setQualitySummary(qualityPayload?.data || qualityPayload || null);
      setEffectMetrics(effectMetricsPayload?.data || effectMetricsPayload || null);
      setChannelMetrics(channelMetricsPayload?.data || channelMetricsPayload || null);
      setRuntimeHealth(runtimeHealthPayload?.data || runtimeHealthPayload || null);
      setEvaluationAudit(evaluationAuditPayload?.data || evaluationAuditPayload || null);
      setAuditCleanupHealth(auditCleanupPayload?.data || auditCleanupPayload || null);
      setAuditSummary(auditSummaryPayload?.data || auditSummaryPayload || null);
      setAuditComparison(auditComparisonPayload?.data || auditComparisonPayload || null);
    }).catch(() => {
      if (!active) return;
      setAgentCapabilities([]);
      setAgentEvaluation({});
      setProviderRegistry([]);
      setProviderQuality(null);
      setProviderQualityTrend(null);
      setFeatureSnapshot(null);
      setQualitySummary(null);
      setEffectMetrics(null);
      setChannelMetrics(null);
      setRuntimeHealth(null);
      setEvaluationAudit(null);
      setAuditCleanupHealth(null);
      setAuditSummary(null);
      setAuditComparison(null);
    }).finally(() => {
      if (active) setAgentDiagnosticsLoading(false);
    });
    return () => { active = false; };
  }, [phase, workspaceLayer, planningResourceId, auditVersionFilter, auditSinceFilter]);
  const loadEarlierAuditEvents = async () => {
    const cursor = evaluationAudit?.pagination?.next_cursor;
    if (!cursor || auditLoadingMore) return;
    setAuditLoadingMore(true);
    try {
      const query = `/api/v1/planning/models/evaluation-audit?limit=8&cursor=${encodeURIComponent(cursor)}${auditVersionFilter ? `&version=${encodeURIComponent(auditVersionFilter)}` : ''}${auditSinceFilter ? `&since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`;
      const payload = await apiJson<any>(query);
      const next = payload?.data || payload;
      if (!next || typeof next !== 'object') return;
      setEvaluationAudit((previous: any) => ({
        ...next,
        timeline: [...(Array.isArray(previous?.timeline) ? previous.timeline : []), ...(Array.isArray(next.timeline) ? next.timeline : [])],
      }));
    } catch {
      // The existing page remains visible when a later audit page is unavailable.
    } finally {
      setAuditLoadingMore(false);
    }
  };
  const [intentsReady, setIntentsReady] = useState(false);
  const [showBlackboard, setShowBlackboard] = useState(false);
  const [tripSaved, setTripSaved] = useState(false);
  // Use the live room as the resource key while drafting, then switch to the
  // persisted trip ID after saving. City names are display data, not IDs.

  // 👑 交互页内可实时切换的出行方式 / 偏好定位（与后端 user_preferences.mode / role 对齐）
  const [activeMode, setActiveMode] = useState<string>(mode);
  const [activeRole, setActiveRole] = useState<string>(role);
  
  const [selectedPoiIndex, setSelectedPoiIndex] = useState<number | null>(0);
  const [activeDayIndex, setActiveDayIndex] = useState<number>(0);

  const [userIntent, setUserIntent] = useState(initialIntent || (initialDestination ? `去${initialDestination}玩3天` : ''));
  // 规划结果与行程执行状态收敛到独立 Store（见 app/lib/stores）。
  // WebSocket 流式消息只负责往 Store 里写，页面组件不再逐个 useState。
  const {
    dynamicRoutes, setDynamicRoutes,
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
  } = usePlanResultStore();
  const {
    visitedNodes, setVisitedNodes,
    nodeVotes, setNodeVotes,
    riskAlert, setRiskAlert,
    replanProposal, setReplanProposal,
    replanBusy, setReplanBusy,
    swappingDay, setSwappingDay,
  } = useTripExecutionStore();

  // 「效果指标」：壁垒不靠“用了多少 Agent”来说明，而是给能算出来的数字。
  // 口径：只展示有真实数据支撑的指标；没有样本或没有真值的，如实写“样本不足/未采集”，
  // 不把估算说成实测，也不把“未知字段比例”包装成“识别准确率”。
  const negotiationRounds = Array.isArray((planGovernance as any)?.negotiation?.revision?.rounds)
    ? ((planGovernance as any).negotiation.revision.rounds as any[])
    : [];
  const conflictReduction = (() => {
    if (negotiationRounds.length === 0) return null;
    const before = Number(negotiationRounds[0]?.conflicts_before?.length ?? 0);
    const after = Number(negotiationRounds[negotiationRounds.length - 1]?.conflicts_after?.length ?? 0);
    if (!Number.isFinite(before) || before <= 0) return null;
    return Math.max(0, Math.round((1 - after / before) * 100));
  })();
  const adoptionRate = qualitySummary?.rates?.adoption != null
    ? Math.round(Number(qualitySummary.rates.adoption) * 100)
    : null;
  const minSatisfaction = fairnessAudit?.minimum_satisfaction != null
    ? Math.round(Number(fairnessAudit.minimum_satisfaction) * 100)
    : null;
  // 近 30 天累计口径来自网关 /planning/effect-metrics：只取已落库的事实。
  // 后端在样本不足时返回 rate=null + status=insufficient_data，这里就显示“样本不足”。
  const effectAdoptionRate = effectMetrics?.adoption?.rate != null
    ? Math.round(Number(effectMetrics.adoption.rate) * 100)
    : null;
  const effectEditTotal = typeof effectMetrics?.user_edits?.total === 'number'
    ? Number(effectMetrics.user_edits.total)
    : null;
  const effectOverrunRate = effectMetrics?.budget_overrun?.rate != null
    ? Math.round(Number(effectMetrics.budget_overrun.rate) * 100)
    : null;
  const effectOverrunTrips = typeof effectMetrics?.budget_overrun?.trips === 'number'
    ? Number(effectMetrics.budget_overrun.trips)
    : null;
  const effectRiskMedianSeconds = typeof effectMetrics?.risk_response?.median_seconds === 'number'
    ? Number(effectMetrics.risk_response.median_seconds)
    : null;
  // 响应时间按量级换单位：秒 → 分钟 → 小时，不统一写成一大串秒数。
  const effectRiskTimeText = (() => {
    if (effectRiskMedianSeconds == null) return '暂无已处理提醒';
    if (effectRiskMedianSeconds < 60) return `${Math.round(effectRiskMedianSeconds)} 秒`;
    if (effectRiskMedianSeconds < 3600) return `${Math.round(effectRiskMedianSeconds / 60)} 分钟`;
    return `${(effectRiskMedianSeconds / 3600).toFixed(1)} 小时`;
  })();
  const enumInvalidRate = Array.isArray(providerQuality?.providers) && providerQuality.providers.length > 0
    ? Math.round(
        (providerQuality.providers.reduce(
          (sum: number, item: any) => sum + Number(item?.enum_invalid_ratio || 0),
          0,
        ) /
          providerQuality.providers.length) *
          100,
      )
    : null;
  // 重规划保留节点比例：只在本次确实有重规划提案时才有分母（保留 + 受影响），
  // 没有提案时写“重规划后显示”，不拿 0 当成 100%。
  const preservationRate = (() => {
    const preserved = Number(replanProposal?.data?.preserved_nodes);
    const affected = Array.isArray(replanProposal?.action?.affected_nodes)
      ? replanProposal.action.affected_nodes.length
      : 0;
    if (!Number.isFinite(preserved) || preserved + affected <= 0) return null;
    return Math.round((preserved / (preserved + affected)) * 100);
  })();
  // 本次没有重规划提案时，退回近 30 天已落库的重规划保留率均值；
  // 连历史样本都没有才显示“重规划后显示”，不拿 0 冒充 100%。
  const effectPreservationRate = effectMetrics?.preservation?.mean != null
    ? Math.round(Number(effectMetrics.preservation.mean) * 100)
    : null;

  const [realPath, setRealPath] = useState<[number, number][]>([]);
  // 方案台账（meal_topup / lodging_geo_adjust / meal_geo_adjust / day_rebalance / official_profiles）：
  // 后端为"吃得着、住得近"做的结构性改动都记在这里，正文上要看得见（2026-10-07）
  const [planLedgers, setPlanLedgers] = useState<any>({});

  const [weatherInfo, setWeatherInfo] = useState<any>(null);
  const [trafficInfo, setTrafficInfo] = useState<any>(null);
  const [safetyInfo, setSafetyInfo] = useState<any>(null);
  const [safetyBrief, setSafetyBrief] = useState<string>('');
  const [briefLoading, setBriefLoading] = useState(false);
  const [showPoster, setShowPoster] = useState(false);
  const [showDiary, setShowDiary] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [showExpenseReport, setShowExpenseReport] = useState(false);
  const [showSatisfaction, setShowSatisfaction] = useState(false);
  // 旅中执行面板回报的“今天”是哪一天：复盘入口（日记）据此先展示当天。
  const [executionTodayDay, setExecutionTodayDay] = useState<number | null>(null);
  const [showBookingHub, setShowBookingHub] = useState(false);
  const [photoModalOpen, setPhotoModalOpen] = useState(false);
  const [photoModalPoi, setPhotoModalPoi] = useState<{name: string; type?: string; city?: string} | null>(null);
  const [activePhotoSource, setActivePhotoSource] = useState(0);
  const [travelDetails, setTravelDetails] = useState<Record<string, any>>({});

  const [streamedText, setStreamedText] = useState(""); 
  const [apiError, setApiError] = useState<string | null>(null);

  const [incrementalHistory, setIncrementalHistory] = useState<string[]>([]);
  const [feedbackPoi, setFeedbackPoi] = useState<string | null>(null);
  const [feedbackReason, setFeedbackReason] = useState('');
  // 预算账本 / POI 反馈是叠在工作台上的模态层，焦点必须锁在弹窗内
  const budgetDialogRef = useFocusTrap<HTMLDivElement>(showBudget);
  const poiFeedbackDialogRef = useFocusTrap<HTMLDivElement>(!!feedbackPoi);

  const wsRef = useRef<WebSocket | null>(null);
  const autoSavedRef = useRef(false);
  // 自动落库只认权威的 final_route：流式 token 阶段路线是逐点长出来的，
  // 此时保存会把路书写成「1 天 1 个节点」。
  const finalRouteRef = useRef<any[] | null>(null);
  const [routeFinalTick, setRouteFinalTick] = useState(0);
  const decisionEventRecordedRef = useRef(false);
  const lastTokenTimeRef = useRef<number>(Date.now());
  const [wsConnected, setWsConnected] = useState(false);
  const [deductionTimeout, setDeductionTimeout] = useState(false);
  const [deductionError, setDeductionError] = useState<string | null>(null);
  // 降级原因：final_route 到达后会清掉 deductionError（否则错误面板会一直挂着），
  // 但「为什么这轮是保底路线」必须留在决策页，不能让用户以为这是正常推演结果。
  const [fallbackReason, setFallbackReason] = useState<string | null>(null);
  // 规划等待体验：真实开始时间用于显示「已用时」，不编造剩余时间。
  const [deductionStartedAt, setDeductionStartedAt] = useState<number | null>(null);
  const [deductionTick, setDeductionTick] = useState(0);
  // 用户点了「停止等待」后，后续到达的流式结果不再抢占界面。
  const deductionDismissedRef = useRef(false);
  // 工具栏「更多」折叠（原来 10 个按钮挤成一团）
  const [showMoreTools, setShowMoreTools] = useState(false);
  // 专业诊断层：高级诊断（技术指标）与智能体运行明细默认收起，避免一屏堆满术语
  const [showAdvancedDiagnostics, setShowAdvancedDiagnostics] = useState(false);
  const [showAgentRuntimeDetail, setShowAgentRuntimeDetail] = useState(false);
  // 局部调整（只重算某一天 / 只替换交通住宿餐饮）：记录请求范围与调整前的路线，
  // 新方案回来后逐天比对，"到底只改了哪一部分"由数据说话，而不是由按钮文案承诺。
  const [pendingScope, setPendingScope] = useState<ScopedScope | null>(null);
  const [scopedReport, setScopedReport] = useState<ScopedReport | null>(null);
  const scopedPlanRef = useRef<{ scope: ScopedScope; before: ScopedRouteNode[] } | null>(null);
  // 超支替代方案：记下用户点了哪一条（以及当时的预算），新方案回来后核对是否真的压进预算。
  const [overagePending, setOveragePending] = useState<OverageOption | null>(null);
  const [overageResult, setOverageResult] = useState<OverageResult | null>(null);
  const overageRequestRef = useRef<{ option: OverageOption; budget: number | null } | null>(null);
  // 预算改动后的连带核对：记下改前预算与请求金额，新方案回来判断预算到底改没改、替代项还需不需要执行。
  const [budgetPending, setBudgetPending] = useState<number | null>(null);
  const [budgetResult, setBudgetResult] = useState<BudgetChangeSummary | null>(null);
  const budgetChangeRef = useRef<{ requested: number; previousBudget: number | null } | null>(null);
  // 多步替代叠加：记下勾选了哪几项与调整前的路线，新方案回来后逐项核对是否落实。
  const [combinedPending, setCombinedPending] = useState<OverageOption[] | null>(null);
  const [combinedResult, setCombinedResult] = useState<CombinedSummary | null>(null);
  // 合并后没落实的项：结果里点名没换的那些，可一键只补做它们（已换好的不再动）。
  const [combinedRetry, setCombinedRetry] = useState<OverageOption[] | null>(null);
  const combinedRequestRef = useRef<{ options: OverageOption[]; before: ScopedRouteNode[]; budget: number | null } | null>(null);

  // 把快照写回工作台：只恢复"方案本身 + 当前视图"，等待中的进度条、弹窗都不恢复。
  const snapshotAppliedRef = useRef(false);
  useEffect(() => {
    if (snapshotAppliedRef.current) return;
    snapshotAppliedRef.current = true;
    const snapshot = restoredSnapshot;
    if (!snapshot) return;
    setDynamicRoutes(snapshot.dynamicRoutes);
    setPlanVariants(
      Array.isArray(snapshot.planVariants) && snapshot.planVariants.length > 0
        ? snapshot.planVariants
        : [{ id: 'primary', name: 'AI 主方案', style: 'primary', desc: '', nodes: snapshot.dynamicRoutes }],
    );
    setActivePlanId(snapshot.activePlanId || 'primary');
    setPlanLedgers(snapshot.planLedgers || {});
    setIsFallbackRoute(Boolean(snapshot.isFallbackRoute));
    if (snapshot.targetCityInfo) setTargetCityInfo(snapshot.targetCityInfo);
    if (snapshot.budgetData) setBudgetData(snapshot.budgetData);
    if (snapshot.planGovernance) setPlanGovernance(snapshot.planGovernance);
    // 证据/专业层在普通模式下没有入口，恢复到会卡在空视图，这里只认主方案与旅中执行。
    if (snapshot.workspaceLayer === 'plan' || snapshot.workspaceLayer === 'execution') {
      setWorkspaceLayer(snapshot.workspaceLayer);
    }
    if (Number.isFinite(snapshot.activeDayIndex) && snapshot.activeDayIndex >= 0) {
      setActiveDayIndex(Math.floor(snapshot.activeDayIndex));
    }
    setPhase('decision');
    // 只在挂载时恢复一次：之后 WebSocket 推来的新方案才是唯一事实。
  }, []);

  // 方案生成后写入快照，刷新时直接复原。没有方案时不写，避免覆盖上一次的有效快照。
  useEffect(() => {
    if (phase !== 'decision' || dynamicRoutes.length === 0) return;
    writeWorkspaceSnapshot(currentUser?.id, roomCode, {
      destination: targetCityInfo?.name || initialDestination || '',
      dynamicRoutes,
      planVariants,
      activePlanId,
      planLedgers,
      isFallbackRoute,
      targetCityInfo,
      budgetData,
      planGovernance,
      workspaceLayer,
      activeDayIndex,
    });
  }, [
    phase,
    dynamicRoutes,
    planVariants,
    activePlanId,
    planLedgers,
    isFallbackRoute,
    targetCityInfo,
    budgetData,
    planGovernance,
    workspaceLayer,
    activeDayIndex,
    currentUser?.id,
    roomCode,
    initialDestination,
  ]);

  // 收口：新方案回来（成功/空路线/报错）时把等待态换成如实核对结果。
  // 只认后端 budget_report 里的数字；没给数字就说无法确认，不替它宣布"压进预算"。
  const finishOverageRequest = (report: any) => {
    if (!overageRequestRef.current) return;
    const { budget: requestBudget } = overageRequestRef.current;
    overageRequestRef.current = null;
    setOveragePending(null);
    setOverageResult(summarizeOverageResult({
      verifiedCost: numberOrNull(report?.verified_cost),
      estimatedCost: numberOrNull(report?.estimated_cost),
      budget: numberOrNull(report?.budget) ?? requestBudget,
      shortfall: numberOrNull(report?.shortfall),
    }));
  };

  // 预算改动的收口：先说清预算改没改，再说清替代项还需不需要执行。
  const finishBudgetChange = (report: any) => {
    if (!budgetChangeRef.current) return;
    const { requested, previousBudget } = budgetChangeRef.current;
    budgetChangeRef.current = null;
    setBudgetPending(null);
    setBudgetResult(summarizeBudgetChange({
      requestedBudget: requested,
      previousBudget,
      newBudget: numberOrNull(report?.budget),
      verifiedCost: numberOrNull(report?.verified_cost),
      estimatedCost: numberOrNull(report?.estimated_cost),
      shortfall: numberOrNull(report?.shortfall),
    }));
  };

  // 合并重排的收口：逐项核对哪一项真的落实、合计省了多少，再按预算报告判断是否压进预算。
  const finishCombinedRequest = (after: any[] | null, report: any) => {
    if (!combinedRequestRef.current) return;
    const { options, before, budget: requestBudget } = combinedRequestRef.current;
    combinedRequestRef.current = null;
    setCombinedPending(null);
    const summary = summarizeCombinedOverage({
      options,
      before,
      after,
      verifiedCost: numberOrNull(report?.verified_cost),
      estimatedCost: numberOrNull(report?.estimated_cost),
      budget: numberOrNull(report?.budget) ?? requestBudget,
      shortfall: numberOrNull(report?.shortfall),
    });
    setCombinedResult(summary);
    setCombinedRetry(unappliedOptions(summary, options));
  };

  const planQuality = useMemo(() => {
    const nodes = Array.isArray(dynamicRoutes) ? dynamicRoutes : [];
    const days = new Set(nodes.map((node: any) => Number(node.day) || 1));
    const dailyCounts = Array.from(days).sort((a, b) => a - b).map((day) => ({ day, count: nodes.filter((node: any) => (Number(node.day) || 1) === day && !node.is_hotel && !node.tags?.includes('住宿')).length }));

    // Cost parsing must distinguish three different situations that used to
    // collapse into one: a node with a real price, a node explicitly priced at
    // zero (free entry), and a node with no supplier price at all. The old
    // implementation matched /[\d,.]+/ against the sentinel string
    // "暂无供应商数据" — which yields no match — and then defaulted to 0, so a
    // completely unpriced itinerary advertised a total of ¥0. Unpriced nodes are
    // now excluded from the sum and counted separately so the UI can say so.
    let pricedSum = 0;
    let unpricedCount = 0;
    for (const node of nodes) {
      const raw = node?.cost;
      if (raw === null || raw === undefined || typeof raw === 'boolean') {
        unpricedCount++;
        continue;
      }
      if (typeof raw === 'number') {
        if (Number.isFinite(raw)) pricedSum += raw;
        else unpricedCount++;
        continue;
      }
      const text = String(raw).trim();
      // Require a currency signal so a year-like value ("2026年") is not read
      // as a price.
      const match = /(?:[¥￥]\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:元|块|人民币))/.exec(text);
      if (!match) {
        unpricedCount++;
        continue;
      }
      const value = Number((match[1] || match[2] || '').replace(/,/g, ''));
      if (Number.isFinite(value)) pricedSum += value;
      else unpricedCount++;
    }
    const pricedCount = nodes.length - unpricedCount;
    const estimatedCost = Math.round(pricedSum * 100) / 100;

    const verified = nodes.filter((node: any) => Array.isArray(node.lnglat) && node.lnglat.length >= 2).length;
    const votes = Object.values(nodeVotes);
    const positiveVotes = votes.filter((value) => value === 'up').length;
    const voteCoverage = nodes.length ? Math.min(1, votes.length / nodes.length) : 0;
    const memberBaseline = roomMembers.length ? Math.min(1, roomMembers.length / 4) : 0.25;
    const consensus = nodes.length
      ? Math.round(((positiveVotes / Math.max(1, votes.length)) * 0.55 + voteCoverage * 0.2 + memberBaseline * 0.25) * 100)
      : 0;
    return {
      nodes: nodes.length,
      days: days.size,
      estimatedCost,
      pricedCount,
      unpricedCount,
      // True when the itinerary exists but carries no usable price anywhere.
      costUnavailable: nodes.length > 0 && pricedCount === 0,
      coverage: nodes.length ? Math.round((verified / nodes.length) * 100) : 0,
      consensus,
      dailyCounts,
    };
  }, [dynamicRoutes, nodeVotes, roomMembers]);

  // 交通耗时：只累加后端 travel_details 里真实返回的分段时长。
  // 拿不到（供应商没给、形状不认识）就返回 null，由概览卡如实写「暂无」。
  const overviewTransportMinutes = useMemo(() => {
    const details = travelDetails as Record<string, any>;
    if (!details || typeof details !== 'object') return null;
    let total = 0;
    let counted = 0;
    for (const value of Object.values(details)) {
      const options = Array.isArray(value) ? value : [value];
      for (const option of options) {
        const minutes = Number(option?.duration ?? option?.duration_minutes);
        if (Number.isFinite(minutes) && minutes > 0 && minutes < 24 * 60) {
          total += minutes;
          counted += 1;
          break;
        }
      }
    }
    return counted > 0 ? Math.round(total) : null;
  }, [travelDetails]);

  // 预算可信度：只认后端 budget_report（已确认/待确认/未取价），
  // 一个金额都没给就返回 null，不在首屏画一块空壳。
  const budgetBreakdown = useMemo<BudgetBreakdown | null>(() => {
    const report = (planGovernance as any)?.budget;
    if (!report || typeof report !== 'object') return null;
    const num = (value: any) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const verified = num(report.verified_cost);
    const estimated = num(report.estimated_cost);
    if ((verified ?? 0) <= 0 && (estimated ?? 0) <= 0) return null;
    return {
      confidence: typeof report.confidence === 'string' ? report.confidence.toLowerCase() : null,
      verifiedCost: verified,
      estimatedCost: estimated,
      unknownCount: Math.max(0, Math.round(num(report.unknown_count) ?? 0)),
      status: typeof report.status === 'string' ? report.status : null,
      shortfall: num(report.shortfall),
    };
  }, [planGovernance]);

  // 超支替代方案使用的预算总额：只认后端 budget_report 里的 budget，没给就按未知处理。
  const overageBudget = useMemo(() => {
    const value = Number((planGovernance as any)?.budget?.budget);
    return Number.isFinite(value) && value > 0 ? value : null;
  }, [planGovernance]);
  const overageShortfall = budgetBreakdown?.shortfall ?? 0;

  // 首屏总预算只认一个来源：有预算报告就用「已确认+待确认」，
  // 没有才退回 budget_breakdown 的行程总额，避免同屏出现两个不同金额。
  const overviewBudgetTotal = useMemo(() => {
    const reportTotal = budgetBreakdown
      ? (budgetBreakdown.verifiedCost ?? 0) + (budgetBreakdown.estimatedCost ?? 0)
      : 0;
    if (reportTotal > 0) return reportTotal;
    const routeTotal = Number(budgetData?.total_budget);
    return Number.isFinite(routeTotal) && routeTotal > 0 ? routeTotal : null;
  }, [budgetBreakdown, budgetData]);

  // 房间内其他人的节点票数（排除自己）：用于在节点卡片上显示「队友赞/踩」。
  const peerVoteCounts = useMemo(
    () => voteCountsByNode(roomVotes, currentUser?.id || ''),
    [roomVotes, currentUser?.id],
  );

  // 👑 将后端路线节点规范化为前端展示结构（与 final_route 映射保持一致）
  const mapRouteForDynamic = (fr: any[]) => (Array.isArray(fr) ? fr : []).map((r: any) => ({
    day: r.day || 1,
    name: r.location || r.name,
    lnglat: normalizeLnglat(r.lnglat),
    coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6",
    desc: r.desc || r.action,
    time: r.time,
    time_reason: r.time_reason || "",
    transport: r.transport,
    tags: r.tags || [],
    category: r.planning_category || r.category || r.type || "other",
    cost: r.cost_estimate || r.cost || "暂无供应商数据",
    cost_estimate: r.cost_estimate ?? r.cost ?? null,
    data_sources: r.data_sources || {},
    price_excerpt: r.price_excerpt || "",
    price_source_url: r.price_source_url || r.cost_source_url || "",
    price_source_label: r.price_source_label || "",
    official_name: r.official_name || "",
    official_site: r.official_site || "",
    official_booking: r.official_booking || "",
    estimated: Boolean(r.estimated),
    // P3 事实可信度四态：后端已按最终路线打好分级，这里必须原样带过去
    // （地图器是显式列字段的，漏一个前端就渲染不出来 —— 2026-10-08 实测踩过）
    trust: r.trust || "",
    trust_label: r.trust_label || "",
    trust_action: r.trust_action || "",
    trust_message: r.trust_message || "",
    source_type: r.source_type || "",
    source_label: r.source_label || "",
    freshness: r.freshness || "",
    derived_confidence: r.derived_confidence ?? null,
    photos: r.photos || [],
    trust_reason: r.trust_reason || "核心地标推荐",
    amap_url: r.amap_url || "",
    map_image: r.map_image || "",
    rating: r.rating || "暂无供应商数据",
    open_time: r.open_time || "暂无供应商数据",
    address: r.address || "",
    hotel_candidates: r.hotel_candidates || [],
    split_info: r.split_info || "",
    merge_point: Boolean(r.merge_point),
    is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
    riskScore: r.risk_score ?? 0,
    crowdedness: r.crowdedness || "",
  }));

  // 满意度只有一个来源：后端 final_route 里按成员在真实路线上的效用算出的 team_satisfaction。
  // 这里曾用 `93 + idx % 4` 给每个成员塞一个"好看"的初值，用户会把编造的数字当成评估结果。
  // 成员变化时只清理已离场成员的旧值，绝不凭空生成分数；没有结果时界面显示"暂无评估"。
  useEffect(() => {
    setTeamSatisfaction((prev) => {
      const names = new Set(roomMembers.map((m) => m.name));
      const next: Record<string, number> = {};
      Object.entries(prev).forEach(([name, score]) => {
        if (names.has(name) && Number.isFinite(score)) next[name] = score;
      });
      return next;
    });
  }, [roomMembers]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    // 规划核对面板的数据来源只有一个：后端 final_route.payload。
    // `stream_token` 里的 [FINAL_JSON] 与 `final_route` 消息带的是同一份 payload，
    // 两条路径都要写，缺一条就会出现"方案出来了但核对面板是空的"。
    const applyPlanGovernance = (fd: any) => {
      if (!fd || typeof fd !== 'object') return;
      // 关联键要在"有没有治理数据"的判断之前抓：保底路线那条 final_route 不带 quality 等字段，
      // 但它同样属于这次规划，不能因此丢掉关联键。
      if (typeof fd.correlation_id === 'string' && fd.correlation_id.trim()) {
        setPlanCorrelationId(fd.correlation_id.trim());
      }
      // 只合并、不整体替换：后端在**保底模式**下额外下发的那条 final_route 只带
      // status/route/simulation，不带 quality/budget_report…；早先这里整体覆盖，
      // 结果那条消息把刚拿到的核对数据（预算/门禁/诉求/降级）全清空、面板整块消失。
      const hasGovernance = Boolean(
        fd.quality || fd.budget_report || fd.fallback || fd.horizon ||
          fd.increment || fd.price_audit || fd.transport_audit || fd.long_trip ||
          fd.review || fd.constraints || fd.degradations || fd.daily_balance ||
          fd.lodging_joint || fd.reference_profile || fd.candidate_supply || fd.composition || fd.negotiation || fd.facts ||
          fd.feedback_applied || fd.lodging_supply || fd.travel_alternatives,
      );
      if (!hasGovernance) return;
      // 新方案到了：清掉"正在按你的要求重排"的即时提示（换点已经落地或如实失败）
      setSwappingDay(null);
      setPlanGovernance((prev) => ({
        quality: fd.quality ?? prev?.quality,
        budget: fd.budget_report ?? prev?.budget,
        fallback: fd.fallback ?? prev?.fallback,
        horizon: fd.horizon ?? prev?.horizon,
        dailyBalance: fd.daily_balance ?? prev?.dailyBalance,
        // 二次增量（这次到底改成了什么）、价格核对、跨城出行比较、长途分段、自动复核、
        // 诉求核对、数据降级：七块都是**后端说了才显示**，缺字段时面板不渲染空壳。
        increment: fd.increment ?? prev?.increment,
        priceAudit: fd.price_audit ?? prev?.priceAudit,
        transportAudit: fd.transport_audit ?? prev?.transportAudit,
        longTrip: fd.long_trip ?? prev?.longTrip,
        review: fd.review ?? prev?.review,
        constraints: fd.constraints ?? prev?.constraints,
        degradations: fd.degradations ?? prev?.degradations,
        // 住哪的联合评估：房价之外的连锁影响（通勤 / 交通费 / 体力 / 碳排 / 可玩节点 / 预算余额）
        lodgingJoint: fd.lodging_joint ?? prev?.lodgingJoint,
        // 本次是否按"参考路线画像"排的（后端只在诉求没说明白时才用，并回填依据）
        referenceProfile: fd.reference_profile ?? prev?.referenceProfile,
        // 本次真实候选够不够（景点/餐饮各多少、有没有补检索、哪些地方靠模型知识补）
        candidateSupply: fd.candidate_supply ?? prev?.candidateSupply,
        // 本次配比（目标 vs 实际）：默认自然=人文各半并穿插美食住宿；自然只算景区
        composition: fd.composition ?? prev?.composition,
        // 本次协商纪要：谁参与、站在什么立场、争了什么、裁决凭什么
        negotiation: fd.negotiation ?? prev?.negotiation,
        // 统一事实记录：请求/约束/供应商/决策/结果/缺口，每条带来源与核实状态
        facts: fd.facts ?? prev?.facts,
        // 节点级负反馈的落地情况：你点过"不喜欢"的地点/原因，这次到底改了没有
        // （后端算出来了但界面不渲染 = 用户以为点了没用）
        feedbackApplied: fd.feedback_applied ?? prev?.feedbackApplied,
        // 住宿候选的供应商实价清单：同样的预算还有哪些有实价的可选（用户的选择权）
        lodgingSupply: fd.lodging_supply ?? prev?.lodgingSupply,
        // 跑得远的那天"可以换成哪个近点"（只提议、不改方案；说一句才换）
        travelAlternatives: fd.travel_alternatives ?? prev?.travelAlternatives,
      }));
    };

    const connect = async () => {
      if (!isMounted) return;
      // Same ticket rule as the collaboration socket: no long-lived credential
      // in the planning stream URL.
      let ticket = '';
      try {
        ticket = await fetchWsTicket();
      } catch {
        ticket = '';
      }
      if (!isMounted) return;
      if (!ticket) {
        // 取票失败（token 刚好过期、网关重启中）不能就此停住：调用方会一直
        // 停在「房间连接正在建立」，再点开始规划也没有任何反应。
        reconnectTimer = setTimeout(connect, 2500);
        return;
      }
      ws = new WebSocket(`${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode || 'OMNI-88')}&ticket=${encodeURIComponent(ticket)}&user_id=${encodeURIComponent(currentUser.id)}`);
      
      ws.onopen = () => {
        setApiError(null);
        setWsConnected(true);
        // 成员进入工作台时先向房主要一次已有方案（房主端由大厅常驻连接回应）。
        if (!isHost) {
          try {
            ws?.send(JSON.stringify({ type: 'room_plan_request', payload: {} }));
          } catch {}
        }
      };
      
      ws.onmessage = (event) => {
        try {
          const rawData = event.data as string;
          const lines = rawData.split('\n').filter((line: string) => line.trim().length > 0);
          
          for (const line of lines) {
            let msg: any;
            try { msg = JSON.parse(line); } catch { continue; }
          
            if (msg.type === "target_city") {
              setTargetCityInfo(msg.payload);
            } else if (msg.type === "weather_info") {
              setWeatherInfo(msg.payload);
            } else if (msg.type === "traffic_info") {
              setTrafficInfo(msg.payload);
            } else if (msg.type === "safety_info") {
              setSafetyInfo(msg.payload);
            } else if (msg.type === "travel_details") {
              setTravelDetails(msg.payload);
            } else if (msg.type === "budget_breakdown") {
              setBudgetData(msg.payload);
            } else if (msg.type === "stream_token") {
              // 用户已停止等待：迟到的流式内容不再改界面，也不再把 phase 推到决策页。
              if (deductionDismissedRef.current) return;
              lastTokenTimeRef.current = Date.now();
              setDeductionTimeout(false);
              setDeductionError(null);
              setStreamedText(prev => {
                const rawToken = msg.payload ? String(msg.payload) : "";
                // 👑 流式原文**原样累积**：以前在累积阶段就把 "null" 全部删掉，而合法 JSON 里的
                // null（例如票价未核实时 price:null）会被删成 "price":，整段 JSON 直接解析失败
                // —— 后果是 final_route 里的 quality / budget_report 全部丢失，核对面板不出现。
                // 现在先按原文解析（我们自己下发的 JSON 一定合法），解析不出来才退回"删 null"的
                // 兜底（那是为模型吐字里夹带的 null 准备的）。展示用的清洗在 humanReadableLogs。
                const newText = (prev || "") + rawToken;

                const finalData = tryExtractJson(newText) || tryExtractJson(newText.replace(/null/g, ""));
                if (finalData && finalData.route && Array.isArray(finalData.route) && finalData.route.length > 0) {
                  const mappedRoutes = finalData.route.map((r: any) => ({
                    day: r.day || 1,
                    name: r.location || r.name,
                    lnglat: normalizeLnglat(r.lnglat),
                    coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
                    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6", 
                    desc: r.desc || r.action,
                    time: r.time,             
                    time_reason: r.time_reason || "",
                    transport: r.transport,   
                    tags: r.tags || [],             
                    category: r.planning_category || r.category || r.type || "other",
                    cost: r.cost_estimate || r.cost || "暂无供应商数据",
                    cost_estimate: r.cost_estimate ?? r.cost ?? null,
                    data_sources: r.data_sources || {},
                    // 👑 价格**出处**（2026-10-07）：三条定向通道（官方文旅名录/文旅部 5A/票务平台）
                    //    带回了票种名与来源 URL，必须传到面板上，否则用户只看到"预估花销 ¥30"，
                    //    分不清是官方门票、平台的哪种票、还是网摘估算。
                    price_excerpt: r.price_excerpt || "",
                    price_source_url: r.price_source_url || r.cost_source_url || "",
                    price_source_label: r.price_source_label || "",
                    ...officialFieldsFor(r.name || r.location, finalData),
                    estimated: Boolean(r.estimated),
                    // P3 事实可信度四态（同上：显式列字段，漏了前端就渲染不出来）
                    trust: r.trust || "",
                    trust_label: r.trust_label || "",
                    trust_action: r.trust_action || "",
                    trust_message: r.trust_message || "",
                    source_type: r.source_type || "",
                    source_label: r.source_label || "",
                    freshness: r.freshness || "",
                    derived_confidence: r.derived_confidence ?? null,
                    photos: r.photos || [],
                    trust_reason: r.trust_reason || "核心地标推荐",
                    amap_url: r.amap_url || "",
                    map_image: r.map_image || "",
                    rating: r.rating || "暂无供应商数据",
                    open_time: r.open_time || "暂无供应商数据",
                    address: r.address || "",
                    hotel_candidates: r.hotel_candidates || [],
                    split_info: r.split_info || "",
                    merge_point: Boolean(r.merge_point),
                    is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
                    riskScore: r.risk_score ?? 0,
                    crowdedness: r.crowdedness || "",
                  }));
                  
                  setDynamicRoutes(mappedRoutes);
                  setPlanLedgers({
                    meal_topup: finalData.meal_topup || null,
                    lodging_geo_adjust: finalData.lodging_geo_adjust || null,
                    meal_geo_adjust: finalData.meal_geo_adjust || null,
                    day_rebalance: finalData.day_rebalance || null,
                    // 每天跑多少公里（后端按最终路线重算过）→ "超预算的那天"要标明
                    travel_feasibility: finalData.travel_feasibility || null,
                    official_profiles: finalData.official_profiles || null,
                  });
                  // 👑 多方案：主方案 + 后端差异化方案。
                  // 流式路径（stream_token → [FINAL_JSON]）是正常到达方式，必须与 final_route
                  // 分支同样写入 planVariants；此前只有 final_route 分支写，导致正常出方案时
                  // 裁决看板的「保留方案 / 替代方案」两列永远是空的，只剩一套方案。
                  const streamVariants = Array.isArray(finalData.plan_variants)
                    ? finalData.plan_variants.map((v: any) => ({ id: v.id, name: v.name, style: v.style, desc: v.desc, nodes: mapRouteForDynamic(v.route) })).filter((v: any) => Array.isArray(v.nodes) && v.nodes.length > 0)
                    : [];
                  setPlanVariants([{ id: 'primary', name: 'AI 主方案', style: 'primary', desc: '', nodes: mappedRoutes }, ...streamVariants]);
                  setActivePlanId('primary');
                  // 流式路径也认一次"保底路线"：后端在保底模式下会额外下发 final_route，
                  // 但只收到 stream_token（或它先到）时这个标记也必须立起来，不能等。
                  setIsFallbackRoute(finalData.status === 'degraded_fallback');
                  if (finalData.negotiation_summary) setConsensusSummary(finalData.negotiation_summary);
                  if (finalData.team_satisfaction) setTeamSatisfaction(finalData.team_satisfaction);
                  // 概率化仿真的分布结果（P50/P90 与超预算概率）
                  if (finalData.simulation && typeof finalData.simulation === 'object') {
                    setSimulation(finalData.simulation);
                  } else {
                    setSimulation(null);
                  }
                  // 规划核对：预算三级模型 + 兜底调整说明 + 超长行程建议 + 门禁问题。
                  // 后端可能只给其中一部分（例如未超预算时没有 fallback），缺什么就渲染什么。
                  applyPlanGovernance(finalData);
                  if (finalData.arbitration_records) setArbitrationRecords(finalData.arbitration_records);
                  if (finalData.fairness && typeof finalData.fairness === 'object') setFairnessAudit(finalData.fairness);
                  if (finalData.member_negotiation && typeof finalData.member_negotiation === 'object') setMemberNegotiation(finalData.member_negotiation);
                  if (Array.isArray(finalData.agent_contracts)) setAgentContractAudits(finalData.agent_contracts);
                  if (finalData.specialist_runtime && typeof finalData.specialist_runtime === 'object') setSpecialistRuntime(finalData.specialist_runtime);
                  if (finalData.optimization_audit && typeof finalData.optimization_audit === 'object') setOptimizationAudit(finalData.optimization_audit);
                  recordDecisionAudit(finalData);
                  // Capture the bandit arm the planner actually used. Without
                  // this the satisfaction form posts an empty bandit_arm_id, the
                  // gateway drops the reward, and the policy accumulates no
                  // evidence at all — the learning loop was broken here.
                  if (finalData.bandit && typeof finalData.bandit === 'object') {
                    setBanditArmId(
                      typeof finalData.bandit.arm_id === 'string' ? finalData.bandit.arm_id : '',
                    );
                    setBanditDecision({
                      reason: finalData.bandit.reason,
                      explored: finalData.bandit.explored,
                      propensity: finalData.bandit.propensity,
                    });
                  } else {
                    // The bandit is disabled by configuration: clear any stale
                    // arm so we never attribute a reward to a previous run.
                    setBanditArmId('');
                    setBanditDecision(null);
                  }
                  setSelectedPoiIndex(0);
                  setActiveDayIndex(0);
                  setPhase('decision');
                } else {
                  const incrementalItems = extractStreamingRoutes(newText);
                  if (incrementalItems.length > 0) {
                    setDynamicRoutes(incrementalItems);
                    setPhase('decision');
                  }
                }
                return newText;
              });
            } else if (msg.type === "error") {
              // 停止等待后服务端再报错，也不弹回到用户面前。
              if (deductionDismissedRef.current) return;
              // 推演阶段错误：不跳回 drafting，让 DeductionPanel 展示错误+重试按钮，
              // 同时等待后端可能下发的保底 final_route
              const errMsg = msg.payload || "规划服务异常";
              setApiError(errMsg);
              setDeductionError(errMsg);
              // 保留原因：即便随后收到保底 final_route，决策页也要能说明这次为什么降级。
              setFallbackReason(errMsg);
              // 局部调整失败：把等待态收掉并如实告知“没拿到新方案”，
              // 不能让进度文案停在那里冒充还在进行。
              if (scopedPlanRef.current) {
                const { scope } = scopedPlanRef.current;
                scopedPlanRef.current = null;
                setPendingScope(null);
                setScopedReport(summarizeScopedChange(scope, [], []));
              }
              // 超支替代方案失败：同样收掉等待态，如实说没拿到可用的预算核对。
              finishOverageRequest(null);
              finishBudgetChange(null);
              finishCombinedRequest(null, null);
            } else if (msg.type === "actual_path") {
              setRealPath(Array.isArray(msg.payload) ? msg.payload : []);
            } else if (msg.type === "risk_subscription_alert") {
              // 旅中风险：gateway 向房间广播风险变化，可能附带"最小扰动范围"提案。
              // 解析交给 lib/riskPerturbation（纯函数、有单测）：算不出来就说算不出来，
              // 绝不在这里凭空推断"影响哪几个节点"。有可执行范围时才把用户带到旅中执行层，
              // 否则提示留在原地（避免无谓跳层打断阅读）。
              const alert = normalizeRiskAlert(msg.payload);
              if (alert) {
                setRiskAlert(alert);
                if (alert.perturbation) setWorkspaceLayer('execution');
              }
            } else if (msg.type === "final_route" || msg.type === "room_plan") {
              // room_plan 是房主广播给房间的同一份方案：成员直接复用，不再各自推演。
              const isSharedPlan = msg.type === "room_plan";
              if (isSharedPlan && isHost) return; // 自己的广播回显，已经应用过了
              if (deductionDismissedRef.current) return;
              const fd = msg.payload || {};
              const fr = Array.isArray(fd.route) ? fd.route : [];
              // 清除推演阶段的错误/超时状态
              setDeductionError(null);
              setDeductionTimeout(false);
              setIsFallbackRoute(fd.status === "degraded_fallback");
              if (fr.length > 0) {
                const mapped = fr.map((r: any) => ({
                  day: r.day || 1,
                  name: r.location || r.name,
                  lnglat: normalizeLnglat(r.lnglat),
                  coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
                  color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6",
                  desc: r.desc || r.action,
                  time: r.time,
                  time_reason: r.time_reason || "",
                  transport: r.transport,
                  tags: r.tags || [],
                  category: r.planning_category || r.category || r.type || "other",
                  cost: r.cost_estimate || r.cost || "暂无供应商数据",
                  cost_estimate: r.cost_estimate ?? r.cost ?? null,
                  data_sources: r.data_sources || {},
                  // 价格出处（同上：官方/票务平台/网摘 + 来源链接 + 官方预约入口）
                  price_excerpt: r.price_excerpt || "",
                  price_source_url: r.price_source_url || r.cost_source_url || "",
                  price_source_label: r.price_source_label || "",
                  ...officialFieldsFor(r.name || r.location, fd),
                  estimated: Boolean(r.estimated),
                  // P3 事实可信度四态（同上）
                  trust: r.trust || "",
                  trust_label: r.trust_label || "",
                  trust_action: r.trust_action || "",
                  trust_message: r.trust_message || "",
                  source_type: r.source_type || "",
                  source_label: r.source_label || "",
                  freshness: r.freshness || "",
                  derived_confidence: r.derived_confidence ?? null,
                  photos: r.photos || [],
                  trust_reason: r.trust_reason || "核心地标推荐",
                  amap_url: r.amap_url || "",
                  map_image: r.map_image || "",
                  rating: r.rating || "暂无供应商数据",
                  open_time: r.open_time || "暂无供应商数据",
                  address: r.address || "",
                  hotel_candidates: r.hotel_candidates || [],
                  split_info: r.split_info || "",
                  merge_point: Boolean(r.merge_point),
                  is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
                  riskScore: r.risk_score ?? 0,
                  crowdedness: r.crowdedness || "",
                }));
                setDynamicRoutes(mapped);
                // 超支替代方案的核对：用这次返回的 budget_report 判断是否真的压进预算。
                finishOverageRequest(fd.budget_report);
                finishBudgetChange(fd.budget_report);
                finishCombinedRequest(mapped, fd.budget_report);
                // 局部调整的核对只做一次：把"实际改到了哪里"写进结果，清掉等待态。
                if (scopedPlanRef.current) {
                  const { scope, before } = scopedPlanRef.current;
                  scopedPlanRef.current = null;
                  setPendingScope(null);
                  setScopedReport(summarizeScopedChange(scope, before, mapped));
                }
                setPlanLedgers({
                  meal_topup: fd.meal_topup || null,
                  lodging_geo_adjust: fd.lodging_geo_adjust || null,
                  meal_geo_adjust: fd.meal_geo_adjust || null,
                  day_rebalance: fd.day_rebalance || null,
                  travel_feasibility: fd.travel_feasibility || null,
                  official_profiles: fd.official_profiles || null,
                });
                setSelectedPoiIndex(0);
                setActiveDayIndex(0);
                setPhase('decision');
                // 👑 多方案：主方案 + 后端返回的差异化方案，一并存入供切换
                const _vs = Array.isArray(fd.plan_variants)
                  ? fd.plan_variants.map((v: any) => ({ id: v.id, name: v.name, style: v.style, desc: v.desc, nodes: mapRouteForDynamic(v.route) })).filter((v: any) => Array.isArray(v.nodes) && v.nodes.length > 0)
                  : [];
                setPlanVariants([{ id: 'primary', name: 'AI 主方案', style: 'primary', desc: '', nodes: mapped }, ..._vs]);
                setActivePlanId('primary');
                setPlanSwitchNotice('');
                // 到这一步才是完整路线：交给自动落库，避免用流式中间态写路书。
                finalRouteRef.current = mapped;
                setRouteFinalTick((tick) => tick + 1);
                // 房主把这份方案同步给房间里的其他成员（成员端不再重复调用 AI）。
                if (!isSharedPlan && isHost) onBroadcastPlan?.(fd);
              }
              // 带回了 final_route 但没有路线：同样是"没拿到新方案"，不能停在等待态。
              if (fr.length === 0 && scopedPlanRef.current) {
                const { scope } = scopedPlanRef.current;
                scopedPlanRef.current = null;
                setPendingScope(null);
                setScopedReport(summarizeScopedChange(scope, [], []));
              }
              if (fr.length === 0) {
                finishOverageRequest(fd.budget_report);
                finishBudgetChange(fd.budget_report);
                finishCombinedRequest(null, fd.budget_report);
              }
              if (fd.negotiation_summary) setConsensusSummary(fd.negotiation_summary);
              if (fd.team_satisfaction) setTeamSatisfaction(fd.team_satisfaction);
              if (fd.arbitration_records) setArbitrationRecords(fd.arbitration_records);
              if (fd.fairness && typeof fd.fairness === 'object') setFairnessAudit(fd.fairness);
              if (fd.member_negotiation && typeof fd.member_negotiation === 'object') setMemberNegotiation(fd.member_negotiation);
              if (Array.isArray(fd.agent_contracts)) setAgentContractAudits(fd.agent_contracts);
              if (fd.specialist_runtime && typeof fd.specialist_runtime === 'object') setSpecialistRuntime(fd.specialist_runtime);
              if (fd.optimization_audit && typeof fd.optimization_audit === 'object') setOptimizationAudit(fd.optimization_audit);
              recordDecisionAudit(fd);
              // final_route 是权威 payload：只收到它（重连、回放、流式 token 丢了）时
              // 核对面板同样要有数据。
              applyPlanGovernance(fd);
            }
          }
        } catch (err) {}
      };

      ws.onerror = () => { setApiError("房间连接中断，请检查后端网关是否正常运行。"); setWsConnected(false); };
      ws.onclose = () => {
        if (!isMounted) return;
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 2000);
      };
      wsRef.current = ws;
    };

    connect();

    return () => {
      isMounted = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [roomCode, currentUser.id]);

  // 👑 客户端超时保护：deduction 阶段超过 90s 未收到任何 token 则提示重试
  useEffect(() => {
    if (phase !== 'deduction') return;
    const timer = setInterval(() => {
      const elapsed = Date.now() - lastTokenTimeRef.current;
      if (elapsed > 90_000) {
        setDeductionTimeout(true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [phase]);

  // 每秒刷新一次「已用时」，秒表停就停，不做匀速进度条假动画。
  useEffect(() => {
    if (phase !== 'deduction' || !deductionStartedAt) return;
    setDeductionTick(Date.now());
    const timer = setInterval(() => setDeductionTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [phase, deductionStartedAt]);

  const deductionElapsedSeconds =
    phase === 'deduction' && deductionStartedAt
      ? Math.max(0, Math.round(((deductionTick || Date.now()) - deductionStartedAt) / 1000))
      : 0;

  // Route delivery completes the reasoning step; return focus to the map so
  // the next actions are immediately available without a blocking drawer.
  useEffect(() => {
    if (phase === 'decision') setShowBlackboard(false);
  }, [phase]);

  const humanReadableLogs = useMemo(() => {
    if (!streamedText) return "正在生成方案并协调成员偏好...";
    
    let thoughtPart = streamedText;
    if (thoughtPart.includes("[FINAL_JSON]")) {
      thoughtPart = thoughtPart.split("[FINAL_JSON]")[0];
    } else if (thoughtPart.includes("```json")) {
      thoughtPart = thoughtPart.split("```json")[0];
    } else {
      const firstJsonIdx = thoughtPart.search(/\{\s*"status"|\{\s*"route"/);
      if (firstJsonIdx !== -1) {
        thoughtPart = thoughtPart.substring(0, firstJsonIdx);
      }
    }

    const cleaned = thoughtPart.replace(/null/g, "").trim();
    return cleaned || "[系统中枢]: 正在分析全员偏好矩阵与分合流图论，生成定制路书中...";
  }, [streamedText]);

  const totalDays = useMemo(() => {
    if (!dynamicRoutes || dynamicRoutes.length === 0) return [1];
    const daysSet = new Set<number>(dynamicRoutes.map((r: any) => Number(r.day) || 1));
    return Array.from(daysSet).sort((a, b) => a - b);
  }, [dynamicRoutes]);

  const currentDayRoutes = useMemo(() => {
    if (!dynamicRoutes || dynamicRoutes.length === 0) return [];
    const targetDay = totalDays[activeDayIndex] || 1;
    return dynamicRoutes.filter((r: any) => (Number(r.day) || 1) === targetDay);
  }, [dynamicRoutes, activeDayIndex, totalDays]);

  const handleSubmitIntent = (intentOverride?: unknown) => {
    const intent = typeof intentOverride === 'string' ? intentOverride : userIntent;
    if (!intent.trim()) {
      setApiError('请先描述目的地、天数或本次旅行的重点诉求');
      return;
    }
    if (memberFollowsHost) {
      setApiError('方案由房主统一生成，房主开始规划后会自动同步到这里');
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('房间连接正在建立，请稍候再点击“开始规划”');
      return;
    }

    setIntentsReady(true);
    setPhase('deduction');
    setShowBlackboard(true); 
    setApiError(null);
    setStreamedText(""); 
    setDeductionError(null);
    setDeductionTimeout(false);
    setDeductionStartedAt(Date.now());
    deductionDismissedRef.current = false;
    // 新一轮规划：上一次的降级原因不再适用。
    setFallbackReason(null);
    // 新的一次完整规划：上一次超支替代方案的结论不再适用，先清掉。
    overageRequestRef.current = null;
    setOveragePending(null);
    setOverageResult(null);
    budgetChangeRef.current = null;
    setBudgetPending(null);
    setBudgetResult(null);
    combinedRequestRef.current = null;
    setCombinedPending(null);
    setCombinedResult(null);
    setCombinedRetry(null);
    setIsFallbackRoute(false);
    setSpecialistRuntime(null);
    setOptimizationAudit(null);
    lastTokenTimeRef.current = Date.now();
    autoSavedRef.current = false;
    decisionEventRecordedRef.current = false;

    const updatedHistory = [...incrementalHistory, intent];
    setIncrementalHistory(updatedHistory);

    // 动态同步发送当前房间全部成员画像
    const wsPayload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: activeMode,
          role: activeRole, 
          intent,
          history_sequence: updatedHistory,
          current_existing_route: dynamicRoutes,
          room_members: roomMembers.map((m: any) => ({
            id: m.id,
            name: m.name,
            role: m.role,
            intent: m.intent,
            avatarUrl: m.avatarUrl || m.avatar_url || ''
          }))
        }
      }
    };
    try {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(wsPayload));
      } else {
        setApiError("WebSocket 连接已断开，请刷新页面后重试。");
        setIntentsReady(false);
        setPhase('drafting');
        setShowBlackboard(false);
      }
    } catch (e) {
      setApiError("消息发送失败，请刷新页面后重试。");
      setIntentsReady(false);
      setPhase('drafting');
      setShowBlackboard(false);
    }
  };

  const handleResetIntent = () => {
    setPhase('drafting');
    setIntentsReady(false);
    setSelectedPoiIndex(null);
    setDeductionError(null);
    setDeductionTimeout(false);
    setIsFallbackRoute(false);
    setFallbackReason(null);
    setSpecialistRuntime(null);
    setOptimizationAudit(null);
    setStreamedText("");
  };

  const recordDecisionAudit = (data: any) => {
    if (decisionEventRecordedRef.current || !planningResourceId || !data || typeof data !== 'object') return;
    decisionEventRecordedRef.current = true;
    const fairness = data.fairness && typeof data.fairness === 'object' ? data.fairness : {};
    const pareto = data.optimization_audit && typeof data.optimization_audit === 'object' ? data.optimization_audit : {};
    const runtime = data.specialist_runtime && typeof data.specialist_runtime === 'object' ? data.specialist_runtime : {};
    void apiFetch('/api/v1/planning/events', {
      method: 'POST',
      body: JSON.stringify({
        trip_id: planningResourceId,
        event_type: 'planning_decision_audit',
        payload: {
          module: 'joint',
          model_version: data.model?.version || data.model_version || 'ai-service',
          candidate_count: Number(pareto.frontier_count || 0) + Number(pareto.rejected_count || 0),
          pareto_frontier_count: Number(pareto.frontier_count || 0),
          pareto_rejected_count: Number(pareto.rejected_count || 0),
          pareto_rejected_reasons: Array.isArray(pareto.rejected) ? pareto.rejected.slice(0, 20).map((item: any) => ({ reason: item?.reason || 'unknown', candidate: item?.candidate || '' })) : [],
          fairness_minimum_utility: Number(fairness.minimum_utility || 0),
          fairness_utility_stddev: Number(fairness.utility_stddev || 0),
          fairness_threshold: Number(fairness.threshold || 0.15),
          fairness_threshold_exceeded: Boolean(fairness.threshold_exceeded),
          fairness_repair_status: fairness.repair_status || 'not_needed',
          specialist_completed: Number(runtime.completed || 0),
          specialist_degraded: Number(runtime.degraded || 0),
          specialist_gate_audits: runtime.gate_audits || {},
        },
      }),
    }).catch(() => {
      decisionEventRecordedRef.current = false;
    });
  };

  /**
   * 一键换近点：把"第 N 天不想跑那么远"当成一句**增量**发回去重排。
   *
   * 为什么这么做（2026-10-05）：后端已经把"可以换成哪个近点"算好了（同类、同档次、离得近），
   * 但界面上只写着"想换直接说一句就行" —— 等于把闭环又交回给用户。
   * 这句话里的「不想跑那么远」会命中距离容忍度=low，于是：
   *   ① 排除/替换点**优先选近的**；② 收口时把远点**就地换成算好的近点**（同一时段）。
   */
  const handleSwapNear = (day: number) => {
    sendIncrementInstruction(`第 ${day} 天太远了，不想跑那么远，换近一点的`, `第 ${day} 天的远点`);
  };

  /** 点「换住这家」：把"住宿换成 X"作为一句增量发回去。 */
  const handleSwapHotel = (name: string) => {
    sendIncrementInstruction(`住宿换成 ${name}`, '住宿');
  };

  /**
   * 只重算某一天 / 只替换某一类：把范围写成一句明确的增量发回去。
   *
   * 前端不替后端保证"只改这部分"：调整前的路线先留档，新方案回来后
   * 用 summarizeScopedChange 逐天比对，实际改到了哪里如实回显。
   */
  const handleScopedAdjust = (scope: ScopedScope) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('连接正在恢复中，请 2 秒后再点一次');
      return;
    }
    setScopedReport(null);
    // 新的调整会改总额：上一次"超支/预算/合并"的结论不再适用。
    setOverageResult(null);
    setBudgetResult(null);
    combinedRequestRef.current = null;
    setCombinedPending(null);
    setCombinedResult(null);
    setCombinedRetry(null);
    setPendingScope(scope);
    // 用户若在上一轮点过「停止等待」，这里必须解除拦截，否则新方案回来会被丢掉，
    // 界面会永久停在"正在重排"。
    deductionDismissedRef.current = false;
    scopedPlanRef.current = {
      scope,
      before: dynamicRoutes.map((node: any) => ({
        day: node.day,
        name: node.name,
        type: node.type,
        tags: node.tags,
        category: node.category,
        is_hotel: node.is_hotel,
        transport: node.transport,
      })),
    };
    // what 传 null：局部调整有自己的进度提示，不再复用「已按你的要求换…」那条文案
    sendIncrementInstruction(buildScopedInstruction(scope), null);
  };

  /**
   * 房主采纳一条成员建议：范围按「第 X 天」处理，把成员原话作为调整意图，
   * 走与手动局部调整相同的流程；结果由 room_plan 自动同步给所有成员。
   */
  const handleAdoptSuggestion = (comment: RoomComment) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('连接正在恢复中，请 2 秒后再点一次');
      return;
    }
    const day = Math.max(1, Math.round(Number(comment.day) || 1));
    const scope: ScopedScope = { kind: 'day', day };
    setScopedReport(null);
    setOverageResult(null);
    setBudgetResult(null);
    combinedRequestRef.current = null;
    setCombinedPending(null);
    setCombinedResult(null);
    setCombinedRetry(null);
    setPendingScope(scope);
    deductionDismissedRef.current = false;
    scopedPlanRef.current = {
      scope,
      before: dynamicRoutes.map((node: any) => ({
        day: node.day,
        name: node.name,
        type: node.type,
        tags: node.tags,
        category: node.category,
        is_hotel: node.is_hotel,
        transport: node.transport,
      })),
    };
    sendIncrementInstruction(`第 ${day} 天「${comment.node_name}」的调整建议：${comment.text}。其它天与其它节点保持原样。`, null);
    onAckNodeComment?.(comment.id, 'adopted');
  };

  /**
   * 超支替代方案：把"先动哪一处"写成一句明确的增量发回去重排。
   *
   * 与局部调整同一套收口：请求前先解除"停止等待"的拦截（否则新方案回来会被丢掉，
   * 界面永久停在重排中）；新方案回来后用后端 budget_report 核对是否真的压进预算。
   */
  const handleOverageOption = (option: OverageOption) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('连接正在恢复中，请 2 秒后再点一次');
      return;
    }
    setOverageResult(null);
    setOveragePending(option);
    overageRequestRef.current = { option, budget: overageBudget };
    budgetChangeRef.current = null;
    setBudgetPending(null);
    setBudgetResult(null);
    combinedRequestRef.current = null;
    setCombinedPending(null);
    setCombinedResult(null);
    setCombinedRetry(null);
    deductionDismissedRef.current = false;
    sendIncrementInstruction(option.instruction, null);
  };

  /**
   * 预算改动后的连带核对：把"预算改成多少"发回去重排。
   *
   * 回来后不只比对总额，还要说清两件事：预算到底改没改（后端可能没采用），
   * 以及之前列出的替代项还需不需要执行（改高就不用再换便宜货，改低则缺口可能更大）。
   */
  const handleOverageBudgetChange = (amount: number) => {
    const requested = Math.round(Number(amount));
    if (!Number.isFinite(requested) || requested <= 0) {
      setApiError('请输入大于 0 的预算金额');
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('连接正在恢复中，请 2 秒后再试');
      return;
    }
    setBudgetResult(null);
    setBudgetPending(requested);
    budgetChangeRef.current = { requested, previousBudget: overageBudget };
    // 预算重排走自己的收口，别让上一次"换便宜同类"的核对抢走这次结果。
    overageRequestRef.current = null;
    setOveragePending(null);
    setOverageResult(null);
    combinedRequestRef.current = null;
    setCombinedPending(null);
    setCombinedResult(null);
    setCombinedRetry(null);
    deductionDismissedRef.current = false;
    sendIncrementInstruction(`把总预算改成 ¥${requested.toLocaleString()}，其它安排尽量保持原样`, null);
  };

  /**
   * 多步替代叠加：把勾选的 2~3 项合并成一句增量，一次重排完成。
   *
   * 前端先留档调整前的节点名与报价，新方案回来后逐项核对哪一项真的换了、
   * 哪一项没换（如实点名），再用合计变化与预算报告判断省了多少、是否压进预算。
   */
  const handleCombineOverage = (options: OverageOption[]) => {
    // 首次合并用合并指令；一键补做只剩 1 项时用它自己的单步指令，不硬凑成"合并"。
    const instruction = buildRetryInstruction(options);
    if (!instruction) {
      setApiError('请至少勾选 2 项再合并重排');
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('连接正在恢复中，请 2 秒后再试');
      return;
    }
    setCombinedResult(null);
    setCombinedRetry(null);
    setCombinedPending(options);
    combinedRequestRef.current = {
      options,
      before: dynamicRoutes.map((node: any) => ({
        day: node.day,
        name: node.name,
        type: node.type,
        tags: node.tags,
        category: node.category,
        is_hotel: node.is_hotel,
        transport: node.transport,
        cost: node.cost,
        cost_estimate: node.cost_estimate,
      })),
      budget: overageBudget,
    };
    // 单步 / 预算两条等待态都清掉，避免互相抢这次结果。
    overageRequestRef.current = null;
    setOveragePending(null);
    setOverageResult(null);
    budgetChangeRef.current = null;
    setBudgetPending(null);
    setBudgetResult(null);
    deductionDismissedRef.current = false;
    sendIncrementInstruction(instruction, null);
  };

  /**
   * 把一句**用户意图**作为增量发回去重排（一键换近点 / 换住这家共用）。
   *
   * 为什么统一走"一句话"而不是结构化指令：后端的增量解析与距离口径都按自然语言设计
   * （`parse_increment` + `extract_preference_signals`），一句话能同时被两侧正确读到；
   * 而且**回传的是意图**，实际换成了谁由后端如实报回来（找不到那一家就如实说没换成）。
   */
  const sendIncrementInstruction = (instruction: string, what: string | null) => {
    if (memberFollowsHost) {
      setApiError("方案由房主统一调整，房主改完后会自动同步到这里");
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError("连接正在恢复中，请 2 秒后再点一次");
      return;
    }
    setSwappingDay(what);
    setDeductionError(null);
    setStreamedText("");
    // 方案要变了：上一次"是否压进预算"的结论不再适用，等这次结果回来重新核对。
    if (what != null) setOverageResult(null);
    lastTokenTimeRef.current = Date.now();
    wsRef.current.send(
      JSON.stringify({
        type: "agent_negotiate",
        payload: {
          destinations: [],
          user_preferences: {
            mode: activeMode,
            role: activeRole,
            intent: `${userIntent}（${instruction}）`,
            history_sequence: incrementalHistory,
            current_existing_route: dynamicRoutes,
            room_members: roomMembers.map((m: any) => ({
              id: m.id, name: m.name, role: m.role, intent: m.intent, avatarUrl: m.avatarUrl || m.avatar_url || ''
            }))
          }
        }
      }),
    );
  };

  // 推演失败/超时后重试：重置状态并重新发送 agent_negotiate。
  // 决策页的「重新尝试智能规划」也复用这条路径，所以要显式回到 deduction 阶段。
  const handleRetryDeduction = () => {
    if (memberFollowsHost) {
      setApiError("方案由房主统一生成，房主开始规划后会自动同步到这里");
      return;
    }
    if (!wsRef.current) {
      setApiError("连接已断开，请刷新页面重试");
      return;
    }
    setDeductionError(null);
    setDeductionTimeout(false);
    setFallbackReason(null);
    setStreamedText("");
    setPhase('deduction');
    setIntentsReady(true);
    setShowBlackboard(true);
    setDeductionStartedAt(Date.now());
    deductionDismissedRef.current = false;
    lastTokenTimeRef.current = Date.now();
    const payload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: activeMode,
          role: activeRole,
          intent: userIntent,
          history_sequence: incrementalHistory,
          current_existing_route: dynamicRoutes,
          room_members: roomMembers.map((m: any) => ({
            id: m.id, name: m.name, role: m.role, intent: m.intent, avatarUrl: m.avatarUrl || m.avatar_url || ''
          }))
        }
      }
    };
    try {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(payload));
      } else {
        // 连接未就绪，等待自动重连后提示用户再点
        setApiError("连接正在恢复中，请 2 秒后再次点击重试");
      }
    } catch {
      setApiError("重试失败，请刷新页面后重试");
    }
  };

  // 停止等待：立刻回到诉求描述页，并忽略这次请求迟到的结果。
  // 诚实说明：后端没有取消通道，这次生成可能仍在后台跑完，只是不会再弹出来。
  const handleCancelDeduction = () => {
    deductionDismissedRef.current = true;
    setPhase('drafting');
    setIntentsReady(false);
    setShowBlackboard(false);
    setStreamedText('');
    setDeductionError(null);
    setDeductionTimeout(false);
    setFallbackReason(null);
    setDeductionStartedAt(null);
  };

  const persistTripRoutes = async (routesToSave: any[]) => {
    if (!routesToSave || routesToSave.length === 0) return false;
    const cityName = targetCityInfo?.name || '';
    // 天数必须按"这次要保存的路线"算：用界面上的 totalDays 会拿到上一版/流式中间态的天数，
    // 路书标题就会被写成「1日行程」。
    const savedDays = Array.from(new Set(routesToSave.map((node: any) => Number(node?.day) || 1))).sort((a, b) => a - b);
    const title = `${cityName || '我的行程'}${savedDays.length}日行程`;
    const content = JSON.stringify({
      summary: consensusSummary,
      city: cityName,
      days: savedDays,
      routes: routesToSave,
      // 带上关联键：网关侧事件（采纳/到访/重规划）才能与 ai-service 的推演事件串成一条轨迹
      ...(planCorrelationId ? { correlation_id: planCorrelationId } : {}),
    });
    try {
      const res = await apiFetch('/api/user/trip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title, dest_city: cityName, content, days: savedDays.length, travelers: roomMembers.length > 0 ? roomMembers.length : 1 })
      });
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`行程响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '行程保存失败');
      if (typeof data?.trip_id === 'string' && data.trip_id.trim()) {
        setSavedTripId(data.trip_id.trim());
      }
      setTripSaved(true);
      setTimeout(() => setTripSaved(false), 1800);
      return true;
    } catch (e: any) {
      setApiError(e.message || '行程保存失败');
      return false;
    }
  };

  const handleSaveTrip = async () => {
    const saved = await persistTripRoutes(dynamicRoutes);
    if (!saved) return;
    // 「保存到我的行程」= 用户明确选定了当前这套方案 → 记一次采纳（事件流里的 user.accepted）。
    // 下面的**自动保存**刻意不走这里：那只是系统落库，不代表用户做了选择，
    // 否则方案一生成就"被采纳"，是假信号。
    // recordPlanAcceptance 按「方案名 + 风格」去重，重复点保存不会重复上报。
    const activeVariant = planVariants.find((v: any) => v.id === activePlanId) || planVariants[0];
    if (activeVariant) await recordPlanAcceptance({ ...activeVariant, nodes: dynamicRoutes });
  };

  const applyReplanCandidate = async (candidate: any) => {
    if (!replanProposal || replanBusy) return;
    setReplanBusy(true);
    try {
      const fairness = replanProposal.data?.fairness;
      if (fairness?.status === 'evaluated' && fairness.threshold_exceeded === true) {
        throw new Error('成员公平指标超过阈值，当前提案不能直接应用');
      }
      const affected = Array.isArray(replanProposal.action?.affected_nodes) ? replanProposal.action.affected_nodes : [];
      if (affected.length !== 1) throw new Error('当前提案必须拆分为逐节点替换后才能应用');
      const targetIndex = dynamicRoutes.findIndex((node: any) => affected.includes(String(node?.name || node?.location || '').trim()));
      if (targetIndex < 0) throw new Error('无法定位待替换节点，提案未应用');
      const target = dynamicRoutes[targetIndex];
      const targetKey = `${Number(target?.day) || 1}:${String(target?.name || target?.location || '').trim()}`;
      const executionResponse = await apiFetch(`/api/v1/planning/execution?trip_id=${encodeURIComponent(planningResourceId)}`);
      const executionPayload = await executionResponse.json();
      const executionRows = executionPayload?.data?.states || executionPayload?.states || [];
      const executionState = Array.isArray(executionRows) ? executionRows.find((row: any) => row?.node_key === targetKey) : null;
      if (executionState && ['visited', 'skipped'].includes(String(executionState.status))) {
        throw new Error(`节点“${target.name || target.location}”已经${executionState.status === 'visited' ? '到访' : '跳过'}，不能再替换`);
      }
      const validationResponse = await apiFetch('/api/v1/planning/replan/validate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trip_id: planningResourceId,
          destination: targetCityInfo?.name || '',
          existing_route: dynamicRoutes,
          affected_nodes: affected,
          candidate,
        }),
      });
      const validationPayload = await validationResponse.json();
      const validation = validationPayload?.data || validationPayload;
      if (!validationResponse.ok || validation?.hard_satisfied !== true) {
        const reasons = Array.isArray(validation?.violations) ? validation.violations.join('、') : '硬约束未通过';
        throw new Error(`候选未通过应用校验：${reasons}`);
      }
      // The gateway is the single commit point. The browser only previews and
      // validates candidates; it must not overwrite the trip or emit an
      // accepted/applied event before the server confirms the transaction.
      const confirmResponse = await apiFetch('/api/v1/planning/replan/confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trip_id: planningResourceId,
          proposal_id: replanProposal.proposalId || '',
          affected_nodes: affected,
          candidate,
          confirmation: 'user_confirmed',
        }),
      });
      const confirmPayload = await confirmResponse.json();
      if (!confirmResponse.ok) throw new Error(confirmPayload?.error?.message || confirmPayload?.error || '服务端未确认重规划');
      const confirmed = confirmPayload?.data || confirmPayload;
      const nextRoutes = Array.isArray(confirmed?.route) ? confirmed.route : [...dynamicRoutes];
      setDynamicRoutes(nextRoutes);
      setReplanProposal(null);
      setApiError(`应急替代节点已由服务端确认并保存，保留 ${confirmed?.preserved_nodes ?? Math.max(0, dynamicRoutes.length - affected.length)} 个未受影响节点`);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : '应用应急提案失败');
    } finally {
      setReplanBusy(false);
    }
  };

  const rejectReplanProposal = async () => {
    if (!replanProposal || replanBusy) return;
    setReplanBusy(true);
    try {
      await apiFetch('/api/v1/planning/replan/reject', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: planningResourceId, proposal_id: replanProposal.proposalId || '', reason: 'keep_original' }),
      });
      setReplanProposal(null);
      setApiError('已拒绝应急重规划提案，当前行程保持不变');
    } catch (error) {
      setApiError(error instanceof Error ? error.message : '记录提案拒绝失败');
    } finally {
      setReplanBusy(false);
    }
  };

  const downloadTextFile = (filename: string, content: string, mime: string) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  };
  const diaryMarkdown = buildDiaryMarkdown(dynamicRoutes, { city: targetCityInfo?.name || '' });
  const downloadIcs = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_行程日历.ics`, buildIcsCalendar(dynamicRoutes, { city: targetCityInfo?.name || '' }), 'text/calendar;charset=utf-8');
  const downloadDiary = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_旅行日记.md`, diaryMarkdown, 'text/markdown;charset=utf-8');
  const downloadJson = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_路书.json`, JSON.stringify({ city: targetCityInfo?.name || '', routes: dynamicRoutes }, null, 2), 'application/json;charset=utf-8');

  // 👑 自动保存到“我的路书”：拿到权威的完整路线后静默落库，无需手动点击。
  // 注意：这里直接调 persistTripRoutes 而**不是** handleSaveTrip —— 自动落库不等于"用户采纳"，
  // 采纳只在用户真的点了「保存到我的行程」或切换方案时才记录。
  // 也不能用 phase/dynamicRoutes 触发：流式阶段 setPhase('decision') 时路线只有一个节点，
  // 会被保存成「1 天 1 个节点」的路书（这正是历史上所有路书只有 1 天的原因）。
  useEffect(() => {
    const routes = finalRouteRef.current;
    if (!routes || routes.length === 0 || autoSavedRef.current) return;
    autoSavedRef.current = true;
    finalRouteRef.current = null;
    void persistTripRoutes(routes);
  }, [routeFinalTick]);

  const [secondaryPoi, setSecondaryPoi] = useState<any | null>(null);
  const handleSinglePoiSwap = (poiName: string) => {
    // Never substitute a placeholder city. The previous fallback silently
    // rewrote the user's request to a hardcoded destination, so a swap made
    // before the workspace had a resolved city would ask the planner to work
    // on the wrong place. Ask the user to state the city instead.
    const currentCityName = targetCityInfo?.name;
    if (!currentCityName) {
      setApiError('还没有确定目的地城市，请先描述目的地或完成一次规划后再替换景点');
      return;
    }
    trackPlanningEvent({ trip_id: planningResourceId, event_type: 'plan_swapped', payload: { module: 'route', target: poiName, reason: '用户替换该景点' } });
    const swapPrompt = `保持在【${currentCityName}】不变，仅把行程中的景点【${poiName}】平替换成【${currentCityName}】另一个同等知名度的高评分文旅景点或特色餐厅，绝对不要改变城市和其他天数的规划。`;
    setUserIntent(swapPrompt);
    handleSubmitIntent(swapPrompt);
  };

  const handleNodeVisited = (poi: any) => {
    const key = `${poi?.day || 1}:${poi?.name || ''}`;
    setVisitedNodes((previous) => previous.includes(key) ? previous : [...previous, key]);
    void apiFetch('/api/v1/planning/execution', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trip_id: planningResourceId, plan_id: savedTripId || undefined, node_key: key, status: 'visited', note: `第${poi?.day || 1}天 · ${poi?.name || ''}` }),
    }).catch(() => {});
  };

  const handleVote = (poiName: string, type: 'up' | 'down') => {
    const nextValue = nodeVotes[poiName] === type ? null : type;
    setNodeVotes(prev => ({
      ...prev,
      [poiName]: nextValue as any
    }));
    // 房间内实时同步：把投票事实交给父级房间协同 socket 广播给其他成员。
    // 连接未就绪时只保留本地乐观更新（父级不排队重试）。
    onRoomVote(poiName, nextValue);
    // 上报正/负反馈到进化闭环，形成系统记忆
    if (type === 'down') {
      setFeedbackPoi(poiName);
      setFeedbackReason('');
      return;
    }
    apiFetch('/api/v1/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomCode, user_id: currentUser.id, target: poiName, score: type === 'up' ? 1 : -1, reason: '用户对路线节点评价' })
    }).catch(() => {});
  };

  const submitPoiFeedback = () => {
    if (!feedbackPoi) return;
    void apiFetch('/api/v1/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomCode, user_id: currentUser.id, target: feedbackPoi, score: -1, reason: feedbackReason || '用户不喜欢该地点' }),
    }).catch(() => {});
    setFeedbackPoi(null);
    setFeedbackReason('');
  };

  // 👑 A/B 方案：换一个调性，生成与当前版本不同的 B 方案
  const handleRegenerateVariant = () => {
    // Same reasoning as the single-POI swap: a missing city must be surfaced,
    // not replaced by a hardcoded destination.
    const currentCityName = targetCityInfo?.name;
    if (!currentCityName) {
      setApiError('还没有确定目的地城市，请先完成一次规划后再生成 B 方案');
      return;
    }
    const variantPrompt = `请给【${currentCityName}】规划一个与当前方案不同调性的 B 版本：换一种行程节奏与景点组合（例如更松弛 vs 更紧凑、或更偏爱小众 vs 更主打地标打卡），保持城市与天数不变，重新生成一版全新路线。`;
    setUserIntent(variantPrompt);
    handleSubmitIntent(variantPrompt);
  };

  // 👑 切换后端生成的多套方案（点击即本地即时生效，无需重新推演）
  // 用户"采纳方案"是真实决策信号（事件流里的 user.accepted）。这里只在**用户明确切换方案**时上报：
  // 自动保存路径不报（否则一生成方案就"被采纳"，那是假的）。同一套方案在一次会话里只报一次。
  // 上报失败只记日志：审计是旁路，绝不能因为上报失败影响用户切方案。
  const recordedAcceptanceRef = useRef<Set<string>>(new Set());
  const recordPlanAcceptance = async (variant: any) => {
    if (!variant || !currentUser?.id || !roomCode) return;
    const key = `${variant.name || ''}|${variant.style || ''}`;
    if (recordedAcceptanceRef.current.has(key)) return;
    recordedAcceptanceRef.current.add(key);
    try {
      const route = JSON.stringify(Array.isArray(variant.nodes) ? variant.nodes : []);
      const saved = await apiFetch('/api/v1/plan/variant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ room_id: roomCode, name: variant.name || '未命名方案', style: variant.style || '', route }),
      });
      const savedPayload = await saved.json().catch(() => ({}));
      const variantId = String(savedPayload?.variant?.id || '');
      if (!variantId) return;
      await apiFetch('/api/v1/plan/variant/adopt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ variant_id: variantId, user_id: currentUser.id, trip_id: savedTripId || undefined }),
      });
    } catch (error) {
      console.warn('采纳信号上报失败（不影响使用）', error);
    }
  };

  const handleSelectPlan = (id: string) => {
    const v = planVariants.find((x: any) => x.id === id);
    if (v && Array.isArray(v.nodes) && v.nodes.length > 0) {
      setDynamicRoutes(v.nodes);
      setActivePlanId(id);
      setActiveDayIndex(0);
      setSelectedPoiIndex(0);
      setPlanSwitchNotice(`已切换至「${v.name || '新方案'}」：${v.desc || '路线节点与节奏已更新'}`);
      void recordPlanAcceptance(v);
    }
  };

  // 👑 AI 安全简报生成
  const generateSafetyBrief = async () => {
    if (!safetyInfo || !targetCityInfo) return;
    setBriefLoading(true);
    try {
      const res = await apiFetch('/api/v1/briefing/safety', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          city: targetCityInfo.name,
          cii_score: safetyInfo.cii_score,
          risk_level: safetyInfo.risk_level,
          active_alerts: safetyInfo.active_alerts,
          weather: weatherInfo,
          traffic: trafficInfo,
        }),
      });
      const data = await res.json();
      setSafetyBrief(data.briefing || '无法生成安全简报');
    } catch (e) {
      setSafetyBrief('安全简报服务暂时不可用');
    }
    setBriefLoading(false);
  };

  const runtimeModels = Array.isArray(runtimeHealth?.models) ? runtimeHealth.models : [];
  const runtimeStatusLabel = (status: string) => status === 'healthy' ? '运行正常' : status === 'rollback_required' ? '已触发回滚' : '样本不足';

  return (
    <motion.div key="workspace-root" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="relative h-screen w-full bg-white flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden font-sans text-slate-800">
      
      {/* 左侧长图文游记与路径排版区 */}
      {/* 桌面（≥lg）把 header 和正文放进同一个滚动容器：从标题往下读时头部自然滚走，
          不再常驻 200px；移动端仍保持「头部固定 + 正文自己滚动」，避免溢出遮挡下方地图。 */}
      <div className={`w-full ${mapLayout === 'doc' ? 'lg:flex-1 h-auto lg:h-full' : mapLayout === 'map' ? 'lg:w-[380px] xl:w-[420px] h-[56vh] lg:h-full' : 'lg:w-[620px] xl:w-[700px] h-[56vh] lg:h-full'} min-h-[480px] lg:min-h-0 flex flex-col relative z-20 border-b lg:border-b-0 lg:border-r border-slate-200 bg-white shadow-2xl shrink-0 lg:overflow-y-auto custom-scrollbar`}>
        <header className="px-5 sm:px-8 py-4 border-b border-slate-100 bg-white shadow-xs z-10">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <h2 className="text-2xl font-black text-slate-800 tracking-tight">{targetCityInfo ? targetCityInfo.name + '全景路线规划' : '规划您的行程'}</h2>
              {/* 决策页已是终态：布局收成标题右侧的一枚紧凑胶囊，不再单占一行 */}
              {phase === 'decision' && (
                <div className="flex shrink-0 items-center gap-0.5 rounded-full border border-slate-200 bg-white p-0.5" role="group" aria-label="地图布局">
                  {(Object.keys(MAP_LAYOUT_LABELS) as MapLayout[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      data-testid={`map-layout-${key}`}
                      aria-pressed={mapLayout === key}
                      onClick={() => changeMapLayout(key)}
                      className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold transition-colors ${mapLayout === key ? 'bg-orange-50 text-orange-700' : 'text-slate-500 hover:bg-stone-50 hover:text-orange-600'}`}
                    >
                      {MAP_LAYOUT_LABELS[key]}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <p className="text-xs text-slate-400 font-semibold mt-0.5">路线、预算与出行依据集中在这里</p>
            {/* 跟随房主：成员随时可以看到当前状态并一键切换，不必回到大厅。 */}
            {!isHost && roomCode && (
              <div data-testid="follow-host-bar" className="mt-1.5 flex flex-wrap items-center gap-2">
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${followHost ? 'bg-emerald-50 text-emerald-700' : 'bg-stone-100 text-stone-500'}`}>
                  <Users className="h-3 w-3" />
                  {followHost ? '正在跟随房主' : '未跟随房主'}
                </span>
                <button
                  type="button"
                  onClick={() => onFollowHostChange?.(!followHost)}
                  className="text-[11px] font-bold text-orange-600 hover:underline"
                >
                  {followHost ? '取消跟随' : '跟随房主'}
                </button>
              </div>
            )}
            {/* 目的地是兜底来的 → 必须如实说，不能让用户以为这是自己要的城市。
                走克制的一行小字（不是大色块），与页面既有语气一致。 */}
            {targetCityInfo?.resolution === 'fallback_default' && (
              <p
                data-testid="city-fallback-notice"
                className="mt-1.5 text-xs leading-5 text-stone-500"
              >
                没从您的描述里认出目的地，以下按「{targetCityInfo.name}」生成。
                <span className="text-stone-400"> 若不对，请在上方写明城市重新规划。</span>
              </p>
            )}
          </div>
          {phase === 'decision' && (
            // 工具栏折叠：常用的 4 个留在外面，其余收进「更多」。原来 10 个按钮挤成 3 列，又长又乱。
            <div className="workspace-toolbar relative flex w-full flex-wrap items-center gap-2 sm:w-[430px]" aria-label="行程工具">
              {/* 主行：点其中任何一个都把「更多」菜单收起来（菜单不该一直挂在屏幕上） */}
              <div
                className="flex flex-wrap items-center gap-2"
                role="button"
                tabIndex={0}
                aria-label="收起更多工具"
                onClick={() => setShowMoreTools(false)}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setShowMoreTools(false);
                  }
                }}
              >
              <button data-testid="save-trip" onClick={handleSaveTrip} className={`px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer ${tripSaved ? 'btn-success' : ''}`}>
                {tripSaved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />} {tripSaved ? '已保存' : '保存到我的行程'}
              </button>
              {/* 普通模式只留“保存 + 更多”：导出、日记、预算账本收进菜单，
                  首屏不再一次摆出整排按钮，视觉焦点回到路线本身。 */}
              {experienceMode === 'pro' && (
                <>
                  <button data-testid="open-diary" onClick={() => setShowDiary(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><BookOpen className="w-3.5 h-3.5"/> 行程日记</button>
                  <button data-testid="export-ics" onClick={downloadIcs} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出日历</button>
                  <button onClick={() => setShowBudget(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="打开预算管家"><PiggyBank className="w-3.5 h-3.5"/> 预算账本</button>
                </>
              )}
              </div>
              <button
                data-testid="toggle-more-tools"
                onClick={() => setShowMoreTools((open) => !open)}
                aria-expanded={showMoreTools}
                className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"
              >
                <MoreHorizontal className="w-3.5 h-3.5" /> {showMoreTools ? '收起' : '更多'}
              </button>
              {showMoreTools && (
                <div
                  // 点菜单里任何一项都收起（捕获阶段先关，再执行按钮自己的动作）
                  role="button"
                  tabIndex={0}
                  aria-label="更多工具菜单"
                  onClick={() => setShowMoreTools(false)}
                  onKeyDown={(event) => {
                    if (event.target !== event.currentTarget) return;
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setShowMoreTools(false);
                    }
                  }}
                  className="absolute right-0 top-full z-50 mt-2 grid w-[330px] grid-cols-2 gap-2 rounded-2xl border border-stone-200 bg-white/98 p-3 shadow-xl backdrop-blur"
                >
                  <button onClick={handleRegenerateVariant} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Shuffle className="w-3.5 h-3.5"/> 换一版
                  </button>
                  <button onClick={() => setShowPoster(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Share2 className="w-3.5 h-3.5"/> 行程海报
                  </button>
                  <button data-testid="export-json" onClick={downloadJson} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出 JSON</button>
                  <button onClick={() => setShowExpenseReport(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="查看消费复盘"><TrendingDown className="w-3.5 h-3.5"/> 消费复盘</button>
                  <button onClick={() => setShowSatisfaction(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="提交行程满意度"><Star className="w-3.5 h-3.5"/> 行程评价</button>
                  <button onClick={() => setShowBookingHub(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="打开官方渠道"><ExternalLink className="w-3.5 h-3.5"/> 官方渠道</button>
                  {experienceMode === 'simple' && (
                    <>
                      <button data-testid="open-diary" onClick={() => setShowDiary(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><BookOpen className="w-3.5 h-3.5"/> 行程日记</button>
                      <button data-testid="export-ics" onClick={downloadIcs} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出日历</button>
                      <button onClick={() => setShowBudget(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="打开预算管家"><PiggyBank className="w-3.5 h-3.5"/> 预算账本</button>
                    </>
                  )}
                  <button onClick={() => { setPhase('drafting'); setIntentsReady(false); setShowMoreTools(false); }} className="btn-accent col-span-2 px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center justify-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Wand2 className="w-3.5 h-3.5"/> 重新调整
                  </button>
                </div>
              )}
            </div>
          )}
          </div>
          {/* 决策页布局已收进标题行，步骤条也已完成使命：两者都不再占用头部高度 */}
          {phase !== 'decision' && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="地图布局">
            <span className="text-[11px] font-bold text-slate-400">布局</span>
            {(Object.keys(MAP_LAYOUT_LABELS) as MapLayout[]).map((key) => (
              <button
                key={key}
                type="button"
                data-testid={`map-layout-${key}`}
                aria-pressed={mapLayout === key}
                onClick={() => changeMapLayout(key)}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-bold transition-colors ${mapLayout === key ? 'border-orange-200 bg-orange-50 text-orange-700' : 'border-slate-200 bg-white text-slate-500 hover:border-orange-200 hover:text-orange-600'}`}
              >
                {MAP_LAYOUT_LABELS[key]}
              </button>
            ))}
            {mapLayout === 'doc' && <span className="ml-auto hidden text-[11px] font-medium text-slate-400 sm:inline">地图已折叠，需要看空间关系时再展开</span>}
          </div>
          )}
          {phase !== 'decision' && (
          <div className="mt-4 grid grid-cols-3 gap-2" aria-label="规划进度">
            {([
              ['drafting', '01', '描述需求'],
              ['deduction', '02', '生成方案'],
              ['decision', '03', '确认行程'],
            ] as const).map(([key, index, label]) => {
              const active = phase === key;
              const complete = key === 'drafting' && phase !== 'drafting';
              return <div key={key} className={`relative flex items-center gap-2 rounded-xl px-3 py-2 border transition-colors ${active ? 'bg-orange-50 border-orange-200 text-orange-700' : complete ? 'bg-stone-100 border-stone-200 text-stone-500' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                <span className="font-mono text-[11px] font-black">{complete ? '✓' : index}</span><span className="text-xs font-bold truncate">{label}</span>
              </div>;
            })}
          </div>
          )}
          {/* 降级必须说清楚，而不只是状态栏里一个小标签：
              用户看到的是「方案已生成」，如果不说原因，会把候选池合成当成智能体推演结果。 */}
          {phase === 'decision' && isFallbackRoute && (
            <div
              data-testid="fallback-route-notice"
              role="status"
              className="mt-3 rounded-lg border border-warn bg-warn-soft px-4 py-3"
            >
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-bold text-warn">本轮是保底路线，不是多智能体推演结果</p>
                  <p className="mt-1 break-words text-xs leading-5 text-ink-2">
                    {fallbackReason || 'AI 规划服务当前不可用，路线已改由真实候选 POI 合成。'}
                  </p>
                  <p className="mt-1 text-[11px] leading-4 text-ink-3">
                    行程仍可查看、编辑与执行；服务恢复后可重新尝试智能规划。
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleRetryDeduction}
                  className="shrink-0 rounded-md border border-line bg-paper-card px-2.5 py-1.5 text-[11px] font-bold text-ink-2 transition-colors hover:border-warn hover:text-warn"
                >
                  重新尝试
                </button>
              </div>
            </div>
          )}
          {phase === 'decision' && experienceMode === 'simple' && (
            <div>
              <TripOverviewCard
                dayLabel={`第 ${totalDays[activeDayIndex] || 1} 天`}
                dayRoutes={currentDayRoutes}
                totalDays={totalDays.length}
                budgetTotal={overviewBudgetTotal}
                budgetEstimated={planQuality.costUnavailable ? 0 : planQuality.estimatedCost}
                unpricedCount={planQuality.unpricedCount}
                budgetBreakdown={budgetBreakdown}
                memberCount={roomMembers.length || 1}
                transportMinutes={overviewTransportMinutes}
                riskHeadline={riskAlert?.perturbation?.headline || riskAlert?.changes?.[0] || '本次行程未触发风险提醒'}
                riskDisclosure={riskAlert ? (riskAlert.perturbation?.disclosure || '已收到风险变更，确认前不会改动行程') : '天气、路况与安全均未触发提醒'}
                isFallbackRoute={isFallbackRoute}
                onEdit={() => { setPhase('drafting'); setIntentsReady(false); }}
              />
              {/* 超支时不只是报缺口：给出真实报价里"先动哪里"的选项，重排后核对是否压进预算。
                  预算改高后缺口归零，面板保留结论（选项自动收起）并明确说替代项不用再执行。 */}
              {(overageShortfall > 0 || overagePending != null || overageResult != null || budgetPending != null || budgetResult != null || combinedPending != null || combinedResult != null) && (
                <OverageAlternativesPanel
                  nodes={dynamicRoutes}
                  budget={overageBudget}
                  shortfall={overageShortfall}
                  pending={overagePending}
                  result={overageResult}
                  budgetPending={budgetPending}
                  budgetResult={budgetResult}
                  combinedPending={combinedPending}
                  combinedResult={combinedResult}
                  combinedRetry={combinedRetry}
                  busy={overagePending != null || budgetPending != null || combinedPending != null || swappingDay != null || pendingScope != null}
                  onChoose={handleOverageOption}
                  onChangeBudget={handleOverageBudgetChange}
                  onCombine={handleCombineOverage}
                  onRetry={handleCombineOverage}
                />
              )}
              <button
                type="button"
                data-testid="toggle-route-evidence"
                aria-expanded={showRouteEvidence}
                onClick={() => setShowRouteEvidence((open) => !open)}
                className="mt-2 inline-flex min-h-8 items-center gap-1 rounded-lg border border-stone-200 bg-white px-2.5 text-xs font-bold text-stone-600 transition-colors hover:bg-stone-50"
              >
                {showRouteEvidence ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                为什么这样安排
              </button>
            </div>
          )}
          {phase === 'decision' && (experienceMode === 'pro' || showRouteEvidence) && (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-bold text-slate-500" aria-label="路书质量摘要">
              <span><strong className="text-slate-800">{planQuality.days}</strong> 天 · <strong className="text-slate-800">{planQuality.nodes}</strong> 个节点</span>
              {/* 花费只认一个来源：后端给了三级预算报告（已核实/估算/未核实）时，
                  这里不再重复显示前端自行汇总的"估算总额"，避免同屏出现两个不同金额。 */}
              {!planGovernance?.budget && (
                planQuality.costUnavailable ? (
                  <span className="text-amber-700" title="供应商未返回价格，无法给出估算总额">
                    价格待补充<strong className="ml-1">（{planQuality.unpricedCount} 个节点无报价）</strong>
                  </span>
                ) : (
                  <span>
                    估算 ¥<strong className="text-orange-700">{planQuality.estimatedCost.toLocaleString()}</strong>
                    {planQuality.unpricedCount > 0 && (
                      <span className="ml-1 font-medium text-slate-400" title="这些节点缺少供应商报价，未计入合计">
                        · {planQuality.unpricedCount} 个未计价
                      </span>
                    )}
                  </span>
                )
              )}
              <span>坐标覆盖 <strong className={planQuality.coverage >= 80 ? 'text-stone-800' : 'text-amber-700'}>{planQuality.coverage}%</strong></span>
              <span>共识指数 <strong className={planQuality.consensus >= 70 ? 'text-stone-800' : 'text-amber-700'}>{planQuality.consensus}</strong></span>
              {/* 保底路线必须一眼看出来：大模型不可用时方案是候选池合成的，
                  不能让它看起来像正常的智能体推演结果（后端同时会在核对面板里登记这条降级）。 */}
              {isFallbackRoute && (
                <span
                  className="text-amber-700"
                  data-testid="fallback-route-badge"
                  title="大模型暂时不可用时，方案改由高德候选池合成（真实 POI，但不是自动规划结果）"
                >
                  保底路线 <strong>（系统兜底）</strong>
                </span>
              )}
              {/* Make the learning step visible: which style the policy picked,
                  and whether it was exploring or exploiting. Without this the
                  bandit's decisions were invisible in the product. */}
              {banditDecision && (
                <span
                  className="text-stone-500"
                  title={[
                    banditDecision.explored ? '本次尝试了一种新策略' : '本次沿用了历史表现更好的策略',
                    banditDecision.propensity !== undefined
                      ? `该方案被选中的概率约 ${(banditDecision.propensity * 100).toFixed(1)}%`
                      : '',
                  ].filter(Boolean).join('；')}
                >
                  推荐策略
                  <strong className="ml-1">{banditArmId ? banditArmId.split(':').slice(-1)[0] : '—'}</strong>
                </span>
              )}
              <RoomConnectionStatus connection={roomConnection} memberCount={roomMembers.length} onRetry={onRetryRoom} compact testId="room-sync" />
              {/* Probabilistic digital twin: show the sampled spread rather than a
                  single point estimate, so the user can see how much slack a day
                  actually has. */}
              {simulation?.total_minutes?.p50 ? (
                <span
                  className="text-stone-600"
                  title={`基于 ${simulation.assumptions?.sample_count ?? 0} 次蒙特卡洛采样；P90 表示 10% 的情形会超过该时长`}
                >
                  时长 P50 <strong>{Math.round((simulation.total_minutes.p50 ?? 0) / 60)}</strong>h
                  {' / '}P90 <strong>{Math.round((simulation.total_minutes.p90 ?? 0) / 60)}</strong>h
                </span>
              ) : null}
              {typeof simulation?.budget_overrun_probability === 'number' ? (
                <span
                  className={simulation.budget_overrun_probability > 0.3 ? 'text-rose-700' : 'text-slate-500'}
                  title="按采样得到的费用分布，超出预算的概率"
                >
                  超预算概率 <strong>{Math.round(simulation.budget_overrun_probability * 100)}%</strong>
                </span>
              ) : null}
              {simulation?.assumptions && (simulation.assumptions.unpriced_nodes ?? 0) > 0 ? (
                <span
                  className="text-amber-700"
                  title="这些节点缺少供应商报价，费用分布未包含它们"
                >
                  {simulation.assumptions.unpriced_nodes} 个节点未计价
                </span>
              ) : null}
              {planQuality.dailyCounts.length > 0 && <span className="text-slate-400">白天节点 {planQuality.dailyCounts.map((item: any) => `D${item.day}:${item.count}`).join(' · ')}</span>}
            </div>
          )}
          {(roomConnection === 'reconnecting' || roomConnection === 'offline') && (
            <div data-testid="room-offline-banner" className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <RoomConnectionStatus connection={roomConnection} retryAttempt={roomRetryAttempt} retryDelayMs={roomRetryDelayMs} onRetry={onRetryRoom} testId="room-banner" />
            </div>
          )}
          {phase === 'decision' && (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <nav className="flex min-w-0 flex-1 items-center gap-1 rounded-xl border border-stone-200 bg-stone-50 p-1" aria-label="工作区层级">
              {layerTabs.map(([key, label, hint]) => (
                <button
                  key={key}
                  type="button"
                  data-testid={`workspace-layer-${key}`}
                  aria-pressed={workspaceLayer === key}
                  onClick={() => { setWorkspaceLayer(key); setShowWorkspaceMenu(false); }}
                  className={`min-h-10 flex-1 rounded-lg px-3 py-1.5 text-left transition-colors ${workspaceLayer === key ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200' : 'text-stone-500 hover:bg-white/70'}`}
                >
                  <span className="block text-xs font-black">{label}</span>
                  <span className="mt-0.5 hidden truncate text-[11px] font-medium text-stone-400 sm:block">{hint}</span>
                </button>
              ))}
              {experienceMode === 'pro' && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  data-testid="workspace-layer-more"
                  aria-expanded={showWorkspaceMenu}
                  onClick={() => setShowWorkspaceMenu((open) => !open)}
                  className={`min-h-10 rounded-lg px-3 py-1.5 text-left transition-colors ${workspaceLayer === 'professional' ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200' : 'text-stone-500 hover:bg-white/70'}`}
                >
                  <span className="flex items-center gap-1 text-xs font-black"><MoreHorizontal className="h-3.5 w-3.5" />更多</span>
                  <span className="mt-0.5 hidden text-[11px] font-medium text-stone-400 sm:block">高级信息</span>
                </button>
                {showWorkspaceMenu && (
                  <div className="absolute right-0 top-full z-40 mt-2 w-52 rounded-xl border border-stone-200 bg-white p-1.5 shadow-xl">
                    <button
                      type="button"
                      data-testid="workspace-layer-professional"
                      onClick={() => { setWorkspaceLayer('professional'); setShowWorkspaceMenu(false); }}
                      className="w-full rounded-lg px-3 py-2 text-left hover:bg-stone-50"
                    >
                      <span className="block text-xs font-black text-stone-800">方案检查</span>
                      <span className="mt-0.5 block text-[11px] leading-4 text-stone-500">成员体验、策略依据与系统状态</span>
                    </button>
                  </div>
                )}
              </div>
              )}
              </nav>
              <div className="flex shrink-0 items-center gap-0.5 rounded-xl border border-stone-200 bg-white p-1" role="group" aria-label="信息密度">
                <button
                  type="button"
                  data-testid="experience-mode-simple"
                  aria-pressed={experienceMode === 'simple'}
                  title="只看路线、预算、交通、风险和保存"
                  onClick={() => changeExperienceMode('simple')}
                  className={`min-h-8 rounded-lg px-2.5 text-xs font-bold transition-colors ${experienceMode === 'simple' ? 'bg-stone-900 text-white' : 'text-stone-500 hover:bg-stone-50'}`}
                >
                  普通
                </button>
                <button
                  type="button"
                  data-testid="experience-mode-pro"
                  aria-pressed={experienceMode === 'pro'}
                  title="展开依据、方案对比与高级信息"
                  onClick={() => changeExperienceMode('pro')}
                  className={`min-h-8 rounded-lg px-2.5 text-xs font-bold transition-colors ${experienceMode === 'pro' ? 'bg-stone-900 text-white' : 'text-stone-500 hover:bg-stone-50'}`}
                >
                  专业
                </button>
              </div>
            </div>
          )}
        </header>

        {/* 文档优先布局下的阅读宽度要分阶段给（2026-11-02 实测 1024 宽屏）：
            · 写需求（drafting）是**表单**，不是长文 —— 680px 会把整页挤成中间一条，
              左右各空出 170px 的空白；表单按两栏铺开更舒服，所以放到 1240px；
            · 读方案（decision）仍是正文，限制在 880px（中文 ~55 字/行），
              只是把外边距收窄一点，别让首屏看着像"两边被裁掉了"。 */}
        <div className={`mx-auto w-full flex-1 overflow-y-auto bg-white custom-scrollbar relative lg:flex-none lg:overflow-visible ${phase === 'drafting' ? 'max-w-[1240px]' : phase === 'deduction' ? 'max-w-[860px]' : 'max-w-[880px]'}`}>
          {/* 核对说明是"要读的内容"，不是工具栏：放进滚动区最上面，跟行程正文一起滚。
              以前它挂在 header 里，而 header 是 auto 高度、不可滚动，所以面板一长
              （预算核对/已作调整/行程规模/本次调整/价格核对/跨城怎么走/长途分段/还需要你留意
              共 8 段）就把整列顶到 1200px 以上：正文区被压成 0 高、面板最后几段滚不到
              （1440×900 实测 header 1210px、正文 clientHeight 0、文档不可滚）。 */}
          {/* 一键换近点的即时反馈：**放在阶段判断之外** —— 点下去之后界面会立刻回到
              "推演中"（phase=drafting），如果这条提示只在 decision 阶段渲染，用户就永远看不到它。 */}
          {swappingDay != null && (
            <div className="px-5 pt-2 sm:px-8">
              <p className="text-[12px] font-bold text-orange-700" data-testid="swap-near-pending">
                已按你的要求换{typeof swappingDay === 'string' ? swappingDay : `第 ${swappingDay} 天的远点`}，正在重排…
              </p>
            </div>
          )}
          {phase === 'decision' && workspaceLayer === 'evidence' && (
            <div className="px-5 pt-2 sm:px-8">
              <PlanGovernancePanel data={planGovernance} onSwapNear={handleSwapNear} onSwapHotel={handleSwapHotel} />
            </div>
          )}
          <AnimatePresence mode="wait">
            {phase === 'drafting' && (
              <>
                {/* 参考路线（可选）：**必须在规划之前** —— 它的用途是"用户没提要求时按它排"，
                    放到规划页就变成了"先排完再用"，顺序反了。默认折叠，不挤占主诉求输入。 */}
                <div className="px-4 pt-4 sm:px-8">
                  <ReferenceRoutePanel />
                </div>
                {/* 房主在场时成员不能单独规划：方案由房主统一生成后同步过来 */}
                {memberFollowsHost && (
                  <div data-testid="follow-waiting-notice" className="mx-4 mt-4 flex items-center gap-2 rounded-xl border border-orange-200 bg-orange-50/70 px-3 py-2.5 text-xs font-semibold text-orange-700 sm:mx-8">
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    方案由房主统一生成，完成后会自动同步到这里。
                  </div>
                )}
                <DraftingPanel 
                  key="workspace-drafting-panel"
                  activeMode={activeMode} 
                  activeRole={activeRole} 
                  onModeChange={setActiveMode} 
                  onRoleChange={setActiveRole} 
                  onSubmit={handleSubmitIntent} 
                  isReady={intentsReady} 
                  userIntent={userIntent} 
                  setUserIntent={setUserIntent} 
                  apiError={apiError} 
                  historyLength={incrementalHistory.length}
                  roomMembers={roomMembers}
                  compact={mapLayout !== 'doc'}
                />
              </>
            )}
            {phase === 'deduction' && (
              <DeductionPanel 
                key="workspace-deduction-panel" 
                latestLog={humanReadableLogs.split('\n').filter(Boolean).pop()}
                error={deductionError}
                timedOut={deductionTimeout}
                elapsedSeconds={deductionElapsedSeconds}
                onRetry={handleRetryDeduction}
                onCancel={handleCancelDeduction}
                onBack={handleResetIntent}
              />
            )}
            {phase === 'decision' && (
              <motion.div key="workspace-decision-panel" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="px-4 pt-4 sm:px-8">
                  {workspaceLayer === 'execution' && <>
                    <RiskPerturbationPanel alert={riskAlert} />
                    <TripExecutionPanel
                      tripId={planningResourceId}
                      planId={savedTripId || undefined}
                      routes={dynamicRoutes}
                      onTodayDayChange={setExecutionTodayDay}
                      onRequestReplan={(node) => {
                        setUserIntent(`保持未完成节点不变，仅因“${node.name}”执行状态变化而重排剩余路线，目标是最小修改。`);
                        setPhase('drafting');
                        setIntentsReady(false);
                      }}
                    />
                    <ReplanProposalPanel
                      proposal={replanProposal}
                      busy={replanBusy}
                      onApply={(candidate) => { void applyReplanCandidate(candidate); }}
                      onReject={() => { void rejectReplanProposal(); }}
                    />
                    <FieldAssistPanel
                      tripId={planningResourceId}
                      destination={targetCityInfo?.name || initialDestination || ''}
                      routes={dynamicRoutes}
                    />
                  </>}
                  {workspaceLayer === 'plan' && experienceMode === 'simple' && (
                    <button
                      type="button"
                      data-testid="toggle-plan-details"
                      aria-expanded={showPlanDetails}
                      onClick={() => setShowPlanDetails((open) => !open)}
                      className="mb-3 inline-flex min-h-8 items-center gap-1 rounded-lg border border-stone-200 bg-white px-2.5 text-xs font-bold text-stone-600 transition-colors hover:bg-stone-50"
                    >
                      {showPlanDetails ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      展开专业信息（行前清单 / 文化背景 / 无障碍）
                    </button>
                  )}
                  {workspaceLayer === 'plan' && (experienceMode === 'pro' || showPlanDetails) && <PreTripPlanningPanel
                      userId={currentUser.id}
                      tripId={planningResourceId}
                      destination={targetCityInfo?.name || initialDestination || ''}
                      baseNodes={currentDayRoutes}
                      budget={Number(budgetData?.total_budget || 0)}
                      days={totalDays.length}
                      travelers={Math.max(1, roomMembers.length)}
                    />}
                  {workspaceLayer === 'professional' && <section className="mb-4 rounded-2xl border border-stone-200 bg-[#fbfaf7] p-4 shadow-[0_8px_30px_rgba(67,45,25,0.06)]" data-testid="professional-diagnostics">
                    <div className="flex items-start justify-between gap-3">
                      <div><h3 className="text-sm font-black text-slate-800">方案检查</h3><p className="mt-1 text-[11px] text-slate-500">这里回答三个问题：路线是否可执行、谁可能不满意、下一步要不要调整。</p></div>
                      <button type="button" onClick={() => setShowBlackboard(true)} className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-[11px] font-bold text-stone-700 hover:bg-stone-50">查看取舍记录</button>
                    </div>
                    <div className="mt-3 grid gap-2 sm:grid-cols-3">
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">最低成员体验</p><p className="mt-1 text-lg font-black text-slate-800">{fairnessAudit?.minimum_satisfaction != null ? `${Math.round(Number(fairnessAudit.minimum_satisfaction) * 100)}%` : '尚未评估'}</p><p className="mt-1 text-[10px] text-slate-400">越低的成员越需要优先照顾</p></div>
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">路线是否均衡</p><p className={`mt-1 text-sm font-black ${fairnessAudit?.threshold_exceeded ? 'text-rose-600' : 'text-emerald-700'}`}>{fairnessAudit ? (fairnessAudit.threshold_exceeded ? '需要调整' : '当前可接受') : '尚未评估'}</p><p className="mt-1 text-[10px] text-slate-400">比较成员之间的体验差距</p></div>
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">历史依据</p><p className="mt-1 text-sm font-black text-slate-800">{banditDecision ? (banditDecision.explored ? '正在试用新策略' : '优先使用稳定策略') : '样本不足'}</p><p className="mt-1 text-[10px] text-slate-400">样本不足时不会宣称“已经学会”</p></div>
                    </div>
                    {/* 效果指标：把“多智能体”叙事换成可核对的数字；算不出来的如实写样本不足/未采集。 */}
                    <div className="mt-3 rounded-xl border border-slate-200 bg-white p-3" data-testid="effect-metrics">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[10px] font-black text-slate-500">效果指标</p>
                        <span className="text-[9px] text-slate-400">只展示能算出来的数；没有样本的如实标注</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-4">
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">方案采纳率（30 天）<span className="ml-1 font-black text-slate-800">{effectAdoptionRate != null ? `${effectAdoptionRate}%` : adoptionRate != null ? `${adoptionRate}%` : '样本不足'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">成员最低满意度<span className="ml-1 font-black text-slate-800">{minSatisfaction != null ? `${minSatisfaction}%` : '尚未评估'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">争议轮数减少<span className="ml-1 font-black text-slate-800">{conflictReduction != null ? `${conflictReduction}%` : negotiationRounds.length > 0 ? '本轮无分歧可减少' : '本轮无多轮协商'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">重规划保留节点<span className="ml-1 font-black text-slate-800">{preservationRate != null ? `${preservationRate}%` : effectPreservationRate != null ? `${effectPreservationRate}%（30 天）` : '重规划后显示'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">预算超支率（记账）<span className="ml-1 font-black text-slate-800">{effectOverrunRate != null ? `${effectOverrunRate}%` : effectOverrunTrips === 0 ? '还没有可比预算' : '样本不足'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">无法识别字段<span className="ml-1 font-black text-slate-800">{enumInvalidRate != null ? `${enumInvalidRate}%` : '样本不足'}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">风险响应时间（30 天）<span className="ml-1 font-black text-slate-800">{effectRiskTimeText}</span></div>
                        <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">用户修改次数（30 天）<span className="ml-1 font-black text-slate-800">{effectEditTotal != null ? `${effectEditTotal} 次` : '暂无记录'}</span></div>
                      </div>
                      <p className="mt-2 text-[9px] leading-4 text-slate-400">
                        累计指标只统计近 30 天已落库的事实：采纳率按“生成 → 采纳”事件，预算超支率按“设了预算且有记账”的行程实际支出对比，风险响应时间是通知从推送到你处理的中位数，用户修改次数只数你主动改方案（不含系统执行）。「无法识别字段」是供应商返回中未能识别、未参与决策的比例，不等同于“未知数据识别准确率”——后者需要标注真值后才能计算，样本不足时不编造。
                      </p>
                    </div>
                    <div className="mt-3 rounded-xl border border-[#e9d8c5] bg-[#fff8ef] px-3 py-2.5 text-[11px] leading-5 text-[#6f4527]">
                      <span className="font-black">你可以直接这样调整：</span> 换成更省钱、减少每天节点、优先某位成员，或打开路线节点查看来源与替代项。技术指标只放在下面的“高级诊断”里，不影响普通使用。
                    </div>
                    {/* 协商纪要 + 取价出处：属于"系统怎么想的"这类诊断信息，
                        所以放在专业诊断层（按需展开），而不是行程核对面板里。 */}
                    <NegotiationDiagnostics
                      negotiation={planGovernance?.negotiation as any}
                      priceAudit={planGovernance?.priceAudit as any}
                    />
                    {/* 统一事实记录（闭环第①段）：请求/约束/供应商/决策/结果/缺口，
                        每条都带来源与核实状态 —— "大家看的是同一份事实吗"要看这里。 */}
                    <PlanFactsPanel facts={planGovernance?.facts as any} />
                    <div className="mt-4 border-t border-slate-200 pt-3">
                      <button type="button" data-testid="toggle-advanced-diagnostics" aria-expanded={showAdvancedDiagnostics} onClick={() => setShowAdvancedDiagnostics((open) => !open)} className="flex w-full items-center justify-between gap-2 rounded-lg px-1 py-1 text-left transition-colors hover:bg-slate-100">
                        <span className="flex items-center gap-1.5 text-[11px] font-black text-slate-700">{showAdvancedDiagnostics ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}高级诊断</span>
                        <span className="text-[10px] text-slate-400">{agentDiagnosticsLoading ? '同步中' : `${agentCapabilities.length} 个专业模块`}</span>
                      </button>
                      {showAdvancedDiagnostics && <div className="mt-2">
                      {agentCapabilities.length > 0 ? <div className="grid gap-2 sm:grid-cols-2">
                        {agentCapabilities.map((capability: any) => {
                          const runtime = agentEvaluation[capability.agent_id] || agentEvaluation[capability.domain];
                          const events = Number(runtime?.events || 0);
                          const errors = Number(runtime?.errors || 0);
                          const status = events === 0 ? '无样本' : errors > 0 ? '有错误事件' : '有运行记录';
                          const statusClass = events === 0 ? 'text-amber-700 bg-amber-50' : errors > 0 ? 'text-rose-700 bg-rose-50' : 'text-emerald-700 bg-emerald-50';
                          return <article key={capability.agent_id} className="rounded-lg border border-slate-200 bg-white p-3" data-testid={`agent-capability-${capability.agent_id}`}>
                            <div className="flex items-start justify-between gap-2"><div><h4 className="text-xs font-black text-slate-800">{capability.label || capability.agent_id}</h4><p className="mt-1 text-[10px] leading-4 text-slate-500">{capability.responsibility}</p></div><span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold ${statusClass}`}>{status}</span></div>
                            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-slate-400"><span>超时 {Math.round(Number(capability.timeout_ms || 0) / 1000)}s</span><span>事件 {events}</span><span>错误 {errors}</span></div>
                            <p className="mt-1 text-[9px] text-slate-400">降级：{capability.fallback || '未声明'} · 证据：{capability.evidence_required ? '必需' : '可选'}</p>
                          </article>;
                        })}
                      </div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-700">能力矩阵暂不可用或尚无运行样本，当前不显示稳定性结论。</p>}
                      <button type="button" data-testid="toggle-agent-runtime-detail" aria-expanded={showAgentRuntimeDetail} onClick={() => setShowAgentRuntimeDetail((open) => !open)} className="mt-3 flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-left transition-colors hover:bg-slate-50">
                        <span className="flex items-center gap-1.5 text-[10px] font-black text-slate-600">{showAgentRuntimeDetail ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}规划运行明细</span>
                        <span className="text-[9px] text-slate-400">{agentContractAudits.length + (specialistRuntime ? 1 : 0)} 项</span>
                      </button>
                      {showAgentRuntimeDetail && <div>
                      {agentContractAudits.length > 0 && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="agent-contract-audit"><p className="text-[10px] font-bold text-slate-500">本次方案提案审计</p><div className="mt-2 flex flex-wrap gap-2">{agentContractAudits.map((audit: any) => <span key={audit.agent_id || audit.domain} className={`rounded px-2 py-1 text-[10px] font-bold ${audit.status === 'ok' ? 'bg-emerald-50 text-emerald-700' : audit.status === 'degraded' ? 'bg-amber-50 text-amber-700' : 'bg-rose-50 text-rose-700'}`}>{audit.agent_id || '提案'} · {audit.status} · 证据 {audit.evidence_count ?? 0}</span>)}</div></div>}
                      {specialistRuntime && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="specialist-runtime-summary">
                        <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-bold text-slate-500">本次专业协作</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${specialistRuntime.status === 'ok' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{specialistRuntime.status === 'ok' ? '全部完成' : '部分降级'}</span></div>
                        <p className="mt-1.5 text-[11px] text-slate-600">交通、住宿、餐饮、风险和预算分别执行，共完成 {specialistRuntime.completed ?? 0} 项；降级 {specialistRuntime.degraded ?? 0} 项。</p>
                        <p className="mt-1 text-[10px] text-slate-400">专业提案先经过来源与格式校验，再进入路线约束和公平性校验。</p>
                      </div>}
                      </div>}
                      {optimizationAudit && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="pareto-audit-summary">
                        <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-bold text-slate-500">候选方案对比</p><span className="text-[9px] text-slate-400">{optimizationAudit.method || 'Pareto'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]"><div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">保留前沿 <strong className="ml-1 text-emerald-700">{optimizationAudit.frontier_count ?? 0}</strong></div><div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">被淘汰 <strong className="ml-1 text-slate-700">{optimizationAudit.rejected_count ?? 0}</strong></div></div>
                        {Array.isArray(optimizationAudit.rejected) && optimizationAudit.rejected.length > 0 && <div className="mt-2 space-y-1">{optimizationAudit.rejected.slice(0, 4).map((item: any, index: number) => <p key={index} className="text-[10px] text-slate-500">{item.candidate || '候选'}：{item.reason === 'dominated' ? `被 ${Array.isArray(item.dominated_by) ? item.dominated_by.join('、') : '更优候选'} 支配` : item.reason || '未通过门禁'}</p>)}</div>}
                      </div>}
                      {fairnessAudit?.repair_attempted && <p className={`mt-3 rounded-lg border px-3 py-2 text-[10px] ${fairnessAudit.threshold_exceeded ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{fairnessAudit.threshold_exceeded ? '已尝试在候选方案中重新选路，但仍没有满足公平阈值的方案；请调整成员偏好或约束。' : '已通过公平性重选路。'}</p>}
                      <div className="mt-3 grid gap-3 lg:grid-cols-2">
                        <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="feature-snapshot-summary">
                          <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">特征分层</p><span className="text-[9px] text-slate-400">{featureSnapshot?.feature_version || 'features-v1'}</span></div>
                          <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px]">
                            {['long_term', 'journey', 'realtime', 'behavior'].map((key) => <div key={key} className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">{key === 'long_term' ? '长期画像' : key === 'journey' ? '当前旅程' : key === 'realtime' ? '实时信号' : '行为统计'}<span className="ml-1 text-slate-400">{key === 'realtime' ? (featureSnapshot?.groups?.realtime?.status || '未持久化') : featureSnapshot?.groups?.[key] ? '可查看' : '待采集'}</span></div>)}
                          </div>
                          <p className="mt-2 text-[9px] leading-4 text-slate-400">原始反馈与提示词不直接暴露，实时天气、客流和延误仅接受供应商快照。</p>
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="provider-registry-summary">
                          <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">供应商字段注册</p><span className="text-[9px] text-slate-400">{providerRegistry.length} 个领域</span></div>
                          <div className="mt-2 flex flex-wrap gap-1.5">{providerRegistry.map((provider: any) => <span key={provider.kind} className={`rounded px-2 py-1 text-[9px] font-bold ${provider.configured ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{provider.kind} · {provider.configured ? provider.provider : '未配置'}</span>)}</div>
                          <p className="mt-2 text-[9px] leading-4 text-slate-400">字段必须带来源、observed_at 和估算标记，注册表不返回密钥或原始响应。</p>
                          <div className="mt-3 border-t border-slate-100 pt-3" data-testid="provider-quality-summary">
                            <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-black text-slate-500">字段质量快照</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${providerQuality?.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{providerQuality?.status === 'available' ? `近 ${providerQuality?.window_days || 7} 天` : '暂无探测样本'}</span></div>
                            {Array.isArray(providerQuality?.providers) && providerQuality.providers.length > 0 ? <div className="mt-2 space-y-1.5">{providerQuality.providers.slice(0, 6).map((item: any) => {
                              const coverage = item.coverage || {};
                              const factCoverage = Number(coverage.fact_usable || 0);
                              const sourceCoverage = Number(coverage.source || 0);
                              const decisionGate = item.decision_gate || {};
                              const decisionGateAvailable = decisionGate.status === 'available';
                              const eligibleCount = Number(decisionGate.eligible_count || 0);
                              const blockedCount = Number(decisionGate.blocked_count || 0);
                              const enumInvalidRatio = Number(item.enum_invalid_ratio || 0);
                              const freshness = Number(item.stale_ratio || 0) > 0 ? '有过期' : Number(item.estimated_ratio || 0) > 0 ? '含估算' : '新鲜度正常';
                              return <div key={`${item.kind}-${item.provider}`} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] text-slate-600"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">{item.kind} · {item.provider}</span><span className={item.last_status === 'ok' ? 'text-emerald-700' : 'text-amber-700'}>{item.last_status === 'ok' ? '最近成功' : '最近无返回'}</span></div><div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-500"><span>候选 {item.candidate_count ?? 0}</span><span>来源 {Math.round(sourceCoverage * 100)}%</span><span>可用事实 {Math.round(factCoverage * 100)}%</span><span>实时决策 {decisionGateAvailable ? `${eligibleCount}/${eligibleCount + blockedCount || item.candidate_count || 0}` : '未采集'}</span><span className={enumInvalidRatio > 0 ? 'font-bold text-amber-700' : ''}>枚举异常 {Math.round(enumInvalidRatio * 100)}%</span><span>{freshness}</span>{item.mean_latency_ms != null && <span>延迟 {Math.round(Number(item.mean_latency_ms))}ms</span>}</div>{decisionGateAvailable && blockedCount > 0 && <p className="mt-1 text-amber-700">{blockedCount} 个候选缺少新鲜、带观测时间的关键字段，仅保留官方核验入口。</p>}{enumInvalidRatio > 0 && <p className="mt-1 text-amber-700">部分天气、客流或可用状态无法识别，未参与实时事实决策。</p>}{item.last_error_code && <p className="mt-1 text-rose-600">状态码：{item.last_error_code}</p>}</div>;
                            })}</div> : <p className="mt-2 rounded bg-amber-50 px-2 py-1.5 text-[9px] text-amber-700">尚未产生供应商质量事件；配置供应商并完成一次查询后，这里会显示字段覆盖率和新鲜度。</p>}
                            <p className="mt-2 text-[9px] leading-4 text-slate-400">只保存聚合计数，不保存供应商原始响应、URL 查询参数或凭据。</p>
                            {Array.isArray(providerQualityTrend?.points) && providerQualityTrend.points.length > 0 && <div className="mt-3 border-t border-slate-100 pt-3" data-testid="provider-quality-trend">
                              <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-black text-slate-500">来源质量趋势</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${providerQualityTrend.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{providerQualityTrend.status === 'available' ? `近 ${providerQualityTrend.window_days || 14} 天` : '样本不足'}</span></div>
                              <p className="mt-1 text-[9px] leading-4 text-slate-400">按日聚合可用事实与多来源冲突；不展示用户、行程或供应商原始响应。</p>
                              <div className="mt-2 space-y-1.5">{providerQualityTrend.points.slice(-7).map((point: any, index: number) => {
                                const eligible = Math.round(Number(point.eligible_ratio || 0) * 100);
                                const conflicts = Math.round(Number(point.source_conflict_ratio || 0) * 100);
                                const enumInvalid = Math.round(Number(point.enum_invalid_ratio || 0) * 100);
                                return <div key={`${point.date}-${point.kind}-${point.provider}-${index}`} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] text-slate-600">
                                  <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1"><span className="font-bold text-slate-700">{point.date} · {point.kind} · {point.provider}</span><span>样本 {point.candidate_count ?? 0}</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, Math.max(0, eligible))}%` }} /></div><span className="text-emerald-700">可用 {eligible}%</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-amber-400" style={{ width: `${Math.min(100, Math.max(0, conflicts))}%` }} /></div><span className={conflicts > 0 ? 'text-amber-700' : 'text-slate-400'}>冲突 {conflicts}%</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-rose-400" style={{ width: `${Math.min(100, Math.max(0, enumInvalid))}%` }} /></div><span className={enumInvalid > 0 ? 'text-rose-700' : 'text-slate-400'}>枚举异常 {enumInvalid}%</span></div>
                                </div>;
                              })}</div>
                            </div>}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="quality-evaluation-summary">
                        <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">持续评估</p><span className={`rounded px-2 py-1 text-[9px] font-bold ${qualitySummary?.training_ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{qualitySummary?.training_ready ? '可进入离线复核' : '样本不足'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-4">
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">事件<span className="ml-1 font-black text-slate-800">{qualitySummary?.events ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">采纳率<span className="ml-1 font-black text-slate-800">{qualitySummary?.rates?.adoption != null ? `${Math.round(Number(qualitySummary.rates.adoption) * 100)}%` : '—'}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">漂移<span className="ml-1 font-black text-slate-800">{qualitySummary?.drift?.status || '无样本'}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">反事实<span className="ml-1 font-black text-slate-800">{qualitySummary?.counterfactual?.status || '待采集'}</span></div>
                        </div>
                        {qualitySummary?.provider_quality_gate && (() => {
                          const gate = qualitySummary.provider_quality_gate;
                          const status = String(gate.status || 'not_required');
                          const isBlocked = status === 'blocked';
                          const isReady = status === 'available';
                          const style = isBlocked ? 'border-rose-200 bg-rose-50 text-rose-700' : isReady ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700';
                          const label = isBlocked ? '供应商质量阻断发布' : isReady ? '供应商质量门禁通过' : status === 'insufficient_data' ? '供应商质量样本不足' : '尚无供应商质量样本';
                          return <div className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] ${style}`} data-testid="provider-quality-release-gate"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-black">{label}</span><span>通过 {gate.ready_providers ?? 0} · 不足 {gate.insufficient_providers ?? 0} · 阻断 {gate.blocked_providers ?? 0}</span></div>{Array.isArray(gate.alerts) && gate.alerts.length > 0 && <div className="mt-1 space-y-0.5" data-testid="provider-quality-alerts">{gate.alerts.slice(0, 4).map((alert: any) => <p key={`${alert.code}-${alert.severity}`} className={alert.severity === 'critical' ? 'font-bold text-rose-700' : 'font-medium text-amber-700'}>{alert.severity === 'critical' ? '需处理' : '需关注'}：{alert.code}（{alert.affected_providers ?? 0} 个供应商）</p>)}</div>}{Array.isArray(gate.reasons) && gate.reasons.length > 0 && <p className="mt-1 leading-4">异常：{gate.reasons.slice(0, 3).join('；')}</p>}{status === 'insufficient_data' && <p className="mt-1 leading-4">证据不足时不会将供应商或模型标记为优质。</p>}</div>;
                        })()}
                        <p className="mt-2 text-[9px] leading-4 text-slate-400">质量指标只接受注册指标和带数值的规划事件；样本不足时不会显示“模型已学会”。</p>
                        {qualitySummary?.quality_metrics?.replan_success_rate_by_trigger && <div className="mt-2 space-y-1.5">{Object.entries(qualitySummary.quality_metrics.replan_success_rate_by_trigger as Record<string, any>).map(([trigger, value]) => <div key={trigger} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] font-bold text-slate-600"><div className="flex flex-wrap justify-between gap-2"><span>{trigger}</span><span className={value.status === 'available' ? 'text-slate-700' : 'text-amber-700'}>{value.status === 'available' ? `${Math.round(Number(value.success_rate || 0) * 100)}%` : '样本不足'} · 成功 {value.successful ?? 0} · 拒绝 {value.rejected ?? 0} · 失败 {value.failed ?? 0}</span></div>{Object.keys(value.failure_code_distribution || {}).length > 0 && <p className="mt-1 text-[8px] font-medium text-rose-600">失败码：{Object.entries(value.failure_code_distribution as Record<string, number>).map(([code, count]) => `${code} × ${count}`).join('、')}</p>}{value.models && Object.keys(value.models).length > 0 && <p className="mt-1 text-[8px] font-medium text-slate-400">版本：{Object.entries(value.models as Record<string, any>).map(([version, model]) => `${version} ${model.status === 'available' ? `${Math.round(Number(model.success_rate || 0) * 100)}%` : '样本不足'}`).join(' · ')}</p>}</div>)}</div>}
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="source-verification-funnel">
                        <div className="flex items-center justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">官方核验闭环</p><p className="mt-1 text-[9px] text-slate-400">从跳转到核验、采用的真实行为漏斗</p></div><span className={`rounded px-2 py-1 text-[9px] font-bold ${channelMetrics?.status === 'attribution_ready' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{channelMetrics?.status === 'attribution_ready' ? '可归因' : '待采集'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-4">
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">跳转<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.clicks ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">返回<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.returned ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">核验<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.verified ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">采用<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.adopted ?? 0}</span></div>
                        </div>
                        <p className="mt-2 text-[9px] leading-4 text-slate-400">返回率 {Math.round(Number(channelMetrics?.rates?.return_rate || 0) * 100)}% · 核验率 {Math.round(Number(channelMetrics?.rates?.verification_rate || 0) * 100)}% · 采用率 {Math.round(Number(channelMetrics?.rates?.adoption_rate || 0) * 100)}%</p>
                        <p className="mt-1 text-[9px] leading-4 text-slate-400">平台不把跳转视为已预订；“采用”仅代表用户明确确认候选。</p>
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="model-runtime-health">
                        <div className="flex items-center justify-between gap-2">
                          <div><p className="text-[10px] font-black text-slate-500">线上模型健康</p><p className="mt-1 text-[9px] text-slate-400">最近 {runtimeHealth?.window_hours ?? 24} 小时 · 仅展示聚合指标</p></div>
                          <span className="rounded px-2 py-1 text-[9px] font-bold text-slate-500 bg-slate-50">自动回滚已启用</span>
                        </div>
                        {runtimeModels.length > 0 ? <div className="mt-2 space-y-2">
                          {runtimeModels.map((model: any) => {
                            const status = String(model.status || 'insufficient_data');
                            const deploymentStatus = String(model.deployment_status || 'unknown');
                            const statusClass = status === 'healthy' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : status === 'rollback_required' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-amber-200 bg-amber-50 text-amber-700';
                            const rate = model.adoption_rate == null ? '—' : `${Math.round(Number(model.adoption_rate) * 100)}%`;
                            return <article key={model.version} className="rounded-lg border border-slate-200 p-2.5" data-testid={`model-runtime-${model.version}`}>
                              <div className="flex items-start justify-between gap-2"><div><p className="text-[11px] font-black text-slate-800">{model.version || '未命名版本'}</p><p className="mt-0.5 text-[9px] text-slate-400">部署：{deploymentStatus === 'canary' ? '灰度' : deploymentStatus === 'active' ? '主版本' : deploymentStatus === 'rolled_back' ? '已回滚' : deploymentStatus}</p></div><span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${statusClass}`}>{runtimeStatusLabel(status)}</span></div>
                              <div className="mt-2 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500 sm:grid-cols-4"><span className="rounded bg-slate-50 px-2 py-1">样本 <strong className="text-slate-800">{model.samples ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">采纳 <strong className="text-slate-800">{rate}</strong></span><span className="rounded bg-slate-50 px-2 py-1">错误 <strong className="text-slate-800">{model.error_rate == null ? '—' : `${Math.round(Number(model.error_rate) * 100)}%`}</strong></span><span className="rounded bg-slate-50 px-2 py-1">公平方差 <strong className="text-slate-800">{model.fairness_utility_variance == null ? '—' : Number(model.fairness_utility_variance).toFixed(3)}</strong></span></div>
                              {Array.isArray(model.reasons) && model.reasons.length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-rose-600">回滚原因：{model.reasons.join('、')}</p>}
                              {status === 'insufficient_data' && <p className="mt-1.5 text-[9px] leading-4 text-amber-700">样本不足，不对该版本做优劣判断。</p>}
                            </article>;
                          })}
                        </div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">暂无模型运行样本；灰度状态将在产生带版本事件后显示。</p>}
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="evaluation-audit-summary">
                        <div className="flex items-center justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">评估任务</p><p className="mt-1 text-[9px] text-slate-400">周期任务最近一次执行状态</p></div><span className={`rounded px-2 py-1 text-[9px] font-bold ${evaluationAudit?.health?.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : evaluationAudit?.health?.status === 'critical' ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'}`}>{evaluationAudit?.health?.status === 'healthy' ? '任务正常' : evaluationAudit?.health?.status === 'critical' ? '需要处理' : evaluationAudit?.health?.status === 'warning' ? '需要关注' : '尚无记录'}</span></div>
                        {evaluationAudit?.health && <p className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] leading-4 ${evaluationAudit.health.status === 'critical' ? 'border-rose-200 bg-rose-50 text-rose-700' : evaluationAudit.health.status === 'warning' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-slate-50 text-slate-600'}`}>{evaluationAudit.health.reason} · 连续失败 {evaluationAudit.health.consecutive_failures ?? 0} 次{evaluationAudit.health.age_seconds != null ? ` · 距最近执行 ${Math.round(Number(evaluationAudit.health.age_seconds) / 60)} 分钟` : ''}</p>}
                        {evaluationAudit?.latest ? <div className="mt-2 rounded-lg bg-slate-50 p-2.5"><div className="flex items-center justify-between gap-2"><span className="text-[10px] font-black text-slate-800">{evaluationAudit.latest.model_version || '未标注版本'}</span><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${evaluationAudit.latest.status === 'ready' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>{evaluationAudit.latest.status === 'ready' ? '可进入门禁' : '证据不足/已阻断'}</span></div><div className="mt-1.5 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500"><span>样本 <strong className="text-slate-800">{evaluationAudit.latest.sample_count ?? 0}</strong></span><span>验证集 <strong className="text-slate-800">{evaluationAudit.latest.validation_samples ?? 0}</strong></span><span>回写 <strong className="text-slate-800">{evaluationAudit.latest.callback?.accepted === true ? '成功' : evaluationAudit.latest.callback?.skipped ? '试运行' : '未成功'}</strong></span><span>时间 <strong className="text-slate-800">{evaluationAudit.latest.recorded_at ? new Date(Number(evaluationAudit.latest.recorded_at) * 1000).toLocaleString() : '—'}</strong></span></div>{evaluationAudit.latest.provider_quality_status && <div className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] ${evaluationAudit.latest.provider_quality_status === 'available' || evaluationAudit.latest.provider_quality_status === 'not_required' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : evaluationAudit.latest.provider_quality_status === 'blocked' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`} data-testid="evaluation-provider-quality"><div className="flex items-center justify-between gap-2"><span className="font-bold">本次评估的供应商质量门禁</span><span className="font-black">{evaluationAudit.latest.provider_quality_status === 'available' ? '通过' : evaluationAudit.latest.provider_quality_status === 'not_required' ? '未启用' : evaluationAudit.latest.provider_quality_status === 'blocked' ? '阻断' : '样本不足'}</span></div><p className="mt-1">覆盖 {evaluationAudit.latest.provider_quality_ready_providers ?? 0}/{evaluationAudit.latest.provider_quality_providers ?? 0} 个供应商 · 不足 {evaluationAudit.latest.provider_quality_insufficient_providers ?? 0} · 阻断 {evaluationAudit.latest.provider_quality_blocked_providers ?? 0}</p></div>}{evaluationAudit.latest.policy_comparison_status && <div className="mt-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[9px] text-slate-600"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">行为排序对照</span><span className={evaluationAudit.latest.policy_comparison_status === 'available' && evaluationAudit.latest.policy_comparison_ready === true ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{evaluationAudit.latest.policy_comparison_status === 'available' && evaluationAudit.latest.policy_comparison_ready === true ? '证据可用' : '样本不足/未通过'}</span></div><p className="mt-1 leading-4">基线 {evaluationAudit.latest.policy_baseline_samples ?? 0} 条 · 行为 {evaluationAudit.latest.policy_behavior_samples ?? 0} 条 · reward 提升 {evaluationAudit.latest.policy_reward_lift == null ? '—' : `${(Number(evaluationAudit.latest.policy_reward_lift) * 100).toFixed(1)}%`}</p></div>}{evaluationAudit.latest.fairness_segment_status && <div className="mt-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[9px] text-slate-600" data-testid="segment-fairness-audit"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">分层公平门禁</span><span className={evaluationAudit.latest.fairness_segment_status === 'available' && Number(evaluationAudit.latest.fairness_segment_threshold_exceeded || 0) === 0 && Number(evaluationAudit.latest.fairness_segment_max_stddev ?? 1) <= 0.1 ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{evaluationAudit.latest.fairness_segment_status === 'available' ? '已覆盖' : '样本不足'}</span></div><p className="mt-1 leading-4">最大标准差 {evaluationAudit.latest.fairness_segment_max_stddev == null ? '—' : Number(evaluationAudit.latest.fairness_segment_max_stddev).toFixed(3)} / 0.100 · 超阈值样本 {evaluationAudit.latest.fairness_segment_threshold_exceeded ?? '—'}</p><p className="mt-1 text-[8px] text-slate-400">按人群、目的地、季节和预算分层独立统计，不借用其他分层样本。</p>{evaluationAudit.latest.fairness_segment_dimensions && <div className="mt-2 grid grid-cols-2 gap-1.5" data-testid="segment-fairness-dimensions">{Object.entries(evaluationAudit.latest.fairness_segment_dimensions as Record<string, any>).map(([dimension, summary]: [string, any]) => <div key={dimension} className="rounded bg-slate-50 px-2 py-1.5"><div className="flex items-center justify-between gap-1"><span className="font-bold text-slate-600">{dimension === 'traveler_group' ? '人群' : dimension === 'budget_bucket' ? '预算' : dimension === 'destination' ? '目的地' : '季节'}</span><span className={summary?.status === 'available' ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{summary?.status === 'available' ? '可用' : '不足'}</span></div><p className="mt-1 text-[8px] text-slate-400">覆盖 {summary?.available_segments ?? 0}/{summary?.segment_count ?? 0} · 标准差 {summary?.max_stddev == null ? '—' : Number(summary.max_stddev).toFixed(3)}</p></div>)}</div>}</div>}{Array.isArray(evaluationAudit.latest.reason) && evaluationAudit.latest.reason.length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-amber-700">阻断原因：{evaluationAudit.latest.reason.join('、')}</p>}</div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">评估任务尚未产生审计记录；这不代表模型已通过评估。</p>}
                      </div>
                      {Array.isArray(evaluationAudit?.timeline) && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="evaluation-timeline">
                        <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">模型评估时间线</p><p className="mt-1 text-[9px] text-slate-400">评估、影子运行、状态变化和回滚的脱敏摘要</p></div><div className="flex items-center gap-2"><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${auditCleanupHealth?.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : auditCleanupHealth?.status === 'warning' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>清理 {auditCleanupHealth?.status === 'healthy' ? '正常' : auditCleanupHealth?.status === 'warning' ? '需关注' : '待执行'}</span><span className="text-[9px] text-slate-400">最近 {evaluationAudit.timeline.length} 条 · {evaluationAudit.timeline_source === 'persisted' ? '持久化审计' : evaluationAudit.timeline_source === 'derived' ? '兼容观察' : '持久化 + 兼容观察'}</span></div></div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 p-2" data-testid="evaluation-timeline-filters">
                          <select aria-label="模型版本筛选" value={auditVersionFilter} onChange={(event) => setAuditVersionFilter(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-bold text-slate-600 outline-none focus:border-orange-400">
                            <option value="">全部版本</option>
                            {Array.from(new Set((evaluationAudit.timeline as any[]).map((item: any) => String(item.version || '')).filter(Boolean))).sort().map((version) => <option key={version} value={version}>{version}</option>)}
                          </select>
                          <label className="flex items-center gap-1 text-[10px] font-bold text-slate-500">起始日期<input aria-label="审计起始日期" type="date" value={auditSinceFilter} onChange={(event) => setAuditSinceFilter(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-medium text-slate-600 outline-none focus:border-orange-400" /></label>
                          {(auditVersionFilter || auditSinceFilter) && <button type="button" onClick={() => { setAuditVersionFilter(''); setAuditSinceFilter(''); }} className="rounded-md px-2 py-1 text-[10px] font-bold text-slate-500 hover:bg-white hover:text-slate-800">清除筛选</button>}
                          {evaluationAudit.retention_days && <span className="ml-auto text-[9px] text-slate-400">保留 {evaluationAudit.retention_days} 天{auditCleanupHealth?.last_deleted != null ? ` · 最近清理 ${auditCleanupHealth.last_deleted} 条` : ''}</span>}
                        </div>
                        {auditSummary?.status === 'available' && <div className="mt-2 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500 sm:grid-cols-4"><span className="rounded bg-slate-50 px-2 py-1">事件 <strong className="text-slate-800">{auditSummary.count ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">样本 <strong className="text-slate-800">{auditSummary.total_samples ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">阻断 <strong className="text-slate-800">{auditSummary.statuses?.insufficient_evidence ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">回滚 <strong className="text-slate-800">{auditSummary.statuses?.rolled_back ?? 0}</strong></span></div>}
                        {auditSummary?.status === 'available' && Object.keys(auditSummary.reason_codes || {}).length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-slate-400">高频原因：{Object.entries(auditSummary.reason_codes as Record<string, number>).sort(([, left], [, right]) => Number(right) - Number(left)).slice(0, 3).map(([code, count]) => `${code} × ${count}`).join('、')}</p>}
                        {auditComparison?.status === 'available' && Array.isArray(auditComparison.versions) && auditComparison.versions.length > 0 && <div className="mt-2 overflow-x-auto rounded-lg border border-slate-100"><table className="w-full min-w-[420px] text-left text-[9px] text-slate-500"><thead className="bg-slate-50 text-[8px] font-black uppercase tracking-wide text-slate-400"><tr><th className="px-2 py-1.5">版本</th><th className="px-2 py-1.5">样本</th><th className="px-2 py-1.5">阻断率</th><th className="px-2 py-1.5">回滚</th><th className="px-2 py-1.5">证据</th></tr></thead><tbody>{auditComparison.versions.map((entry: any) => <tr key={entry.version} className="border-t border-slate-100"><td className="px-2 py-1.5 font-bold text-slate-700">{entry.version}</td><td className="px-2 py-1.5">{entry.samples ?? 0}</td><td className="px-2 py-1.5">{Math.round(Number(entry.block_rate || 0) * 100)}%</td><td className="px-2 py-1.5">{entry.rollbacks ?? 0}</td><td className={`px-2 py-1.5 font-bold ${entry.sufficient_evidence ? 'text-emerald-700' : 'text-amber-700'}`}>{entry.sufficient_evidence ? '足够' : '不足'}</td></tr>)}</tbody></table><p className="px-2 py-1.5 text-[8px] text-slate-400">仅比较聚合统计；样本不足时不判定策略优劣。</p></div>}
                        {evaluationAudit.timeline.length > 0 ? <div className="mt-2 space-y-1.5">{evaluationAudit.timeline.map((item: any, index: number) => <div key={`${item.id || item.timestamp || index}-${item.kind || 'event'}`} className="flex items-start gap-2 rounded-lg bg-slate-50 px-2.5 py-2"><span className={`mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full ${item.kind === 'rollback' ? 'bg-rose-500' : item.kind === 'evaluation' ? 'bg-amber-500' : item.kind === 'shadow' ? 'bg-orange-400' : 'bg-slate-400'}`} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-0.5"><span className="text-[10px] font-bold text-slate-700">{item.summary || item.kind}</span><span className="text-[9px] text-slate-400">{item.version || '未标注版本'}</span></div><p className="mt-0.5 text-[9px] text-slate-400">{item.at ? new Date(item.at).toLocaleString() : '—'}{item.sample_count != null ? ` · 样本 ${item.sample_count}` : ''}</p>{Array.isArray(item.reason_codes) && item.reason_codes.length > 0 && <p className="mt-1 text-[9px] leading-4 text-amber-700">原因码：{item.reason_codes.join('、')}</p>}</div></div>)}</div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">当前筛选范围内暂无审计事件；样本不足不代表模型已通过评估。</p>}
                        {evaluationAudit?.pagination?.has_more && <button type="button" onClick={() => void loadEarlierAuditEvents()} disabled={auditLoadingMore} className="mt-2 w-full rounded-md border border-slate-200 bg-white px-2.5 py-2 text-[10px] font-bold text-slate-600 transition hover:border-orange-300 hover:text-orange-700 disabled:cursor-wait disabled:opacity-60">{auditLoadingMore ? '正在加载更早事件…' : '加载更早事件'}</button>}
                      </div>}
                      </div>}
                    </div>
                  </section>}
                </div>
                {planVariants.length > 1 && (
                  <div className="px-8 pt-4 flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-bold text-slate-500">方案切换：</span>
                    {planVariants.map((v: any) => (
                      <button
                        key={v.id}
                        onClick={() => activePlanId !== v.id && handleSelectPlan(v.id)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors cursor-pointer ${
                          activePlanId === v.id ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-100'
                        }`}
                        title={v.desc || ''}
                      >
                        {v.name}{v.style === 'relaxed' ? ' · 慢游' : v.style === 'intense' ? ' · 暴走' : v.style === 'niche' ? ' · 小众' : ''}
                      </button>
                    ))}
                    {planSwitchNotice && <span role="status" className="basis-full text-[11px] font-bold text-emerald-700">{planSwitchNotice}</span>}
                  </div>
                )}
                {workspaceLayer === 'plan' && (experienceMode === 'pro' || showPlanDetails) && <SpecialistInsightsPanel
                  // P1 渲染端：文化背景与无障碍信息。后端早就放进了 payload，
                  // 但此前只发给审计接口、用户一个字段都看不到（等于没做）。
                  proposals={specialistRuntime?.proposals}
                />}
                {workspaceLayer === 'plan' && isHost && (
                  <RoomSuggestionInbox
                    comments={roomComments}
                    busy={pendingScope != null || swappingDay != null}
                    onAdopt={handleAdoptSuggestion}
                    onDismiss={(id) => onAckNodeComment?.(id, 'dismissed')}
                  />
                )}
                {workspaceLayer === 'plan' && (
                  <ScopedAdjustPanel
                    days={totalDays.length}
                    pending={pendingScope}
                    report={scopedReport}
                    busy={pendingScope != null || swappingDay != null}
                    onAdjust={handleScopedAdjust}
                  />
                )}
                {workspaceLayer === 'plan' && <MafengwoStylePanel
                  routes={currentDayRoutes} 
                  totalDays={totalDays}
                  activeDayIndex={activeDayIndex}
                  // 方案台账（缺餐/换餐/换住宿/分天重排 + 官方档案）：正文上要看得见，不能只躺在 JSON 里
                  planLedgers={planLedgers}
                  onSelectDay={setActiveDayIndex}
                  travelDetails={travelDetails} 
                  selectedPoiIndex={selectedPoiIndex} 
                  onSelectPoi={setSelectedPoiIndex} 
                  weatherInfo={weatherInfo}
                  trafficInfo={trafficInfo}
                  onReset={handleResetIntent}
                  consensusSummary={consensusSummary}
                  fairnessAudit={fairnessAudit}
                  memberNegotiation={memberNegotiation}
                  budgetData={budgetData}
                  onSwapPoi={handleSinglePoiSwap}
                  onMarkVisited={handleNodeVisited}
                  visitedNodes={visitedNodes}
                  roomCode={roomCode}
                  roomMembers={roomMembers}
                  teamSatisfaction={teamSatisfaction}
                  arbitrationRecords={arbitrationRecords}
                  nodeVotes={nodeVotes}
                  peerVoteCounts={peerVoteCounts}
                  onVote={handleVote}
                  roomComments={roomComments}
                  onNodeComment={onNodeComment}
                  wsConnected={wsConnected}
                  currentUser={currentUser}
                  onApplyScenario={({ budgetLimit, paceLimit, riskTolerance }: { budgetLimit: number; paceLimit: number; riskTolerance: number }) => {
                    const constraintIntent = `请在下一轮规划中控制人均预算不超过 ¥${budgetLimit}，每天最多安排 ${paceLimit} 个节点，风险容忍度为 ${riskTolerance}。`;
                    setUserIntent((previous: string) => previous ? `${previous}\n${constraintIntent}` : constraintIntent);
                    setPhase('drafting');
                    setIntentsReady(false);
                  }}
                  safetyInfo={safetyInfo}
                  safetyBrief={safetyBrief}
                  briefLoading={briefLoading}
                  onGenerateSafetyBrief={generateSafetyBrief}
                  targetCity={targetCityInfo?.name || ''}
                   onPhotoClick={(poi: {name: string; type?: string; city?: string}) => {
                    setPhotoModalPoi(poi);
                    setActivePhotoSource(0);
                    setPhotoModalOpen(true);
                   }}
                   onSecondaryPlan={(poi: any) => setSecondaryPoi({ ...poi, city: targetCityInfo?.name || '' })}
                 />}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      <SecondaryPlanner
        open={Boolean(secondaryPoi)}
        city={secondaryPoi?.city || targetCityInfo?.name || ''}
        poi={secondaryPoi}
        userId={currentUser.id}
        tripId={planningResourceId}
        onClose={() => setSecondaryPoi(null)}
      />

      {/* 旅中风险推送中心：工作区级浮层，不随地图折叠而消失（只要地图能收起，风险按钮就不能一起消失） */}
      <RiskPushCenter
        city={targetCityInfo?.name || ''}
        coordinate={targetCityInfo?.lnglat}
        safetyInfo={safetyInfo}
        weatherInfo={weatherInfo}
        trafficInfo={trafficInfo}
        userId={currentUser?.id}
        roomCode={roomCode}
        routeNodes={dynamicRoutes.map((node) => node.name).filter(Boolean)}
        onOpenExecution={phase === 'decision' ? () => setWorkspaceLayer('execution') : undefined}
        onRequestReplan={(item) => {
          // 提醒里没有可直接确认的提案时，唯一诚实的动作是"带着这条风险回到需求页"：
          // 由用户自己确认后再生成，前端不替用户改方案，也不编造受影响节点。
          const affected = item.affectedNodes && item.affectedNodes.length > 0
            ? `已收到的可能受影响节点：${item.affectedNodes.join('、')}。`
            : '这条提醒没有给出受影响节点，请在需求里说明要调整哪些安排。';
          setUserIntent(`因风险提醒“${item.title}”需要调整剩余行程：${item.detail} ${affected} 保持未完成节点不变，只对受影响的部分做最小修改。`);
          setPhase('drafting');
          setIntentsReady(false);
        }}
        onReplan={(proposal) => {
          void (async () => {
            const signals = Array.isArray(proposal?.changed_signals) ? proposal.changed_signals : [];
            let actionText = '仅对受影响节点执行最小修改重规划';
            try {
              const response = await apiFetch('/api/v1/planning/emergency/evaluate', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: currentUser.id, trip_id: planningResourceId, destination: targetCityInfo?.name || '', changed_signals: signals, current_route: dynamicRoutes }),
              });
              const payload = await response.json();
              const action = payload?.data?.actions?.[0] || payload?.actions?.[0];
              if (action?.title) actionText = `${action.title}：${action.reason || ''}`;

              if (!action || action.type === 'manual_review') {
                setApiError(`${actionText}，请补充具体受影响节点后再重排`);
                return;
              }

              // The evaluator is deterministic; submit its structured action
              // directly to the minimal-perturbation endpoint. This keeps
              // unaffected nodes intact and avoids a second LLM rewrite.
              const replanResponse = await apiFetch('/api/v1/planning/replan', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  trip_id: planningResourceId,
                  destination: targetCityInfo?.name || '',
                  existing_route: dynamicRoutes,
                  affected_nodes: action.affected_nodes || [],
                  changed_signals: signals,
                  replacement_kind: action.replacement_kind || 'DESTINATION',
                  member_utilities: Object.values(teamSatisfaction).map((value) => Number(value)).filter((value) => Number.isFinite(value)),
                }),
              });
              const replanPayload = await replanResponse.json();
              if (!replanResponse.ok) {
                throw new Error(replanPayload?.error?.message || replanPayload?.error || '结构化重规划失败');
              }
              const data = replanPayload?.data || replanPayload;
              const candidateCount = Array.isArray(data?.replacement_candidates) ? data.replacement_candidates.length : 0;
              setReplanProposal({
                proposalId: proposal?.proposal_id || payload?.data?.proposal_id || payload?.proposal_id || '',
                action: { ...action, trigger_types: proposal?.trigger_types || [], expires_at: proposal?.expires_at },
                data,
              });
              setApiError(`${actionText}。已保留 ${data?.preserved_nodes ?? 0} 个节点，${candidateCount} 个替代候选待确认`);
              return;
            } catch { /* use the safe generic action text when the evaluator is unavailable */ }
            setUserIntent(`风险中心确认：${actionText}。保留未受影响节点，不改变总目的地。`);
            setApiError(`${actionText}，结构化服务暂不可用，请稍后重试`);
          })();
        }}
        />

      {/* 右侧：地图与全视图渲染 */}
      <div className={`relative bg-slate-100 overflow-hidden flex flex-col ${mapLayout === 'doc' ? 'w-full min-h-[56px] lg:w-14 lg:flex-none lg:min-h-0' : 'flex-1 min-h-[48vh] lg:min-h-0'}`}>
        {mapLayout === 'doc' ? (
          <button
            type="button"
            data-testid="expand-map"
            onClick={() => changeMapLayout('balanced')}
            className="group flex h-full w-full flex-row items-center justify-center gap-2 px-4 py-3 text-slate-500 transition-colors hover:bg-orange-50 hover:text-orange-600 lg:flex-col lg:px-0 lg:py-0"
            title="展开地图"
          >
            <ChevronRight className="h-4 w-4 lg:rotate-180" />
            <span className="text-xs font-bold lg:[writing-mode:vertical-rl]">展开地图</span>
            <span className="hidden text-[11px] font-medium text-slate-400 lg:inline lg:[writing-mode:vertical-rl]">空间关系需要时再看</span>
          </button>
        ) : (
          <>
        <FullRouteVisualizer 
          phase={phase}
          routes={currentDayRoutes}
          allRoutes={dynamicRoutes}
          activeDay={totalDays[activeDayIndex]}
          actualPath={realPath}
          selectedPoiIndex={selectedPoiIndex}
          onPoiSelect={setSelectedPoiIndex}
          onWakeAgent={() => setShowBlackboard(true)}
          onExit={onBack}
          targetCityInfo={targetCityInfo}
          travelDetails={travelDetails}
          safetyInfo={safetyInfo}
          weatherInfo={weatherInfo}
          trafficInfo={trafficInfo}
          budgetData={budgetData}
          planVariants={planVariants}
          activePlanId={activePlanId}
          optimizationAudit={optimizationAudit}
          onSelectPlanVariant={handleSelectPlan}
          simpleMode={experienceMode === 'simple'}
        />

        {/* 碳足迹与绿色交通评估（P5）：属于创新能力，只在专业模式里出现，不进普通用户的默认路径 */}
        {experienceMode === 'pro' && (
          <CarbonFootprintPanel routes={dynamicRoutes} travelDetails={travelDetails} />
        )}

        <div className="spatial-dock absolute top-6 right-6 z-40 flex items-center gap-3 pointer-events-auto">
          <button 
            onClick={() => setShowBlackboard(!showBlackboard)} 
            className="bg-white/90 backdrop-blur-md p-2.5 rounded-xl shadow-lg text-slate-700 hover:text-orange-500 border border-slate-200/80 transition-all hover:scale-105 cursor-pointer flex items-center gap-1.5 text-xs font-bold"
            title="查看方案取舍与协作记录"
          >
            <BrainCircuit className="w-4 h-4 text-orange-500"/>
            <span className="hidden sm:inline">方案取舍</span>
          </button>
        </div>

        <div className="absolute bottom-6 left-0 w-full z-30 pointer-events-none">
          <AnimatePresence>
            {phase === 'decision' && (
              <motion.div key="bottom-carousel-wrapper" initial={{ y: 120, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 120, opacity: 0 }} className="pointer-events-auto flex flex-col items-center">
                <BottomMapCarousel selectedIndex={selectedPoiIndex} onSelect={setSelectedPoiIndex} routes={currentDayRoutes} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
          </>
        )}

        <AnimatePresence>
          {showBlackboard && (
            <motion.div key="blackboard-drawer-panel" initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: "spring", damping: 25 }} className="fixed top-0 right-0 w-[420px] h-full bg-white/95 backdrop-blur-2xl shadow-2xl z-50 flex flex-col border-l border-slate-200">
              <div className="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <div>
                  <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><BrainCircuit className="w-4 h-4 text-orange-500"/> 方案取舍记录</h3>
                  <p className="mt-1 text-[10px] text-slate-500">记录系统如何在预算、距离、成员偏好和风险之间做选择；它不是聊天记录，也不会改变已确认的路线。</p>
                </div>
                <button data-testid="close-blackboard" onClick={() => setShowBlackboard(false)} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer" aria-label="关闭方案取舍记录"><X className="w-4 h-4"/></button>
              </div>
              <div className="p-6 overflow-y-auto text-xs font-mono text-slate-600 whitespace-pre-wrap custom-scrollbar leading-relaxed">
                {humanReadableLogs}
                {phase === 'deduction' && <span className="inline-block w-2 h-4 bg-orange-500 animate-pulse ml-1 align-middle"></span>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showPoster && (
            <motion.div key="poster-modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-8">
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }} className="bg-white rounded-2xl shadow-2xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between p-4 border-b border-slate-100">
                  <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><Share2 className="w-4 h-4 text-orange-600"/> 行程海报</h3>
                  <button onClick={() => setShowPoster(false)} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer"><X className="w-4 h-4"/></button>
                </div>
                <div className="p-4">
                  <ItineraryPoster 
                    route={currentDayRoutes.map((r: any) => ({
                      day: r.day,
                      time: r.time,
                      location: r.name,
                      name: r.name,
                      cost_estimate: r.cost,
                      tags: r.tags,
                    }))} 
                    city={targetCityInfo?.name || ''}
                  />
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        <TripDiaryModal open={showDiary} onClose={() => setShowDiary(false)} markdown={diaryMarkdown} city={targetCityInfo?.name || ''} onDownload={downloadDiary} todayDay={executionTodayDay ?? (totalDays[activeDayIndex] || 1)} />

        {showBudget && (
          <div ref={budgetDialogRef} className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/45 p-4" role="dialog" aria-modal="true" aria-labelledby="budget-panel-title">
            <div className="max-h-[90vh] w-full max-w-[520px] overflow-y-auto">
              <div className="mb-2 flex items-center justify-between rounded-xl bg-white px-4 py-3 shadow-lg">
                <h3 id="budget-panel-title" className="text-sm font-black text-slate-800">协作预算账本</h3>
                <button type="button" onClick={() => setShowBudget(false)} className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100" title="关闭预算账本" aria-label="关闭预算账本"><X className="h-4 w-4" /></button>
              </div>
              <BudgetPanel userId={currentUser.id} tripId={planningResourceId} />
            </div>
          </div>
        )}
        <ExpenseReportModal open={showExpenseReport} onClose={() => setShowExpenseReport(false)} userId={currentUser.id} tripId={planningResourceId} />
      <SatisfactionModal open={showSatisfaction} onClose={() => setShowSatisfaction(false)} userId={currentUser.id} tripId={planningResourceId} banditArmId={banditArmId} />
        <BookingHub open={showBookingHub} destination={targetCityInfo?.name || initialDestination || ''} tripId={planningResourceId} simpleMode={experienceMode === 'simple'} onClose={() => setShowBookingHub(false)} />

        <AnimatePresence>
          {feedbackPoi && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/45 p-4" onClick={() => setFeedbackPoi(null)}>
              <motion.div ref={poiFeedbackDialogRef} initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-2xl" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="poi-feedback-title">
                <div className="flex items-start justify-between gap-3">
                  <div><h3 id="poi-feedback-title" className="text-sm font-black text-slate-900">告诉我们为何不喜欢</h3><p className="mt-1 text-xs text-slate-500">{feedbackPoi}</p></div>
                  <button type="button" onClick={() => setFeedbackPoi(null)} className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100" title="关闭反馈" aria-label="关闭反馈"><X className="h-4 w-4" /></button>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2">
                  {['人太多', '距离太远', '太累', '价格不合适', '兴趣不符', '信息不确定'].map((reason) => (
                    <button type="button" key={reason} onClick={() => setFeedbackReason(reason)} className={`min-h-9 rounded-md border px-2 text-xs font-bold transition ${feedbackReason === reason ? 'border-orange-500 bg-orange-50 text-orange-700' : 'border-slate-200 text-slate-600 hover:border-orange-300'}`}>{reason}</button>
                  ))}
                </div>
                <textarea value={feedbackReason && !['人太多', '距离太远', '太累', '价格不合适', '兴趣不符', '信息不确定'].includes(feedbackReason) ? feedbackReason : ''} onChange={(event) => setFeedbackReason(event.target.value)} rows={2} placeholder="其他原因（可选）" className="mt-3 w-full resize-none rounded-md border border-slate-200 px-3 py-2 text-xs outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100" />
                <button type="button" onClick={submitPoiFeedback} className="mt-3 min-h-10 w-full rounded-md bg-orange-500 px-4 text-sm font-bold text-white hover:bg-orange-600">提交反馈</button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* 实景照片搜索 Modal — 精准搜索：城市+景点+实景 */}
        <AnimatePresence>
          {photoModalOpen && photoModalPoi && (() => {
            const city = photoModalPoi.city || '';
            const poi = photoModalPoi.name;
            const preciseQuery = city ? `${city} ${poi} 实景` : `${poi} 实景`;
            const preciseEncoded = encodeURIComponent(preciseQuery);

            const sources = [
              { label: '百度图片', url: `https://image.baidu.com/search/index?tn=baiduimage&word=${preciseEncoded}` },
              { label: 'Google', url: `https://www.google.com/search?q=${preciseEncoded}&tbm=isch` },
              { label: 'Bing', url: `https://www.bing.com/images/search?q=${preciseEncoded}` },
            ];

            return (
            <motion.div
              key="photo-modal-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 sm:p-8"
              onClick={() => setPhotoModalOpen(false)}
            >
              <motion.div
                initial={{ scale: 0.9, opacity: 0, y: 20 }}
                animate={{ scale: 1, opacity: 1, y: 0 }}
                exit={{ scale: 0.9, opacity: 0, y: 20 }}
                transition={{ type: 'spring', damping: 25 }}
                className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[88vh] flex flex-col overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                {/* 标题栏 */}
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 bg-slate-50 shrink-0">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-emerald-500 flex items-center justify-center shrink-0">
                      <Camera className="w-4 h-4 text-white" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="font-bold text-slate-800 text-sm truncate">{poi}</h3>
                      <p className="text-[11px] text-slate-500 truncate">
                        {city ? `${city} · ` : ''}{photoModalPoi.type || '景点'} · 搜索词：<span className="text-orange-600 font-bold">{preciseQuery}</span>
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <a
                      href={sources[activePhotoSource]?.url || sources[0].url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 rounded-lg bg-orange-50 text-orange-600 text-xs font-bold hover:bg-orange-100 border border-orange-200 transition-colors flex items-center gap-1"
                    >
                      <ExternalLink className="w-3 h-3" /> 新窗口打开
                    </a>
                    <button
                      onClick={() => setPhotoModalOpen(false)}
                      className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-400 cursor-pointer transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* 搜索源 Tab 切换 */}
                <div className="flex items-center gap-1 px-5 py-2 border-b border-slate-100 bg-white shrink-0">
                  <span className="text-[10px] text-slate-400 mr-2 font-bold">搜索源：</span>
                  {sources.map((src, i) => (
                    <button
                      key={src.label}
                      onClick={() => setActivePhotoSource(i)}
                      className={`px-3 py-1 rounded-md text-[11px] font-bold border transition-colors ${i === activePhotoSource ? 'bg-emerald-500 text-white border-emerald-500' : 'bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100'}`}
                    >
                      {src.label}
                    </button>
                  ))}
                  <div className="flex-1" />
                  <span className="text-[10px] text-slate-400">
                    精准搜索：城市+景点名+实景
                  </span>
                </div>

                  {/* iframe 内容区 */}
                  <div className="flex-1 relative bg-slate-100">
                    <iframe
                      src={sources[activePhotoSource]?.url || sources[0].url}
                      className="w-full h-full border-0"
                      title={`${poi} 实景照片`}
                      /* allow-same-origin is deliberately NOT granted: this
                         document is a third-party search page built from user
                         and POI text. Combining allow-scripts with
                         allow-same-origin would let the framed page reach this
                         origin, defeating the sandbox. Without it the frame
                         gets an opaque origin, which is what we want. */
                      sandbox="allow-scripts allow-popups allow-forms allow-popups-to-escape-sandbox"
                    />
                  </div>

                {/* 底部提示 */}
                <div className="px-5 py-2.5 border-t border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
                  <span className="text-[11px] text-slate-500">
                    当前搜索词：<span className="font-bold text-orange-600">{preciseQuery}</span> — 已包含城市名以精确定位
                  </span>
                  <span className="text-[11px] text-slate-400">
                    搜索结果仅供参考，以实际现场为准
                  </span>
                </div>
              </motion.div>
            </motion.div>
            );
          })()}
        </AnimatePresence>

      </div>
    </motion.div>
  );
}
