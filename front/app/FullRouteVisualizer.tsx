'use client';

import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import dynamic from 'next/dynamic';
import { 
  Globe2, 
  Map as MapIcon, 
  ShieldCheck, 
  AlertTriangle, 
  Activity, 
  ArrowLeft,
  CheckCircle2,
  AlertCircle,
  X,
  Scale,
  MapPin,
  Car,
  Hotel,
  RotateCw
} from 'lucide-react';
import InteractiveAmapComponent, { RoutePoint, SafetyInfo } from './InteractiveAmapComponent';

// 👑 SSR 安全导入：动态加载 3D 地球组件
const Globe = dynamic(() => import('react-globe.gl'), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 z-[120] bg-slate-950/90 backdrop-blur-md text-white flex flex-col items-center justify-center gap-3">
      <div className="w-8 h-8 border-4 border-orange-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-xs font-bold text-slate-300">正在载入 WorldMonitor 3D 全息态势雷达...</span>
    </div>
  )
}) as any;

// 👑 智能实景图片渲染组件：彻底解决张冠李戴与破图问题
export function RealPoiImage({ photoUrl, poiName, className = '' }: { photoUrl?: string; poiName: string; className?: string }) {
  const [imgError, setImgError] = useState(false);

  // 统一升级 https + 兼容协议相对 `//`，禁用 Referer 绕过 CDN 防盗链
  const normalizedUrl = (photoUrl && typeof photoUrl === 'string')
    ? (photoUrl.startsWith('//') ? 'https:' + photoUrl : photoUrl.startsWith('http://') ? 'https://' + photoUrl.slice(7) : photoUrl)
    : '';
  const isValidUrl = Boolean(normalizedUrl.startsWith('https://'));

  const showReal = isValidUrl && !imgError;

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 rounded-xl ${className}`}>
      {showReal ? (
        <img
          src={normalizedUrl}
          alt={poiName}
          onError={() => setImgError(true)}
          className="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
          loading="lazy"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className="flex flex-col items-center justify-center gap-1 w-full h-full">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-8 h-8 text-slate-300 dark:text-slate-600">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <circle cx="8.5" cy="10" r="1.5" />
            <path d="m21 15-5-5L5 21" />
          </svg>
          <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 text-center px-2">{poiName}·实景暂不可用</span>
        </div>
      )}
      <div className="absolute top-1.5 right-1.5 bg-slate-900/80 backdrop-blur text-[10px] text-orange-300 font-bold px-2 py-0.5 rounded-full border border-orange-500/30 pointer-events-none">
        {showReal ? '📷 高德实景' : '实景暂不可用'}
      </div>
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

// 从"门票 ¥45/人"、"人均餐饮 ¥65"、"住宿预留 ¥380/晚"等成本文本中解析出数字金额
function parseCostNumber(cost?: string): number {
  if (!cost) return 0;
  const m = String(cost).match(/¥\s*([\d.]+)/);
  return m ? parseFloat(m[1]) : 0;
}

interface FullRouteVisualizerProps {
  phase: 'drafting' | 'deduction' | 'decision';
  routes?: RoutePoint[];
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
  // 控制 2D 地图 (MICRO) 与 3D 态势雷达 (MACRO)
  const [viewState, setViewState] = useState<'MACRO' | 'MICRO'>('MICRO');
  const [windowReady, setWindowReady] = useState(false);
  const [showArbitrationModal, setShowArbitrationModal] = useState(false);
  const [activeArbitrationTab, setActiveArbitrationTab] = useState<'replacement' | 'original'>('replacement');

  const containerRef = useRef<HTMLDivElement>(null);
  const [globeDimensions, setGlobeDimensions] = useState({ width: 800, height: 600 });
  const globeEl = useRef<any>(null);
  const [macroAutoRotate, setMacroAutoRotate] = useState(true);

  useEffect(() => {
    setWindowReady(true);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    
    const updateDimensions = () => {
      if (containerRef.current) {
        setGlobeDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight
        });
      }
    };

    updateDimensions();
    const observer = new ResizeObserver(() => updateDimensions());
    observer.observe(containerRef.current);

    return () => observer.disconnect();
  }, [windowReady, viewState]);

  useEffect(() => {
    if (targetCityInfo && globeEl.current && viewState === 'MACRO') {
      globeEl.current.pointOfView({ 
        lat: targetCityInfo.lnglat[1], 
        lng: targetCityInfo.lnglat[0], 
        altitude: 1.6
      }, 2000);
    }
  }, [targetCityInfo, viewState]);

  // 地球自动巡航：默认缓慢自转，支持手动拖拽探索（带阻尼）
  useEffect(() => {
    if (globeEl.current && globeEl.current.controls) {
      const ctrl = globeEl.current.controls();
      if (ctrl) {
        ctrl.autoRotate = macroAutoRotate;
        ctrl.autoRotateSpeed = 0.85;
        ctrl.enableDamping = true;
        ctrl.dampingFactor = 0.08;
      }
    }
  }, [macroAutoRotate, globeDimensions, viewState]);

  const destLng = routes.length > 0 ? routes[0].lnglat[0] : (targetCityInfo?.lnglat[0] || 110.34);
  const destLat = routes.length > 0 ? routes[0].lnglat[1] : (targetCityInfo?.lnglat[1] || 20.03);
  const cityName = targetCityInfo?.name || (routes.length > 0 ? routes[0].name : '目标城市');
  
  const hasRealCii = typeof safetyInfo?.cii_score === 'number';
  const ciiScore = hasRealCii ? (safetyInfo?.cii_score as number) : 0;
  const isHighRisk = hasRealCii && ciiScore > 30;

  const macroArcs: any[] = [
    { startLat: 39.90, startLng: 116.40, endLat: destLat, endLng: destLng, color: ['#ffffff', '#f97316'] },
    { startLat: 31.23, startLng: 121.47, endLat: destLat, endLng: destLng, color: ['#f97316', '#eab308'] } 
  ];

  if (routes.length > 1) {
    for (let i = 0; i < routes.length - 1; i++) {
      macroArcs.push({
        startLat: routes[i].lnglat[1],
        startLng: routes[i].lnglat[0],
        endLat: routes[i + 1].lnglat[1],
        endLng: routes[i + 1].lnglat[0],
        color: ['#f97316', '#38bdf8']
      });
    }
  }

  const elementsData = routes.length > 0 
    ? routes.map((pt, idx) => ({
        name: pt.name,
        desc: pt.desc || '全域雷达正在扫描当地时空拓扑，监控路况与气象...',
        lat: pt.lnglat[1],
        lng: pt.lnglat[0],
        index: idx + 1,
        isMain: idx === 0,
        cii: ciiScore
      }))
    : (targetCityInfo ? [{
        name: targetCityInfo.name,
        desc: '目标城市核心中枢拓扑已接入...',
        lat: targetCityInfo.lnglat[1],
        lng: targetCityInfo.lnglat[0],
        index: 1,
        isMain: true,
        cii: ciiScore
      }] : []);

  const ringsData = [
    {
      lat: destLat,
      lng: destLng,
      maxR: ciiScore / 3 + 4,
      propagationSpeed: 2,
      repeatPeriod: 1000,
      color: isHighRisk ? 'rgba(244, 63, 94, 0.8)' : 'rgba(245, 158, 11, 0.8)'
    }
  ];

  const realReplacementNode = routes.find(r => r.tags?.includes('住宿') || r.name.includes('酒店') || r.name.includes('民宿')) 
    || (routes.length > 0 ? routes[routes.length - 1] : { name: '精选品质宿所/特色体验点' });

  // 👑 基于真实节点成本计算博弈裁决数据（替代硬编码的"预算偏离红线"话术）
  const dayNodes = (routes || []).filter((r: any) => r && typeof r.name === 'string');
  const dayTotal = dayNodes.reduce((sum: number, r: any) => sum + parseCostNumber(r.cost), 0);
  const dailyBudget = Number(budgetData?.daily_avg || budgetData?.total_budget || 0);
  const highestCostNode = dayNodes.length > 0
    ? dayNodes.reduce((max: any, r: any) => (parseCostNumber(r.cost) > parseCostNumber(max.cost) ? r : max), dayNodes[0])
    : null;
  const lowestCostNode = dayNodes.length > 1
    ? dayNodes.reduce((min: any, r: any) => (parseCostNumber(r.cost) < parseCostNumber(min.cost) ? r : min), dayNodes[0])
    : null;
  const overrun = dailyBudget > 0 ? dayTotal - dailyBudget : 0;
  const hasOverrun = overrun > 0;
  const avgCost = dayNodes.length > 0 ? Math.round(dayTotal / dayNodes.length) : 0;
  const overrunPercent = dailyBudget > 0 ? Math.max(0, Math.round((overrun / dailyBudget) * 100)) : 0;
  // 按成本降序排列的节点，用于决策看板成本条形图
  const costSortedNodes = [...dayNodes].sort((a, b) => parseCostNumber(b.cost) - parseCostNumber(a.cost)).slice(0, 6);
  const maxNodeCost = costSortedNodes.length > 0 ? Math.max(1, parseCostNumber(costSortedNodes[0].cost)) : 1;

  if (!windowReady) return null;

  return (
    <div ref={containerRef} className="relative w-full h-full bg-slate-900 overflow-hidden flex select-none">
      
      {/* 主地图视图 */}
      <div className="relative flex-1 h-full w-full">
        <InteractiveAmapComponent 
          phase={phase}
          selectedPoiIndex={selectedPoiIndex}
          onPoiSelect={onPoiSelect}
          luoyangRoute={routes} 
          actualPath={actualPath}
          safetyInfo={safetyInfo}
          weatherInfo={weatherInfo}
          trafficInfo={trafficInfo}
          onExit={onExit} 
          onWakeAgent={onWakeAgent}
        />

        {/* 👑 彻底解决遮挡问题：动作条置于右上角，图层优先级升至 z-50，高亮“返回大厅”按钮 */}
        <div className="absolute top-6 right-6 z-50 flex items-center gap-2.5 pointer-events-auto flex-wrap justify-end">
          <button
            onClick={() => setShowArbitrationModal(true)}
            className="bg-slate-900/90 hover:bg-slate-800 text-amber-400 border border-amber-500/40 px-3.5 py-2 rounded-2xl shadow-2xl flex items-center gap-2 text-xs font-bold transition-all hover:scale-105 cursor-pointer backdrop-blur-md"
          >
            <Scale className="w-4 h-4 text-amber-400" />
            <span>博弈裁决看板</span>
          </button>

          <button
            onClick={() => setViewState(prev => prev === 'MACRO' ? 'MICRO' : 'MACRO')}
            className="bg-slate-900/90 hover:bg-slate-800 text-white px-3.5 py-2 rounded-2xl border border-slate-700 shadow-2xl flex items-center gap-2 text-xs font-bold transition-all hover:scale-105 cursor-pointer backdrop-blur-md"
          >
            {viewState === 'MACRO' ? (
              <>
                <MapIcon className="w-4 h-4 text-orange-400" />
                <span>返回 2D 导航</span>
              </>
            ) : (
              <>
                <Globe2 className="w-4 h-4 text-cyan-400 animate-spin-slow" />
                <span>3D 风控雷达</span>
              </>
            )}
          </button>

          {/* 👑 “返回大厅”按钮：高亮橙色、清晰文字、防遮挡 */}
          {onExit && (
            <button 
              onClick={onExit} 
              className="bg-orange-500 hover:bg-orange-600 text-white backdrop-blur-md px-4 py-2 rounded-2xl border border-orange-400 shadow-2xl flex items-center gap-1.5 text-xs font-bold transition-all hover:scale-105 cursor-pointer"
              title="返回大厅"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>返回大厅</span>
            </button>
          )}
        </div>

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
                    <div className="text-[10px] text-slate-400 font-bold">今日总消费</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">¥{dayTotal}</div>
                  </div>
                  <div className="bg-slate-50 dark:bg-slate-800/50 rounded-2xl p-2.5 text-center">
                    <div className="text-[10px] text-slate-400 font-bold">日均预算</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">¥{dailyBudget || '—'}</div>
                  </div>
                  <div className={`rounded-2xl p-2.5 text-center ${hasOverrun ? 'bg-rose-50 dark:bg-rose-950/40' : 'bg-emerald-50 dark:bg-emerald-950/40'}`}>
                    <div className={`text-[10px] font-bold ${hasOverrun ? 'text-rose-400' : 'text-emerald-400'}`}>预算状态</div>
                    <div className={`text-sm font-black mt-0.5 ${hasOverrun ? 'text-rose-500' : 'text-emerald-600 dark:text-emerald-400'}`}>
                      {dailyBudget > 0 ? (hasOverrun ? `超 ¥${Math.round(overrun)}` : '健康') : '未设'}
                    </div>
                  </div>
                  <div className="bg-slate-50 dark:bg-slate-800/50 rounded-2xl p-2.5 text-center">
                    <div className="text-[10px] text-slate-400 font-bold">人均估算</div>
                    <div className="text-sm font-black text-slate-800 dark:text-white mt-0.5">¥{avgCost}</div>
                  </div>
                </div>

                {/* 预算使用进度条 */}
                {dailyBudget > 0 && (
                  <div className="mb-4">
                    <div className="flex items-center justify-between text-[11px] font-bold mb-1.5">
                      <span className="text-slate-500">预算使用进度</span>
                      <span className={hasOverrun ? 'text-rose-500' : 'text-emerald-600'}>{Math.round((dayTotal / dailyBudget) * 100)}%</span>
                    </div>
                    <div className="h-2.5 w-full rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${hasOverrun ? 'bg-gradient-to-r from-amber-500 to-rose-500' : 'bg-gradient-to-r from-emerald-500 to-teal-400'}`}
                        style={{ width: `${Math.min(100, Math.round((dayTotal / dailyBudget) * 100))}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* 成本最高节点条形图 */}
                {costSortedNodes.length > 0 && (
                  <div className="mb-4 bg-slate-50 dark:bg-slate-800/40 rounded-2xl p-3.5">
                    <div className="text-[11px] font-bold text-slate-500 mb-2.5 flex items-center gap-1.5">
                      <Activity className="w-3.5 h-3.5 text-amber-500" /> 节点成本分布（TOP {costSortedNodes.length}）
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
                )}

                <p className="text-xs text-slate-600 dark:text-slate-300 mb-4 leading-relaxed bg-amber-50 dark:bg-slate-800/60 p-3 rounded-xl border border-amber-200/60 dark:border-slate-700">
                  {hasOverrun
                    ? `精算 Agent 检测到【${cityName}】今日估算消费 ¥${dayTotal}，超出日均预算 ¥${dailyBudget} 约 ${overrunPercent}%（¥${Math.round(overrun)}）。建议优先优化最高成本节点【${highestCostNode?.name || '—'}】，或参考更低成本节点【${lowestCostNode?.name || '—'}】进行团队平替置换。`
                    : `【${cityName}】今日估算消费 ¥${dayTotal}${dailyBudget > 0 ? `，处于日均预算 ¥${dailyBudget} 以内（使用率 ${Math.round((dayTotal / dailyBudget) * 100)}%）` : ''}。多智能体已完成成本与体验的多目标帕累托权衡，当前组合预算健康。`}
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

        {/* 全屏 3D 风控雷达 View */}
        <AnimatePresence>
          {viewState === 'MACRO' && (
            <motion.div 
              key="macro-view-globe"
              initial={{ opacity: 0 }} 
              animate={{ opacity: 1 }} 
              exit={{ opacity: 0, scale: 1.2 }}
              transition={{ duration: 0.5 }}
              className="absolute inset-0 w-full h-full z-20 bg-slate-950 flex items-center justify-center cursor-grab active:cursor-grabbing"
            >
              <Globe
                ref={globeEl}
                globeImageUrl="//unpkg.com/three-globe/example/img/earth-night.jpg"
                bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
                backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
                
                width={globeDimensions.width}
                height={globeDimensions.height}
                
                showAtmosphere={true}
                atmosphereColor="#3b82f6"
                atmosphereAltitude={0.2}

                arcsData={macroArcs}
                arcStartLat="startLat" arcStartLng="startLng" 
                arcEndLat="endLat" arcEndLng="endLng"
                arcColor="color" 
                arcDashLength={0.4} arcDashGap={0.2} arcDashAnimateTime={1800}
                arcStroke={1.3}

                ringsData={ringsData}
                ringColor="color"
                ringMaxRadius="maxR"
                ringPropagationSpeed="propagationSpeed"
                ringRepeatPeriod="repeatPeriod"
                
                htmlElementsData={elementsData}
                htmlElement={(d: any) => {
                   const el = document.createElement('div');
                   el.style.pointerEvents = 'auto';
                   el.style.cursor = 'pointer';
                   // 👑 态势节点可交互：点击聚焦到 2D 地图对应节点
                   el.onclick = () => {
                     if (d.index) onPoiSelect(d.index - 1);
                     setViewState('MICRO');
                   };
                   el.innerHTML = `
                    <div class="flex flex-col items-center group cursor-pointer">
                      <div class="bg-slate-900/90 text-white p-3 rounded-2xl shadow-[0_10px_35px_rgba(249,115,22,0.4)] border border-orange-500/50 max-w-xs transform transition-all duration-300 hover:scale-110 backdrop-blur">
                        <div class="flex items-center justify-between gap-2 mb-1">
                          <span class="text-[10px] font-black text-white bg-orange-500 px-2 py-0.5 rounded-full">
                            打卡点 ${d.index}
                          </span>
                          <span class="text-[10px] text-slate-400 font-mono">${d.isMain ? '起点/核心' : '全息节点'}</span>
                        </div>
                        <h3 class="text-sm font-black text-orange-400 tracking-tight">${d.name}</h3>
                        <p class="text-[11px] text-slate-300 line-clamp-2 mt-1 leading-relaxed">${d.desc}</p>
                        <div class="text-[9px] text-orange-400/70 mt-1.5 text-center">点击聚焦 2D 导航定位</div>
                      </div>
                      <div class="w-0.5 h-6 bg-gradient-to-b from-orange-500 to-transparent opacity-80"></div>
                      <div class="w-3.5 h-3.5 bg-orange-500 rounded-full ring-4 ring-orange-300/80 shadow-[0_0_20px_#f97316] animate-ping"></div>
                    </div>
                   `;
                   return el;
                }}
              />
              
              <div className="absolute top-8 left-8 z-10 pointer-events-none">
                <div className="bg-slate-900/90 backdrop-blur-md border border-slate-800 shadow-2xl p-5 rounded-3xl max-w-sm text-white">
                  <h2 className="text-xl font-black text-white tracking-tight flex items-center gap-2.5">
                    <div className={`w-3 h-3 rounded-full ${phase === 'deduction' ? 'bg-rose-500 animate-pulse' : 'bg-rose-500'}`} />
                    <span>OmniRoute 3D 风控雷达</span>
                    <span className="text-[10px] bg-rose-500/20 text-rose-300 border border-rose-400/40 px-2 py-0.5 rounded-full font-mono font-bold">
                      风控 RISK CONTROL
                    </span>
                  </h2>
                  <p className="text-slate-400 mt-1.5 text-xs font-medium leading-relaxed">
                    {phase === 'deduction' 
                      ? `多智能体正在评估【${cityName}】CII 风险与安全隐患，计算最优时空拓扑...` 
                      : `已评估【${cityName}】综合风险，覆盖 ${routes.length} 个节点的 CII 风险与安全隐患链路。`}
                  </p>
                </div>
              </div>

              {/* 自动巡航开关 */}
              <div className="absolute top-24 left-8 z-10 pointer-events-auto">
                <button
                  onClick={() => setMacroAutoRotate(v => !v)}
                  className={`backdrop-blur-md px-3.5 py-2 rounded-2xl border shadow-2xl transition-all hover:scale-105 cursor-pointer flex items-center gap-2 text-xs font-bold ${
                    macroAutoRotate
                      ? 'bg-sky-500/20 text-sky-300 border-sky-400/40'
                      : 'bg-slate-900/80 text-slate-300 border-slate-700'
                  }`}
                >
                  <RotateCw className={`w-4 h-4 ${macroAutoRotate ? 'animate-spin-slow' : ''}`} />
                  <span>{macroAutoRotate ? '自动巡航 开' : '自动巡航 关'}</span>
                </button>
              </div>

              {/* WorldMonitor 全息安全简报 */}
              <div className="absolute bottom-8 left-8 z-10 w-96 bg-slate-900/90 backdrop-blur-md border border-slate-800 p-5 rounded-3xl text-white shadow-2xl space-y-3 pointer-events-auto">
                <div className="flex items-center justify-between pb-2.5 border-b border-slate-800">
                  <div className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-orange-400" />
                    <span className="font-bold text-sm text-orange-400">WorldMonitor 安全风控简报</span>
                  </div>
                  <span className="text-xs font-mono bg-orange-500/20 text-orange-300 border border-orange-500/30 px-2.5 py-0.5 rounded-full">
                    CII: {hasRealCii ? ciiScore.toFixed(1) : '待评估'}
                  </span>
                </div>

                {/* CII 综合风险仪表盘 */}
                {hasRealCii && (
                  <div className="bg-slate-800/50 p-3 rounded-xl space-y-2">
                    <div className="flex items-center justify-between text-[11px] font-bold">
                      <span className="text-slate-300">综合风险指数</span>
                      <span className={ciiScore > 40 ? 'text-rose-400' : ciiScore > 20 ? 'text-amber-400' : 'text-emerald-400'}>
                        {ciiScore > 40 ? '高危' : ciiScore > 20 ? '预警' : '安全'}
                      </span>
                    </div>
                    <div className="relative h-2.5 w-full rounded-full bg-slate-700/70 overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{ width: `${Math.max(0, Math.min(100, ciiScore))}%`, background: 'linear-gradient(90deg, #10b981, #f59e0b, #f43f5e)' }}
                      />
                    </div>
                    <div className="flex justify-between text-[9px] font-mono text-slate-500">
                      <span>0</span><span>20</span><span>40</span><span>100</span>
                    </div>
                  </div>
                )}

                {/* 实时气象 + 路况：来自高德/心知真实数据，非写死占位 */}
                <div className="grid grid-cols-2 gap-2">
                  <div className="bg-slate-800/60 p-2.5 rounded-xl">
                    <div className="text-[10px] text-slate-400 font-bold mb-1">实时气象</div>
                    <div className="text-xs font-bold text-slate-200">
                      {weatherInfo?.condition ? String(weatherInfo.condition) : '获取中'}
                    </div>
                  </div>
                  <div className="bg-slate-800/60 p-2.5 rounded-xl">
                    <div className="text-[10px] text-slate-400 font-bold mb-1">实时路况</div>
                    <div className="text-xs font-bold text-slate-200">
                      {(trafficInfo?.description || trafficInfo?.advice) ? String(trafficInfo.description || trafficInfo.advice) : '数据获取中'}
                    </div>
                  </div>
                </div>

                <div className="space-y-2 text-xs text-slate-300 max-h-40 overflow-y-auto pr-1">
                  {safetyInfo?.active_alerts && safetyInfo.active_alerts.length > 0 ? (
                    safetyInfo.active_alerts.map((alert, idx) => (
                      <div key={idx} className="bg-slate-800/80 p-2.5 rounded-xl border border-slate-700/80">
                        <div className="font-bold text-amber-400 mb-1 flex items-center gap-1.5">
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                          <span>{alert.title}</span>
                        </div>
                        <div className="text-[11px] text-slate-300 leading-relaxed">{alert.detail}</div>
                      </div>
                    ))
                  ) : (
                    <div className="space-y-2">
                      <div className="bg-slate-800/60 p-2.5 rounded-xl border border-slate-700/60">
                        <div className="font-bold text-slate-300 mb-1 flex items-center gap-1">
                          <ShieldCheck className="w-3.5 h-3.5" /> 实时风控状态
                        </div>
                        <p className="text-[11px] text-slate-400 leading-relaxed">
                          {hasRealCii
                            ? `【${cityName}】综合风险指数 CII ${ciiScore.toFixed(1)}（${safetyInfo?.risk_level || 'LOW'}），当前无中高风险预警。`
                            : `【${cityName}】暂未接入第三方实时风控数据，风险评级待评估；安全数据源就绪后将自动刷新。`}
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                {safetyInfo?.safety_advice && (
                  <div className="pt-2 border-t border-slate-800 text-[11px] text-slate-300 leading-relaxed">
                    <span className="text-orange-400 font-bold">智能体避险建议：</span>
                    {safetyInfo.safety_advice}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

    </div>
  );
}