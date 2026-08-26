'use client';

import React, { useEffect, useRef, useState } from 'react';
import Globe from 'react-globe.gl';
import { ShieldCheck, ShieldAlert, AlertTriangle, Wind, Compass, X, Activity, Navigation2, RotateCw, Gauge, MapPin } from 'lucide-react';

// 根据 CII 指数返回风险等级与配色（统一供地球水波、图例、仪表盘使用）
function riskProfile(ciiScore: number) {
  if (ciiScore >= 40) return { label: '高危严碍', color: '#f43f5e', text: 'text-rose-400', bg: 'bg-rose-500', ring: 'rgba(244, 63, 94, 0.85)' };
  if (ciiScore >= 20) return { label: '中度预警', color: '#f59e0b', text: 'text-amber-400', bg: 'bg-amber-500', ring: 'rgba(245, 158, 11, 0.85)' };
  return { label: '安全区间', color: '#10b981', text: 'text-emerald-400', bg: 'bg-emerald-500', ring: 'rgba(16, 185, 129, 0.8)' };
}

export interface SafetyAlert {
  type: string;
  level: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  detail: string;
}

export interface SafetyInfo {
  city?: string;
  cii_score?: number;
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH';
  active_alerts?: SafetyAlert[];
  safety_advice?: string;
}

export interface RoutePoint {
  name: string;
  lnglat: [number, number];
  desc?: string;
  time?: string;
  transport?: string;
}

interface WorldSafetyGlobeProps {
  targetCity: string;
  cityCoords?: [number, number]; // [lng, lat]
  originCoords?: [number, number]; // 出发地坐标，用于绘制 3D 航线弧线
  routePoints?: RoutePoint[]; // 👑 全线景点列表：在 3D 地球上全息呈现与路线串联
  safetyInfo?: SafetyInfo;
  weatherCondition?: string;
  trafficSummary?: string; // 实时路况摘要（来自高德交通 API）
  onClose: () => void;
}

export default function WorldSafetyGlobe({
  targetCity,
  cityCoords = [104.0665, 30.5722],
  originCoords = [116.4074, 39.9042], // 默认起点（如北京）
  routePoints = [],
  safetyInfo,
  weatherCondition = '多云 24°C',
  trafficSummary = '实时路况良好，整体畅通',
  onClose
}: WorldSafetyGlobeProps) {
  const globeRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [autoRotate, setAutoRotate] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);

  // 1. 动态监控自适应父容器尺寸，彻底解决右侧/底部黑屏与画面剪裁问题
  useEffect(() => {
    const updateSize = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight
        });
      }
    };
    updateSize();
    window.addEventListener('resize', updateSize);
    return () => window.removeEventListener('resize', updateSize);
  }, []);

  const destLng = routePoints.length > 0 ? routePoints[0].lnglat[0] : cityCoords[0];
  const destLat = routePoints.length > 0 ? routePoints[0].lnglat[1] : cityCoords[1];
  const origLng = originCoords[0];
  const origLat = originCoords[1];

  // 👑 真实 CII 判定：未收到 safety_info 时不再伪装成“12.5 安全”，而是明确标注“待评估”，杜绝误导
  const hasRealCii = typeof safetyInfo?.cii_score === 'number';
  const ciiScore = hasRealCii ? (safetyInfo!.cii_score as number) : 0;
  const profile = riskProfile(ciiScore);
  // CII 归一化 0-100 用于仪表盘进度条
  const ciiPercent = hasRealCii ? Math.max(0, Math.min(100, ciiScore)) : 0;

  useEffect(() => {
    if (globeRef.current) {
      // 视角平滑转动并聚焦到目标城市/核心景点
      globeRef.current.pointOfView(
        { lat: destLat, lng: destLng, altitude: 1.6 },
        2200
      );
    }
  }, [destLat, destLng]);

  // 2. 地球自动巡航开关：默认缓慢自转，用户可关闭后手动拖拽探索
  useEffect(() => {
    const ctrl = globeRef.current?.controls?.();
    if (ctrl) {
      ctrl.autoRotate = autoRotate;
      ctrl.autoRotateSpeed = 0.85;
      ctrl.enableDamping = true;
      ctrl.dampingFactor = 0.08;
    }
  }, [autoRotate, dimensions, globeRef]);

  // 2. 3D HTML 标注数据：包含目标城市与全行程景点打卡标记
  const htmlElementsData = routePoints.length > 0
    ? routePoints.map((pt, idx) => ({
        lat: pt.lnglat[1],
        lng: pt.lnglat[0],
        city: pt.name,
        index: idx + 1,
        isOrigin: idx === 0,
        cii: ciiScore,
        desc: pt.desc || '',
        weather: weatherCondition
      }))
    : [
        {
          lat: destLat,
          lng: destLng,
          city: targetCity,
          index: 1,
          isOrigin: true,
          cii: ciiScore,
          desc: '目标游玩城市中心节点',
          weather: weatherCondition
        }
      ];

  // 3. 3D 航线弧线 (Arcs) —— 从出发地连向目的地，并串联全线景点打卡动线
  const arcsData: any[] = [
    {
      startLat: origLat,
      startLng: origLng,
      endLat: destLat,
      endLng: destLng,
      color: ['#3b82f6', '#f97316'],
      label: '远程航线'
    }
  ];

  // 若有多个 POI，串联相邻景点 3D 轨迹
  if (routePoints.length > 1) {
    for (let i = 0; i < routePoints.length - 1; i++) {
      arcsData.push({
        startLat: routePoints[i].lnglat[1],
        startLng: routePoints[i].lnglat[0],
        endLat: routePoints[i + 1].lnglat[1],
        endLng: routePoints[i + 1].lnglat[0],
        color: ['#f97316', '#38bdf8'],
        label: `节点 ${i + 1} → ${i + 2}`
      });
    }
  }

  // 4. 3D 动态水波纹
  const ringsData = [
    {
      lat: destLat,
      lng: destLng,
      maxR: ciiScore / 4 + 4,
      propagationSpeed: 2,
      repeatPeriod: 1000,
      color: profile.ring
    }
  ];

  return (
    <div ref={containerRef} className="fixed inset-0 z-[100] bg-slate-950 flex flex-col items-center justify-center overflow-hidden">
      {/* 顶部科技感 HUD 标题栏 */}
      <div className="absolute top-6 left-8 z-10 flex items-center gap-3.5 pointer-events-auto">
        <div className="w-3.5 h-3.5 rounded-full bg-orange-500 animate-ping" />
        <div>
          <h2 className="text-white font-black text-xl tracking-wider flex items-center gap-2">
            <span>OmniRoute 3D 态势雷达</span>
            <span className="text-xs bg-sky-500/20 text-sky-300 border border-sky-400/40 px-2.5 py-0.5 rounded-full font-mono font-bold">
              态势 SITUATION
            </span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            聚焦「路线 · 气象 · 路况」时空态势 | 串联赛场全线 {routePoints.length > 0 ? routePoints.length : 1} 个景点动线
          </p>
        </div>
      </div>

      {/* 右上角：自动巡航开关 + 关闭按钮 */}
      <div className="absolute top-6 right-8 z-10 flex items-center gap-2.5 pointer-events-auto">
        <button
          onClick={() => setAutoRotate(v => !v)}
          className={`backdrop-blur-md p-2.5 rounded-2xl border transition-all hover:scale-105 cursor-pointer flex items-center gap-2 text-xs font-bold ${
            autoRotate
              ? 'bg-sky-500/20 text-sky-300 border-sky-400/40'
              : 'bg-white/10 text-slate-300 border-white/20 hover:bg-white/20'
          }`}
        >
          <RotateCw className={`w-4 h-4 ${autoRotate ? 'animate-spin-slow' : ''}`} />
          <span>{autoRotate ? '自动巡航 开' : '自动巡航 关'}</span>
        </button>
        <button
          onClick={onClose}
          className="bg-white/10 hover:bg-white/20 text-white p-2.5 rounded-2xl border border-white/20 transition-all hover:scale-105 cursor-pointer flex items-center gap-2 text-xs font-bold"
        >
          <span>返回 2D 导航</span>
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* 图例说明（清晰告知评审与用户各类标注含义） */}
      <div className="absolute top-24 left-8 z-10 bg-slate-900/85 border border-slate-800 p-4 rounded-2xl text-xs text-slate-300 space-y-2 backdrop-blur shadow-2xl">
        <div className="font-bold text-white mb-1 flex items-center gap-1.5">
          <Activity className="w-4 h-4 text-orange-400" />
          <span>3D 图例与风险说明：</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-emerald-500 inline-block shadow-sm shadow-emerald-500/50" />
          <span>安全区间 (CII &lt; 20)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-amber-500 inline-block shadow-sm shadow-amber-500/50" />
          <span>中度预警 (CII 20-40)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-rose-500 inline-block shadow-sm shadow-rose-500/50" />
          <span>高危严碍 (CII &gt; 40)</span>
        </div>
        <div className="text-[11px] text-slate-400 pt-1.5 border-t border-slate-800 space-y-1">
          <div>• 弧线：起终点跨城航线与城内景点串联动线</div>
          <div>• 标记：路线打卡顺序与全息信息标</div>
        </div>
      </div>

      {/* 左下角详细风控与气象 HUD 面板 */}
      <div className="absolute bottom-8 left-8 z-10 w-96 bg-slate-900/90 backdrop-blur-md border border-slate-800 p-5 rounded-2xl text-white shadow-2xl space-y-3 pointer-events-auto">
        <div className="flex items-center justify-between pb-2.5 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Navigation2 className="w-4 h-4 text-orange-400" />
            <span className="font-bold text-sm text-orange-400">【{targetCity}】全息风控档案</span>
          </div>
          <span className={`text-xs font-mono bg-orange-500/10 border px-2.5 py-1 rounded-lg ${hasRealCii ? profile.text : 'text-slate-400'} border-current/30`}>
            CII 指数: {hasRealCii ? ciiScore.toFixed(1) : '待评估'}
          </span>
        </div>

        {/* CII 风险仪表盘：直观呈现 0-100 风险刻度与档位 */}
        <div className="bg-slate-800/50 p-3 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-[11px] font-bold">
            <span className="flex items-center gap-1.5 text-slate-300">
              <Gauge className="w-3.5 h-3.5 text-orange-400" /> 综合风险仪表盘
            </span>
            <span className={hasRealCii ? profile.text : 'text-slate-400'}>
              {hasRealCii ? profile.label : '数据加载中'}
            </span>
          </div>
          <div className="relative h-2.5 w-full rounded-full bg-slate-700/70 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{ width: `${ciiPercent}%`, background: `linear-gradient(90deg, #10b981, #f59e0b, #f43f5e)` }}
            />
          </div>
          <div className="flex justify-between text-[9px] font-mono text-slate-500">
            <span>0 安全</span>
            <span>20 预警</span>
            <span>40 高危</span>
            <span>100</span>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs bg-slate-800/50 p-2.5 rounded-xl">
          <span className="text-slate-400">当前实时气象:</span>
          <span className="font-bold text-slate-200 flex items-center gap-1">
            <Wind className="w-3.5 h-3.5 text-sky-400" />
            {weatherCondition}
          </span>
        </div>

        <div className="flex items-center justify-between text-xs bg-slate-800/50 p-2.5 rounded-xl">
          <span className="text-slate-400">实时路况:</span>
          <span className="font-bold text-slate-200 flex items-center gap-1">
            <Activity className="w-3.5 h-3.5 text-emerald-400" />
            {trafficSummary}
          </span>
        </div>

        <div className="space-y-2 text-xs text-slate-300 max-h-44 overflow-y-auto pr-1">
          {safetyInfo?.active_alerts && safetyInfo.active_alerts.length > 0 ? (
            safetyInfo.active_alerts.map((alert, idx) => (
              <div key={idx} className="bg-slate-800/80 p-3 rounded-xl border border-slate-700/80">
                <div className="font-bold text-amber-400 mb-1 flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  <span>{alert.title}</span>
                </div>
                <div className="text-[11px] text-slate-300 leading-relaxed">{alert.detail}</div>
              </div>
            ))
          ) : (
            <div className="text-emerald-400 text-center py-3 bg-emerald-950/20 rounded-xl border border-emerald-800/30 flex items-center justify-center gap-1.5 text-xs font-bold">
              <ShieldCheck className="w-4 h-4" />
              <span>该区域治安与气候良好，未检测到重大阻断风险</span>
            </div>
          )}
        </div>

        {safetyInfo?.safety_advice && (
          <div className="pt-2.5 border-t border-slate-800 text-[11px] text-slate-300 leading-relaxed">
            <span className="text-orange-400 font-bold">智能体避险方案：</span>
            {safetyInfo.safety_advice}
          </div>
        )}
      </div>

      {/* 3D 地球主体（绑定自适应宽度与高度） */}
      <div className="w-full h-full">
        <Globe
          ref={globeRef}
          width={dimensions.width}
          height={dimensions.height}
          globeImageUrl="//unpkg.com/three-globe/example/img/earth-night.jpg"
          bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
          backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
          htmlElementsData={htmlElementsData}
          htmlElement={(d: any) => {
            // 关键修复：地球上的 HTML 标注由“大卡片”改为“小圆点标记”，
            // 从根本上消除同城景点临近时卡片的相互重叠挤压；详情改由右侧独立面板承载。
            const el = document.createElement('div');
            el.style.cursor = 'pointer';
            el.style.transform = 'translate(-50%, -50%)';
            el.innerHTML = `
              <div class="relative flex items-center justify-center" style="width:22px;height:22px;">
                <span class="absolute inset-0 rounded-full ${d.isOrigin ? 'bg-orange-500/50' : 'bg-sky-500/40'} animate-ping"></span>
                <span class="relative w-5 h-5 rounded-full ${d.isOrigin ? 'bg-orange-500' : 'bg-sky-500'} text-white text-[9px] font-black flex items-center justify-center shadow-lg border border-white/60">
                  ${d.index}
                </span>
              </div>
            `;
            el.onclick = (e: Event) => {
              e.stopPropagation?.();
              setSelectedIndex(d.index - 1);
              if (globeRef.current) {
                globeRef.current.pointOfView({ lat: d.lat, lng: d.lng, altitude: 1.1 }, 800);
              }
            };
            return el;
          }}
          arcsData={arcsData}
          arcColor="color"
          arcDashLength={0.4}
          arcDashGap={0.2}
          arcDashAnimateTime={1800}
          arcStroke={1.4}
          ringsData={ringsData}
          ringColor="color"
          ringMaxRadius="maxR"
          ringPropagationSpeed="propagationSpeed"
          ringRepeatPeriod="repeatPeriod"
          atmosphereColor="#3b82f6"
          atmosphereAltitude={0.25}
        />
      </div>

      {/* 右侧航点面板：承载完整信息，彻底解决球面卡片重叠挤压问题 */}
      {routePoints.length > 0 && (
        <div className="absolute right-6 top-24 bottom-8 z-10 w-80 flex flex-col pointer-events-auto">
          <div className="bg-slate-900/90 backdrop-blur-md border border-slate-800 rounded-2xl shadow-2xl text-white overflow-hidden flex flex-col max-h-full">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between shrink-0">
              <span className="font-bold text-sm text-sky-300 flex items-center gap-2">
                <MapPin className="w-4 h-4" /> 航点清单
              </span>
              <span className="text-[10px] text-slate-500 font-mono">{routePoints.length} 节点</span>
            </div>

            <div className="overflow-y-auto flex-1 custom-scrollbar">
              {routePoints.map((pt, idx) => (
                <button
                  key={`poi-row-${idx}`}
                  onClick={() => {
                    setSelectedIndex(idx);
                    if (globeRef.current) {
                      globeRef.current.pointOfView({ lat: pt.lnglat[1], lng: pt.lnglat[0], altitude: 1.1 }, 800);
                    }
                  }}
                  className={`w-full text-left px-4 py-2.5 flex items-center gap-2.5 border-b border-slate-800/60 transition-colors ${
                    selectedIndex === idx ? 'bg-orange-500/15' : 'hover:bg-slate-800/60'
                  }`}
                >
                  <span className={`w-5 h-5 shrink-0 rounded-full ${idx === 0 ? 'bg-orange-500' : 'bg-sky-500'} text-white text-[10px] font-black flex items-center justify-center`}>
                    {idx + 1}
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-xs font-bold truncate">{pt.name}</span>
                    {pt.time && <span className="block text-[10px] text-slate-400 font-mono">{pt.time}</span>}
                  </span>
                </button>
              ))}
            </div>

            {routePoints[selectedIndex] && (
              <div className="px-4 py-3 border-t border-slate-800 bg-slate-800/40 shrink-0">
                <div className="text-sm font-black text-orange-400">{routePoints[selectedIndex].name}</div>
                {routePoints[selectedIndex].desc && (
                  <div className="text-[11px] text-slate-300 mt-1 leading-relaxed">{routePoints[selectedIndex].desc}</div>
                )}
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {routePoints[selectedIndex].time && (
                    <span className="text-[10px] bg-slate-700/70 px-2 py-0.5 rounded text-slate-300 font-mono">时间 {routePoints[selectedIndex].time}</span>
                  )}
                  {routePoints[selectedIndex].transport && (
                    <span className="text-[10px] bg-slate-700/70 px-2 py-0.5 rounded text-slate-300 font-mono">交通 {routePoints[selectedIndex].transport}</span>
                  )}
                  <span className="text-[10px] bg-slate-700/70 px-2 py-0.5 rounded text-slate-300 font-mono">气象 {weatherCondition}</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}