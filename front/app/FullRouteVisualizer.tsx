'use client';

import { useState, useEffect, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Map as MapIcon, 
  Activity, 
  ArrowLeft,
  CheckCircle2,
  X,
  Scale,
  MapPin,
  TrendingUp,
  BarChart3,
} from 'lucide-react';
import {
  RADAR_FONT,
  parseCostNumber,
  useRadarTheme,
  radarPalette,
  radarBackground,
  radarSharedCss,
} from './lib/radarTheme';
import DraggablePanel from './components/DraggablePanel';
import TripMacroDashboard from './components/TripMacroDashboard';
import InteractiveAmapComponent, { RoutePoint, SafetyInfo } from './InteractiveAmapComponent';

function routeDay(point: any): number {
  const structured = Number(point?.day);
  if (Number.isFinite(structured) && structured > 0) return structured;
  const text = `${point?.time || ''} ${point?.desc || ''}`;
  const match = text.match(/Day\s*(\d+)/i);
  return match ? Number(match[1]) : 1;
}

// 👑 智能实景图片渲染组件：HTTPS 统一 + Referer 防盗链绕过 + 精美渐变兜底卡片（永不灰色占位）
export function RealPoiImage({ photoUrl, poiName, className = '', type }: { photoUrl?: string; poiName: string; className?: string; type?: string }) {
  const [loaded, setLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  const normalizedUrl = (() => {
    if (!photoUrl || typeof photoUrl !== 'string') return '';
    let u = photoUrl.trim();
    if (u.startsWith('//')) u = 'https:' + u;
    else if (u.startsWith('http://')) u = 'https://' + u.slice(7);
    return u.startsWith('https://') ? u : '';
  })();
  const showReal = Boolean(normalizedUrl) && !imgError;

  if (!showReal) {
    const typeLabel = type || '目的地';
    const typeColor =
      type?.includes('餐') || type?.includes('食') ? 'from-orange-500 to-rose-500'
      : type?.includes('酒店') || type?.includes('住宿') ? 'from-indigo-500 to-purple-600'
      : 'from-emerald-500 to-teal-600';
    return (
      <div className={`relative overflow-hidden rounded-xl ${className}`}>
        <div className={`absolute inset-0 bg-gradient-to-br ${typeColor}`} />
        <div className="absolute inset-0 opacity-20" style={{
          backgroundImage: 'radial-gradient(circle at 2px 2px, rgba(255,255,255,0.3) 1px, transparent 0)',
          backgroundSize: '16px 16px',
        }} />
        <div className="relative h-full w-full flex flex-col items-center justify-center gap-2 p-4">
          <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center">
            <MapPin className="w-6 h-6 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <p className="text-white font-bold text-sm leading-tight px-2 line-clamp-2 drop-shadow-md">{poiName}</p>
            <p className="text-white/80 text-[10px] font-bold mt-1 tracking-wide">{typeLabel}·地图预览</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 rounded-xl ${className}`}>
      {!loaded && (
        <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-300 dark:from-slate-700 dark:to-slate-800 animate-pulse" />
      )}
      <img
        src={normalizedUrl}
        alt={poiName}
        onLoad={() => setLoaded(true)}
        onError={() => setImgError(true)}
        className={`w-full h-full object-cover transition-opacity duration-500 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}

// 👑 真实分步导航解析器：剥离 HTML 标签，精确提取高德真实指引
export function getNaviSteps(naviObj: any): string[] {
  if (!naviObj) return [];
  const rawSteps = naviObj?.driving?.steps || naviObj?.walking?.steps || naviObj?.transit?.steps || naviObj?.steps;
  if (Array.isArray(rawSteps) && rawSteps.length > 0) {
    return rawSteps
      .map((s: any) => {
        if (typeof s === 'string') return s.replace(/<\/?[^>]+(>|$)/g, '').trim();
        const inst = s?.instruction || s?.road || '';
        return inst.replace(/<\/?[^>]+(>|$)/g, '').trim();
      })
      .filter((s: string) => Boolean(s && s.length > 0));
  }
  return [];
}

interface FullRouteVisualizerProps {
  phase: 'drafting' | 'deduction' | 'decision';
  routes?: RoutePoint[];
  /** 整个行程（所有天）：当天没有节点时地图改为展示它，避免地图永远是空的 */
  allRoutes?: RoutePoint[];
  activeDay?: number;
  actualPath?: [number, number][];
  selectedPoiIndex: number | null;
  onPoiSelect: (index: number) => void;
  onWakeAgent?: () => void;
  onExit?: () => void;
  targetCityInfo?: { name: string; lnglat: [number, number] };
  safetyInfo?: SafetyInfo;
  weatherInfo?: any;
  trafficInfo?: any;
  travelDetails?: Record<string, any>;
  budgetData?: any;
}

export default function FullRouteVisualizer({
  phase, 
  routes = [], 
  allRoutes = [],
  activeDay,
  actualPath = [], 
  selectedPoiIndex, 
  onPoiSelect, 
  onWakeAgent, 
  onExit, 
  targetCityInfo,
  safetyInfo,
  weatherInfo,
  trafficInfo,
  budgetData
}: FullRouteVisualizerProps) {
  const mode = useRadarTheme();
  const COLORS = radarPalette(mode);

  // 控制 2D 地图 (MICRO) 与 行程数据看板 (MACRO)
  const [viewState, setViewState] = useState<'MACRO' | 'MICRO'>('MICRO');
  const [windowReady, setWindowReady] = useState(false);
  const [showArbitrationModal, setShowArbitrationModal] = useState(false);
  const [activeArbitrationTab, setActiveArbitrationTab] = useState<'replacement' | 'original'>('replacement');
  // 👑 记录「从看板节点点击跳回 2D」的原节点 idx，用于在 2D 提供「返回看板 + 定位原节点」快捷入口
  const [returnDashboardNode, setReturnDashboardNode] = useState<number | null>(null);

  // 👑 时间轴状态：按天回放路线
  const [timelineDay, setTimelineDay] = useState(1);
  const timelineDays = useMemo(() => {
    const days = new Set<number>(routes.map(routeDay));
    return Array.from(days).sort((a, b) => a - b);
  }, [routes]);

  // 👑 按时间轴天过滤路线节点
  const filteredRoutes = useMemo(() => {
    return routes.filter((r: any) => routeDay(r) === timelineDay);
  }, [routes, timelineDay]);

  useEffect(() => {
    setWindowReady(true);
  }, []);

  const cityName = targetCityInfo?.name || (routes.length > 0 ? routes[0].name : '目标城市');

  // ⚠️ 职责分离：行程数据看板聚焦「成本分布 / 时间轴 / 预算」，风险维度（CII/五维/告警/天气路况）统一归属 WorldSafetyGlobe 3D 态势感知雷达

  const realReplacementNode = routes.find(r => r.tags?.includes('住宿') || r.name.includes('酒店') || r.name.includes('民宿')) 
    || (routes.length > 0 ? routes[routes.length - 1] : { name: '精选品质宿所/特色体验点' });

  // 👑 基于「当前时间轴天」的真实节点成本计算博弈裁决数据（修复：此前误用全程 nodes，导致"今日"口径=全程总消费）
  const dayNodes = (filteredRoutes || []).filter((r: any) => r && typeof r.name === 'string');
  const dayTotal = dayNodes.reduce((sum: number, r: any) => sum + parseCostNumber(r.cost), 0);
  const dailyBudget = Number(budgetData?.daily_avg || budgetData?.total_budget || 0);
  // 👑 人均估算 = 当日总消费 ÷ 出行人数（后端下发 member_count；缺省按 1 人，避免误除导致数值失真）
  const memberCount = Math.max(1, Number(budgetData?.member_count || 1));
  const highestCostNode = dayNodes.length > 0
    ? dayNodes.reduce((max: any, r: any) => (parseCostNumber(r.cost) > parseCostNumber(max.cost) ? r : max), dayNodes[0])
    : null;
  const lowestCostNode = dayNodes.length > 1
    ? dayNodes.reduce((min: any, r: any) => (parseCostNumber(r.cost) < parseCostNumber(min.cost) ? r : min), dayNodes[0])
    : null;
  const overrun = dailyBudget > 0 ? dayTotal - dailyBudget : 0;
  const hasOverrun = overrun > 0;
  const avgCost = dayNodes.length > 0 ? Math.round(dayTotal / memberCount) : 0;
  const overrunPercent = dailyBudget > 0 ? Math.max(0, Math.round((overrun / dailyBudget) * 100)) : 0;
  // 按成本降序排列的节点，用于决策看板成本条形图
  // 价格只有"能解析出数字"的才参与比较：取不到价格的一律不计入、也不当 0 元
  // （之前把"暂无供应商数据"解析成 0，于是看板显示"消费 ¥0 / 人均 ¥0 / 成本最低=某酒店"，
  //  看着像结论，其实什么都没说）
  const pricedNodes = [...dayNodes].filter((n: any) => parseCostNumber(n.cost) > 0);
  const pricedTotal = pricedNodes.reduce((s: number, n: any) => s + parseCostNumber(n.cost), 0);
  const unpricedNodes = dayNodes.filter((n: any) => parseCostNumber(n.cost) <= 0);
  const costSortedNodes = [...pricedNodes].sort((a, b) => parseCostNumber(b.cost) - parseCostNumber(a.cost)).slice(0, 6);
  const maxNodeCost = costSortedNodes.length > 0 ? Math.max(1, parseCostNumber(costSortedNodes[0].cost)) : 1;

  // 👑 多日预算滚动预测：逐日累计花费 vs 累计预算，提前预警后期超支
  const dailyForecast = useMemo(() => {
    const days = timelineDays;
    const perDay = days.map((d) => {
      const nodes = routes.filter((r: any) => {
        return routeDay(r) === d;
      });
      const cost = nodes.reduce((s: number, r: any) => s + parseCostNumber(r.cost), 0);
      return { day: d, cost, over: dailyBudget > 0 && cost > dailyBudget };
    });
    const totalSpent = perDay.reduce((s, d) => s + d.cost, 0);
    const totalBudget = Number(budgetData?.total_budget || dailyBudget * days.length || 0);
    return { perDay, totalSpent, totalBudget, remaining: totalBudget - totalSpent };
  }, [routes, timelineDays, dailyBudget, budgetData]);

  if (!windowReady) return null;

  return (
    <div className="relative w-full h-full bg-slate-900 overflow-hidden flex select-none">
      
      {/* 主地图视图 */}
      <div className="relative flex-1 h-full w-full">
        <InteractiveAmapComponent 
          phase={phase}
          selectedPoiIndex={selectedPoiIndex}
          onPoiSelect={onPoiSelect}
          luoyangRoute={routes} 
          allRoute={allRoutes}
          activeDay={activeDay}
          actualPath={actualPath}
          safetyInfo={safetyInfo}
          weatherInfo={weatherInfo}
          trafficInfo={trafficInfo}
          onExit={onExit} 
          onWakeAgent={onWakeAgent}
        />

        {/* 👑 彻底解决遮挡问题：动作条置于右上角，图层优先级升至 z-50，高亮“返回大厅”按钮 */}
        <div className="visualizer-action-dock absolute top-4 right-4 sm:top-6 sm:right-6 z-50 flex items-center gap-2.5 pointer-events-auto flex-wrap justify-end">
          <button
            onClick={() => setShowArbitrationModal(true)}
            className="visualizer-action-button bg-slate-900/90 hover:bg-slate-800 text-amber-400 border border-amber-500/40 px-3.5 py-2 rounded-2xl shadow-2xl flex items-center gap-2 text-xs font-bold transition-all cursor-pointer backdrop-blur-md"
          >
            <Scale className="w-4 h-4 text-amber-400" />
            <span>博弈裁决看板</span>
          </button>

          <button
            onClick={() => setViewState(prev => prev === 'MACRO' ? 'MICRO' : 'MACRO')}
            aria-pressed={viewState === 'MACRO'}
            aria-label={viewState === 'MACRO' ? '返回 2D 导航' : '切换到行程数据看板'}
            className="visualizer-action-button bg-slate-900/90 hover:bg-slate-800 text-white px-3.5 py-2 rounded-2xl border border-slate-700 shadow-2xl flex items-center gap-2 text-xs font-bold transition-all cursor-pointer backdrop-blur-md"
          >
            {viewState === 'MACRO' ? (
              <>
                <MapIcon className="w-4 h-4 text-orange-400" />
                <span>返回 2D 导航</span>
              </>
            ) : (
              <>
                <BarChart3 className="w-4 h-4 text-cyan-400" />
                <span>行程数据看板</span>
              </>
            )}
          </button>

          {/* 👑 “返回大厅”按钮：高亮橙色、清晰文字、防遮挡 */}
          {onExit && (
            <button 
              onClick={onExit} 
              className="visualizer-action-button bg-orange-500 hover:bg-orange-600 text-white backdrop-blur-md px-4 py-2 rounded-2xl border border-orange-400 shadow-2xl flex items-center gap-1.5 text-xs font-bold transition-all cursor-pointer"
              title="返回大厅"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>返回大厅</span>
            </button>
          )}
        </div>

        {/* 👑 从看板节点点击跳回 2D 后：返回看板快捷入口 + 定位原节点悬停态 */}
        {viewState === 'MICRO' && returnDashboardNode !== null && (
          <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-50 pointer-events-auto">
            <div className="group relative flex items-center gap-1.5 rounded-2xl bg-slate-900/90 backdrop-blur-md border border-cyan-500/40 shadow-2xl pl-1.5 pr-1 py-1.5">
              <button
                onClick={() => onPoiSelect(returnDashboardNode)}
                title={`定位节点：${routes[returnDashboardNode]?.name || '—'}`}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-cyan-300 hover:bg-cyan-500/10 text-xs font-bold transition-all cursor-pointer"
              >
                <MapPin className="w-3.5 h-3.5" />
                定位
              </button>
              <button
                onClick={() => setViewState('MACRO')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-orange-500/15 border border-orange-500/40 text-orange-300 hover:bg-orange-500/25 text-xs font-bold transition-all cursor-pointer"
              >
                <BarChart3 className="w-4 h-4" />
                返回看板
              </button>
              <button
                onClick={() => setReturnDashboardNode(null)}
                aria-label="关闭快捷入口"
                className="px-1.5 py-1.5 rounded-lg text-slate-500 hover:text-slate-300 cursor-pointer"
              >
                <X className="w-3.5 h-3.5" />
              </button>
              {/* 悬停提示：原节点信息 */}
              <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-200 whitespace-nowrap">
                <div className="bg-slate-900/95 border border-cyan-500/30 text-white rounded-xl px-3 py-2 text-xs shadow-xl">
                  已定位节点 <span className="text-cyan-300 font-bold">#{returnDashboardNode + 1} {routes[returnDashboardNode]?.name || '—'}</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 多智能体动态博弈裁决看板 Modal */}
        <AnimatePresence>
          {showArbitrationModal && (
            <div className="fixed inset-0 z-[110] bg-slate-950/70 backdrop-blur-sm flex items-center justify-center p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.9, y: 20 }}
                className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl p-6 max-w-xl w-full shadow-2xl relative text-slate-800 dark:text-white max-h-[90vh] overflow-y-auto custom-scrollbar"
              >
                <button
                  onClick={() => setShowArbitrationModal(false)}
                  className="absolute top-5 right-5 text-slate-400 hover:text-slate-600 dark:hover:text-white p-1 rounded-full cursor-pointer"
                >
                  <X className="w-5 h-5" />
                </button>

                <div className="flex items-center gap-2.5 mb-2">
                  <div className="p-2 rounded-xl bg-amber-500/10 text-amber-500 border border-amber-500/20">
                    <Scale className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="font-black text-base">多智能体博弈裁决看板</h3>
                    <p className="text-xs text-slate-400">黑板协作裁决：预算红线与帕累托优化</p>
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-2 my-4">
                  <div className="bg-slate-50 dark:bg-slate-800/50 rounded-2xl p-2.5 text-center">
                    <div className="text-[10px] text-slate-400 font-bold">Day {timelineDay} 已计价</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">
                      {pricedNodes.length > 0 ? `¥${pricedTotal}` : '—'}
                    </div>
                  </div>
                  <div className="bg-slate-50 dark:bg-slate-800/50 rounded-2xl p-2.5 text-center">
                    <div className="text-[10px] text-slate-400 font-bold">日均预算</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">¥{dailyBudget || '—'}</div>
                  </div>
                  <div className={`rounded-2xl p-2.5 text-center ${hasOverrun ? 'bg-rose-50 dark:bg-rose-950/40' : unpricedNodes.length > 0 ? 'bg-amber-50 dark:bg-amber-950/40' : 'bg-emerald-50 dark:bg-emerald-950/40'}`}>
                    <div className={`text-[10px] font-bold ${hasOverrun ? 'text-rose-400' : unpricedNodes.length > 0 ? 'text-amber-500' : 'text-emerald-400'}`}>预算状态</div>
                    <div className={`text-sm font-black mt-0.5 ${hasOverrun ? 'text-rose-500' : unpricedNodes.length > 0 ? 'text-amber-600' : 'text-emerald-600 dark:text-emerald-400'}`}>
                      {dailyBudget <= 0 ? '未设' : hasOverrun ? `超 ¥${Math.round(overrun)}` : unpricedNodes.length > 0 ? '还有未计价' : '健康'}
                    </div>
                  </div>
                  <div className="bg-slate-50 dark:bg-slate-800/50 rounded-2xl p-2.5 text-center">
                    <div className="text-[10px] text-slate-400 font-bold">待你确认</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">
                      {unpricedNodes.length} <span className="text-[9px] text-slate-400 font-normal">个节点</span>
                    </div>
                  </div>
                </div>

                {/* 预算使用进度：只按**已计价**部分算，并写清还有几个没计价（不混进合计） */}
                {dailyBudget > 0 && (
                  <div className="mb-4">
                    <div className="flex items-center justify-between text-[11px] font-bold mb-1.5">
                      <span className="text-slate-500">已计价花费 / 日均预算</span>
                      <span className={hasOverrun ? 'text-rose-500' : 'text-emerald-600'}>
                        {Math.round((pricedTotal / dailyBudget) * 100)}%
                      </span>
                    </div>
                    <div className="h-2.5 w-full rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${hasOverrun ? 'bg-gradient-to-r from-amber-500 to-rose-500' : 'bg-gradient-to-r from-emerald-500 to-teal-400'}`}
                        style={{ width: `${Math.min(100, Math.round((pricedTotal / dailyBudget) * 100))}%` }}
                      />
                    </div>
                    {unpricedNodes.length > 0 && (
                      <p className="mt-1 text-[10px] text-amber-600">
                        还有 {unpricedNodes.length} 个节点没有可核实价格，未计入上面的合计（取不到不当成 0 元）
                      </p>
                    )}
                  </div>
                )}

                {/* 成本分布：只画**有价格**的节点；一个都没有就如实说清 + 给核价入口，
                    而不是显示一堆 ¥0 和"成本最低=某酒店"这种假结论 */}
                {costSortedNodes.length > 0 ? (
                  <div className="mb-4 bg-slate-50 dark:bg-slate-800/40 rounded-2xl p-3.5">
                    <div className="text-[11px] font-bold text-slate-500 mb-2.5 flex items-center gap-1.5">
                      <Activity className="w-3.5 h-3.5 text-amber-500" /> 节点成本分布（TOP {costSortedNodes.length}）
                      {unpricedNodes.length > 0 && (
                        <span className="ml-auto text-[10px] text-amber-600">{unpricedNodes.length} 个未计价，未参与比较</span>
                      )}
                    </div>
                    <div className="space-y-1.5">
                      {costSortedNodes.map((node: any, i: number) => {
                        const cost = parseCostNumber(node.cost);
                        const w = Math.max(8, Math.round((cost / maxNodeCost) * 100));
                        return (
                          <div key={`costbar-${i}`} className="flex items-center gap-2">
                            <span className="text-[10px] text-slate-500 font-bold w-24 truncate shrink-0">{node.name}</span>
                            <div className="flex-1 h-3 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${i === 0 && hasOverrun ? 'bg-rose-500' : 'bg-orange-400'}`}
                                style={{ width: `${w}%` }}
                              />
                            </div>
                            <span className="text-[10px] text-slate-500 font-mono w-12 text-right shrink-0">{node.cost}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <div className="mb-4 bg-amber-50 dark:bg-slate-800/60 rounded-2xl p-3.5 border border-amber-200/60 dark:border-slate-700">
                    <div className="text-[11px] font-bold text-amber-700 dark:text-amber-400 mb-2 flex items-center gap-1.5">
                      <Activity className="w-3.5 h-3.5" /> 今天这些节点还没取到价格，无法比较贵/便宜
                    </div>
                    <p className="text-[11px] leading-relaxed text-slate-600 dark:text-slate-300 mb-2">
                      取不到的价格不会当成 0 元，也不会参与"成本最高/最低"。下面这些可以点开自己核一下：
                    </p>
                    <div className="space-y-1">
                      {(unpricedNodes.length > 0 ? unpricedNodes : dayNodes).slice(0, 4).map((item: any, i: number) => (
                        <div key={`unpriced-${i}`} className="flex items-baseline justify-between gap-3">
                          <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200 truncate">{item.name}</span>
                          <a
                            href={item.amap_url || `https://www.amap.com/search?query=${encodeURIComponent(item.name || '')}`}
                            target="_blank"
                            rel="noreferrer"
                            className="shrink-0 text-[11px] font-bold text-orange-600 underline underline-offset-2 hover:text-orange-700"
                          >
                            去核价
                          </a>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 👑 多日预算滚动预测 */}
                {dailyForecast.perDay.length > 1 && (
                  <div className="mb-4 bg-slate-50 dark:bg-slate-800/40 rounded-2xl p-3.5">
                    <div className="text-[11px] font-bold text-slate-500 mb-2.5 flex items-center gap-1.5">
                      <TrendingUp className="w-3.5 h-3.5 text-emerald-500" /> 多日预算滚动预测
                      <span className="ml-auto font-mono text-[10px] text-slate-400">
                        累计 ¥{dailyForecast.totalSpent} / ¥{dailyForecast.totalBudget || '—'}
                      </span>
                    </div>
                    <div className="flex items-end gap-2 h-20">
                      {dailyForecast.perDay.map((d) => {
                        const barH = Math.max(3, Math.round((d.cost / (dailyBudget > 0 ? dailyBudget : 1)) * 100));
                        return (
                          <div key={d.day} className="flex-1 flex flex-col items-center gap-1" title={`Day ${d.day}: ¥${d.cost}`}>
                            <div className={`w-full rounded-t ${d.over ? 'bg-rose-500' : 'bg-emerald-500/80'}`} style={{ height: `${Math.min(100, barH)}%` }} />
                            <span className={`text-[9px] font-bold ${d.over ? 'text-rose-500' : 'text-slate-500'}`}>D{d.day}</span>
                          </div>
                        );
                      })}
                    </div>
                    {dailyForecast.remaining < 0 && (
                      <div className="text-[10px] text-rose-500 font-bold mt-2">⚠ 预计超总预算 ¥{Math.abs(Math.round(dailyForecast.remaining))}，建议调低后续天数成本节点。</div>
                    )}
                  </div>
                )}

                <p className="text-xs text-slate-600 dark:text-slate-300 mb-4 leading-relaxed bg-amber-50 dark:bg-slate-800/60 p-3 rounded-xl border border-amber-200/60 dark:border-slate-700">
                  {pricedNodes.length === 0
                    ? `精算 Agent 说明：【${cityName}】今天的节点都还没拿到可核实价格，所以这里不给"花了多少、超没超"的结论 —— 需要你确认（上方可点开核价），确认后预算判断才有依据。`
                    : hasOverrun
                      ? `精算 Agent 检测到【${cityName}】今日已计价消费 ¥${pricedTotal}，超出日均预算 ¥${dailyBudget} 约 ${overrunPercent}%（¥${Math.round(overrun)}）。建议优先优化最高成本节点【${highestCostNode?.name || '—'}】，或参考更低成本节点【${lowestCostNode?.name || '—'}】做平替。`
                      : `【${cityName}】今日已计价消费 ¥${pricedTotal}${dailyBudget > 0 ? `，在日均预算 ¥${dailyBudget} 以内（使用率 ${Math.round((pricedTotal / dailyBudget) * 100)}%）` : ''}${unpricedNodes.length > 0 ? `；另有 ${unpricedNodes.length} 个节点没有可核实价格，未计入` : ''}。`}
                </p>

                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={() => {
                      setActiveArbitrationTab('original');
                      const idx = routes.findIndex((r: any) => r.name === highestCostNode?.name);
                      if (idx >= 0) onPoiSelect(idx);
                      setShowArbitrationModal(false);
                    }}
                    className={`p-3.5 rounded-2xl border text-left transition-all cursor-pointer ${
                      activeArbitrationTab === 'original'
                        ? 'border-rose-500 bg-rose-50 dark:bg-rose-950/30 ring-2 ring-rose-500/20'
                        : 'border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40'
                    }`}
                  >
                    <div className="text-rose-500 font-bold text-xs mb-1">{hasOverrun ? '最高成本节点' : '今日成本最高'}</div>
                    <div className="text-xs font-bold truncate">{highestCostNode?.name || '暂无数据'}</div>
                    <div className="text-[10px] text-rose-500 mt-1 font-mono">{highestCostNode?.cost || '—'}</div>
                  </button>

                  <button
                    onClick={() => {
                      setActiveArbitrationTab('replacement');
                      const idx = routes.findIndex((r: any) => r.name === lowestCostNode?.name);
                      if (idx >= 0) onPoiSelect(idx);
                      setShowArbitrationModal(false);
                    }}
                    className={`p-3.5 rounded-2xl border text-left transition-all cursor-pointer ${
                      activeArbitrationTab === 'replacement'
                        ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30 ring-2 ring-emerald-500/20'
                        : 'border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40'
                    }`}
                  >
                    <div className="text-emerald-600 dark:text-emerald-400 font-bold text-xs mb-1 flex items-center gap-1">
                      <CheckCircle2 className="w-3.5 h-3.5" /> {hasOverrun ? '更低成本平替' : '今日成本最低'}
                    </div>
                    <div className="text-xs font-bold truncate">{lowestCostNode?.name || realReplacementNode.name}</div>
                    <div className="text-[10px] text-emerald-600 dark:text-emerald-400 mt-1 font-mono">{lowestCostNode?.cost || '性价比优先'}</div>
                  </button>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>

        {/* 全屏 行程数据看板 View */}
        <AnimatePresence>
          {viewState === 'MACRO' && (
            <motion.div 
              key="macro-view-dashboard"
              initial={{ opacity: 0 }} 
              animate={{ opacity: 1 }} 
              exit={{ opacity: 0, scale: 1.2 }}
              transition={{ duration: 0.5 }}
              className="absolute inset-0 w-full h-full z-20 overflow-hidden"
              style={{ background: radarBackground(mode) }}
            >
              {/* 星空背景层 */}
              <div className="absolute inset-0 pointer-events-none overflow-hidden">
                <div className="absolute inset-0" style={{
                  background: 'radial-gradient(1px 1px at 20% 30%, rgba(255,255,255,0.6), transparent), radial-gradient(1px 1px at 60% 70%, rgba(255,255,255,0.5), transparent), radial-gradient(1px 1px at 80% 20%, rgba(255,255,255,0.7), transparent), radial-gradient(2px 2px at 40% 80%, rgba(180,200,255,0.4), transparent), radial-gradient(1px 1px at 10% 60%, rgba(255,255,255,0.5), transparent), radial-gradient(1px 1px at 90% 50%, rgba(255,255,255,0.6), transparent), radial-gradient(1px 1px at 50% 10%, rgba(200,220,255,0.5), transparent), radial-gradient(2px 2px at 70% 90%, rgba(255,255,255,0.3), transparent), radial-gradient(1px 1px at 30% 90%, rgba(255,255,255,0.5), transparent), radial-gradient(1px 1px at 15% 15%, rgba(255,255,255,0.4), transparent)',
                }} />
                <div className="absolute inset-0 opacity-20" style={{
                  background: 'radial-gradient(ellipse 80% 20% at 70% 30%, rgba(56,189,248,0.15), transparent), radial-gradient(ellipse 60% 15% at 20% 70%, rgba(139,92,246,0.1), transparent)'
                }} />
              </div>
              {/* 网格背景 */}
              <div className="absolute inset-0 opacity-[0.04] pointer-events-none" style={{
                backgroundImage: 'linear-gradient(rgba(56,189,248,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(56,189,248,0.5) 1px, transparent 1px)',
                backgroundSize: '80px 80px'
              }} />

              {/* 扫描线 / 暗角 */}
              <div className="absolute inset-0 pointer-events-none radar-scanlines opacity-70" />
              <div className="absolute inset-0 pointer-events-none radar-vignette" />

              {/* 顶部HUD */}
              <div className="absolute top-0 left-0 right-0 z-50 px-6 py-4 flex items-center justify-between pointer-events-none">
                <div className="flex items-center gap-4 pointer-events-auto">
                  <div className="relative">
                    <div className="w-10 h-10 rounded-xl bg-sky-500/10 border border-sky-500/30 flex items-center justify-center">
                      <BarChart3 className="w-5 h-5 text-sky-600 dark:text-sky-400" />
                    </div>
                    <div className="absolute inset-0 w-10 h-10 rounded-xl bg-sky-400/20 animate-ping" />
                  </div>
                  <div>
                    <div className="flex items-center gap-3">
                      <h2 className="text-slate-900 dark:text-white font-black text-xl tracking-[0.25em]" style={{ fontFamily: RADAR_FONT.display, textShadow: '0 0 20px rgba(56,189,248,0.45)' }}>OMNIROUTE</h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded border border-orange-500/30 text-orange-600 dark:text-orange-300 bg-orange-500/10 tracking-wider">
                        行程数据看板
                      </span>
                      <span className="text-[10px] text-slate-500" style={{ fontFamily: RADAR_FONT.data }}>v3.0</span>
                    </div>
                    <div className="flex items-center gap-4 mt-0.5">
                      <p className="text-[11px] text-slate-700 dark:text-slate-300 tracking-wide" style={{ fontFamily: RADAR_FONT.data }}>
                        {cityName} <span className="text-slate-500">·</span> {routes.length} 节点 <span className="text-slate-500">·</span> 今日 <span className="text-amber-600 dark:text-amber-300">¥{dayTotal}</span>
                      </p>
                      <span className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ${hasOverrun ? 'bg-rose-500/10 text-rose-500' : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'}`}>
                        {hasOverrun ? '超支预警' : dailyBudget > 0 ? '预算健康' : '未设预算'}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 pointer-events-auto">
                  <button onClick={() => setShowArbitrationModal(true)} className="h-8 px-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-300 hover:bg-amber-500/20 transition-all flex items-center gap-1.5 text-[10px] font-bold cursor-pointer">
                    <Scale className="w-3 h-3" /> 裁决看板
                  </button>
                  <button onClick={() => setViewState('MICRO')} className="h-8 px-3 rounded-lg bg-slate-900/5 dark:bg-white/5 border border-slate-300/70 dark:border-white/10 text-slate-700 dark:text-slate-300 hover:bg-slate-900/10 dark:hover:bg-white/10 transition-all flex items-center gap-1.5 text-[10px] font-bold cursor-pointer">
                    <MapIcon className="w-3 h-3" /> 2D地图
                  </button>
                </div>
              </div>

              {/* 行程数据看板 */}
              <TripMacroDashboard
                mode={mode}
                timelineDay={timelineDay}
                timelineDays={timelineDays}
                routes={routes}
                dayTotal={dayTotal}
                dailyBudget={dailyBudget}
                memberCount={memberCount}
                avgCost={avgCost}
                overrun={overrun}
                hasOverrun={hasOverrun}
                costSortedNodes={costSortedNodes}
                maxNodeCost={maxNodeCost}
                dailyForecast={dailyForecast}
                onDayChange={setTimelineDay}
                onPoiSelect={(idx: number) => { onPoiSelect(idx); setReturnDashboardNode(idx); setViewState('MICRO'); }}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* 全局动画样式（统一共享动画与滚动条 CSS） */}
      <style>{`
        ${radarSharedCss(mode)}
        .animate-marquee { animation-duration: 25s; }
        @keyframes risk-pulse-halo {
          0% { box-shadow: 0 0 0 0 rgba(244, 63, 94, 0.7); }
          70% { box-shadow: 0 0 0 18px rgba(244, 63, 94, 0); }
          100% { box-shadow: 0 0 0 0 rgba(244, 63, 94, 0); }
        }
        .risk-pulse-halo {
          animation: risk-pulse-halo 2s ease-out infinite;
        }
      `}</style>
    </div>
  );
}
