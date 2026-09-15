'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Leaf, Wind, Trees, X, Route, RefreshCw, TrendingDown, Info } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';


// 中文出行方式 → 碳核算模式键（与 ai-service 的 EMISSION_FACTORS 对齐）
function mapTransportToMode(t: string | undefined | null): string {
  if (!t) return 'transit';
  const s = String(t).trim();
  if (s.includes('步行')) return 'walking';
  if (s.includes('骑行') || s.includes('单车') || s.includes('自行车')) return 'cycling';
  if (s.includes('地铁')) return 'metro';
  if (s.includes('公交')) return 'bus';
  if (s.includes('自驾') || s.includes('驾车')) return 'driving';
  if (s.includes('打车') || s.includes('出租') || s.includes('网约车')) return 'taxi';
  if (s.includes('高铁') || s.includes('动车')) return 'highspeed_rail';
  if (s.includes('飞机') || s.includes('航班')) return 'flight';
  return 'transit';
}

// 两点间直线距离（公里，Haversine），仅作为无真实导航数据时的兜底估算
function haversineKm(a: [number, number] | undefined, b: [number, number] | undefined): number | null {
  if (!a || !b || a.length < 2 || b.length < 2) return null;
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

interface CarbonSegment {
  from: string;
  to: string;
  mode: string;
  distance_km: number;
}

interface CarbonFootprintPanelProps {
  routes: any[]; // 全程路线节点（含 name / lnglat / transport / day）
  travelDetails?: Record<string, any>;
}

export default function CarbonFootprintPanel({ routes, travelDetails }: CarbonFootprintPanelProps) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  // 从路线节点构建逐段出行（仅连接同一天内相邻节点）
  const segments: CarbonSegment[] = useMemo(() => {
    if (!routes || routes.length < 2) return [];
    const segs: CarbonSegment[] = [];
    for (let i = 0; i < routes.length - 1; i++) {
      const a = routes[i];
      const b = routes[i + 1];
      if (!a || !b) continue;
      // 跨天节点不视为连续出行段（次日出发另计）
      if (a.day != null && b.day != null && Number(a.day) !== Number(b.day)) continue;

      const nameA = a.name || `节点${i + 1}`;
      const nameB = b.name || `节点${i + 2}`;
      const mode = mapTransportToMode(a.transport || b.transport);

      // 优先采用高德真实导航距离，取不到再退化到直线距离估算
      const pairKey = `${nameA}|${nameB}`;
      const reverseKey = `${nameB}|${nameA}`;
      const details = travelDetails?.[pairKey] || travelDetails?.[reverseKey];
      const amapMode = mode === 'driving' || mode === 'taxi' ? 'driving'
        : (mode === 'metro' || mode === 'bus' || mode === 'transit') ? 'transit' : 'walking';
      const dist = details?.[amapMode]?.distance_km
        ?? details?.driving?.distance_km
        ?? details?.transit?.distance_km
        ?? details?.walking?.distance_km
        ?? haversineKm(a.lnglat, b.lnglat);

      if (dist == null || !Number.isFinite(Number(dist)) || Number(dist) <= 0) continue;
      segs.push({ from: nameA, to: nameB, mode, distance_km: Number(dist) });
    }
    return segs;
  }, [routes, travelDetails]);

  const evaluate = useCallback(async () => {
    if (segments.length === 0) {
      setError('当前路线缺少可核算的相邻坐标或交通方式');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await apiJson<{ result?: any; error?: string }>(`${API_BASE}/api/v1/carbon/footprint`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ segments }),
      });
      if (!data?.result) throw new Error(data?.error || '未返回碳足迹核算结果');
      setResult(data.result);
    } catch (e) {
      setError(e instanceof Error ? e.message : '绿色出行评估暂时不可用');
    } finally {
      setLoading(false);
    }
  }, [segments]);

  useEffect(() => {
    setResult(null);
    setError('');
  }, [segments]);

  useEffect(() => {
    if (open && segments.length > 0 && !result) evaluate();
  }, [open, segments, result, evaluate]);

  const greenPct = result?.green_ratio != null ? Math.round(result.green_ratio * 100) : null;

  return (
    <>
      {/* 悬浮入口按钮：抬升到 5.25rem，避开右下角地图归属控件（attribution pill） */}
      <div className="absolute bottom-[5.25rem] left-4 z-40">
        <motion.button
          onClick={() => setOpen((o) => !o)}
          whileTap={{ scale: 0.92 }}
          className={`relative p-3 rounded-2xl shadow-xl border transition-all cursor-pointer flex items-center gap-2 ${
            open
              ? 'bg-gradient-to-br from-emerald-500 to-teal-500 text-white border-emerald-400/40'
              : 'bg-white/95 backdrop-blur-md text-slate-700 border-slate-200/80 hover:text-emerald-600'
          }`}
          title="碳足迹与绿色交通评估"
        >
          <Leaf className="w-5 h-5" />
          <span className="text-xs font-bold hidden sm:inline">绿色出行</span>
        </motion.button>
      </div>

      {/* 碳足迹评估面板 */}
      <AnimatePresence>
        {open && (
          <motion.div
            key="carbon-panel"
            initial={{ opacity: 0, y: 12, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.96 }}
            transition={{ type: 'spring', damping: 24 }}
            className="absolute bottom-[13.5rem] right-4 z-40 w-[360px] max-w-[calc(100vw-2rem)] bg-white/95 backdrop-blur-2xl rounded-2xl shadow-2xl border border-slate-200/80 overflow-hidden flex flex-col"
          >
            {/* 头部 */}
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between bg-emerald-50/60">
              <div className="flex items-center gap-2">
                <Wind className="w-4 h-4 text-emerald-600" />
                <div>
                  <h4 className="text-sm font-black text-slate-800 leading-none">碳足迹 · 绿色交通评估</h4>
                  <p className="text-[10px] text-slate-400 font-medium mt-0.5">基于出行方式 × 里程的估算</p>
                </div>
              </div>
              <button onClick={() => setOpen(false)} className="p-1.5 hover:bg-emerald-100 rounded-lg text-slate-400 cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="max-h-[360px] overflow-y-auto custom-scrollbar">
              {segments.length === 0 ? (
                <div className="py-10 flex flex-col items-center gap-2 text-center px-6">
                  <Route className="w-8 h-8 text-slate-300" />
                  <p className="text-sm font-bold text-slate-600">暂无可用出行段</p>
                  <p className="text-xs text-slate-400">生成行程后，系统将按出行方式与里程核算全程碳排放。</p>
                </div>
              ) : loading && !result ? (
                <div className="py-10 flex flex-col items-center gap-2 text-center">
                  <RefreshCw className="w-6 h-6 text-emerald-500 animate-spin" />
                  <p className="text-xs text-slate-400">正在核算全程碳排放…</p>
                </div>
              ) : error ? (
                <div className="flex flex-col items-center gap-3 px-6 py-9 text-center">
                  <Info className="h-7 w-7 text-amber-500" />
                  <p className="text-sm font-bold text-slate-700">{error}</p>
                  <button onClick={() => void evaluate()} className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700"><RefreshCw className="h-3.5 w-3.5" />重新评估</button>
                </div>
              ) : result ? (
                <div className="p-4 space-y-4">
                  {/* 总量卡片 */}
                  <div className="grid grid-cols-3 gap-2">
                    <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-3 text-center">
                      <p className="text-2xl font-black text-emerald-600">{result.total_kg}</p>
                      <p className="text-[10px] text-emerald-600/80 font-bold mt-0.5">总排放 (kg)</p>
                    </div>
                    <div className="bg-sky-50 border border-sky-100 rounded-xl p-3 text-center">
                      <p className="text-2xl font-black text-sky-600">{greenPct}%</p>
                      <p className="text-[10px] text-sky-600/80 font-bold mt-0.5">绿色里程占比</p>
                    </div>
                    <div className="bg-amber-50 border border-amber-100 rounded-xl p-3 text-center">
                      <p className="text-2xl font-black text-amber-600">{result.tree_equivalent}</p>
                      <p className="text-[10px] text-amber-600/80 font-bold mt-0.5">≈ 棵树/年固碳</p>
                    </div>
                  </div>

                  {/* 全程里程 */}
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <Trees className="w-3.5 h-3.5 text-emerald-500" />
                    <span>
                      全程约 <b className="text-slate-700">{result.total_km}</b> km，其中绿色里程{' '}
                      <b className="text-emerald-600">{result.green_km}</b> km
                    </span>
                  </div>

                  {/* 逐段分解 */}
                  <div>
                    <p className="text-[11px] font-bold text-slate-500 mb-2">逐段排放</p>
                    <div className="space-y-1.5">
                      {(result.segments || []).map((s: any) => (
                        <div key={s.index} className="flex items-center justify-between bg-slate-50 rounded-lg px-3 py-2">
                          <div className="min-w-0">
                            <p className="text-[11px] font-bold text-slate-700 truncate">{s.from} → {s.to}</p>
                            <p className="text-[10px] text-slate-400">{s.mode_label} · {s.distance_km} km</p>
                          </div>
                          <span className={`text-xs font-black shrink-0 ml-2 ${s.emission_kg === 0 ? 'text-emerald-500' : 'text-slate-600'}`}>
                            {s.emission_kg} kg
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* 绿色替代建议 */}
                  {(result.alternatives || []).length > 0 && (
                    <div>
                      <p className="text-[11px] font-bold text-slate-500 mb-2 flex items-center gap-1">
                        <TrendingDown className="w-3.5 h-3.5 text-emerald-600" /> 绿色替代建议
                      </p>
                      <div className="space-y-1.5">
                        {result.alternatives.map((a: any, i: number) => (
                          <div key={i} className="bg-emerald-50/70 border border-emerald-100 rounded-lg px-3 py-2">
                            <p className="text-[11px] font-bold text-emerald-700">{a.from} → {a.to}</p>
                            <p className="text-[11px] text-emerald-600/90 leading-snug mt-0.5">{a.hint}</p>
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-slate-400 mt-1.5">全部替换约可减排 {result.saved_if_green_kg} kg CO2e</p>
                    </div>
                  )}

                  {/* 信任提示（估算口径） */}
                  <div className="flex items-start gap-2 bg-slate-50 border border-slate-150 rounded-lg px-3 py-2">
                    <Info className="w-3.5 h-3.5 text-slate-400 mt-0.5 shrink-0" />
                    <p className="text-[10px] text-slate-400 leading-snug">
                      碳排放量为行业公开因子估算（方法：距离 × 排放因子），非实测监测值，仅供绿色出行参考。
                      {result.is_estimated && ' 部分里程缺失按典型接驳距离估算。'}
                    </p>
                  </div>
                </div>
              ) : null}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
