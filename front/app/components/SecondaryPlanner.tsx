'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Footprints, MapPin, Navigation, RefreshCw, Search, Sparkles, Store, X } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';

type Poi = {
  name?: string;
  lnglat?: [number, number];
  address?: string;
};

type MicroPoi = {
  name: string;
  type?: string;
  address?: string;
  lng?: string;
  lat?: string;
  coordinate_status?: 'verified' | 'unavailable';
  distanceKm?: number;
};

const QUICK_NEEDS = ['特色景点', '特色店铺', '人少优先', '步行最短', '亲子友好', '无障碍'];

function distanceKm(origin: [number, number] | undefined, lng: string, lat: string) {
  if (!origin || !Number.isFinite(Number(lng)) || !Number.isFinite(Number(lat))) return undefined;
  const toRad = (value: number) => value * Math.PI / 180;
  const [lng1, lat1] = origin;
  const lat2 = Number(lat);
  const lng2 = Number(lng);
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return Math.round(6371 * 2 * Math.asin(Math.sqrt(h)) * 100) / 100;
}

export default function SecondaryPlanner({ open, city, poi, onClose, userId, tripId }: {
  open: boolean;
  city: string;
  poi: Poi | null;
  onClose: () => void;
  userId?: string;
  tripId?: string;
}) {
  const [need, setNeed] = useState('特色景点、特色店铺；优先人少且步行距离短');
  const [results, setResults] = useState<MicroPoi[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    if (!open) {
      setResults([]);
      setError('');
      setSearched(false);
    }
  }, [open, poi?.name]);

  const anchor = useMemo<[number, number] | undefined>(() => {
    const value = poi?.lnglat;
    return Array.isArray(value) && value.length >= 2 && value.every(Number.isFinite) ? value : undefined;
  }, [poi?.lnglat]);

  const plan = useCallback(async () => {
    if (!poi?.name || !need.trim()) return;
    setLoading(true);
    setError('');
    setSearched(true);
    try {
      const keywords = `${poi.name} ${need.trim()}`;
      const query = new URLSearchParams({ city, keywords, offset: '20' });
      const data = await apiJson<{ available?: boolean; pois?: MicroPoi[]; message?: string }>(
        `${API_BASE}/api/v1/amap/poi?${query.toString()}`,
      );
      if (!data.available) throw new Error(data.message || '地图 POI 服务尚未配置');
      const unique = new Map<string, MicroPoi>();
      for (const item of data.pois || []) {
        if (!item?.name || item.name === poi.name || unique.has(item.name)) continue;
        unique.set(item.name, { ...item, distanceKm: distanceKm(anchor, item.lng, item.lat) });
      }
      const ranked = [...unique.values()]
        .sort((a, b) => (a.distanceKm ?? Number.MAX_SAFE_INTEGER) - (b.distanceKm ?? Number.MAX_SAFE_INTEGER))
        .slice(0, 6);
      setResults(ranked);
      if (ranked.length === 0) setError('景区周边暂未检索到符合要求的真实地点，请换一组关键词');
    } catch (e) {
      // POI providers may be unavailable in local/demo environments. Keep the
      // core secondary-planning workflow usable through the typed planner API.
      try {
        if (!userId || !tripId) throw e;
        const fallback = await apiJson<any>(`${API_BASE}/api/v1/planning/secondary`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, trip_id: tripId, destination: city, requirements: need.trim() }),
        });
        const route = fallback?.data?.route || fallback?.route || [];
        const fallbackResults = route.flatMap((item: any) => {
          const name = typeof item?.name === 'string' ? item.name.trim() : '';
          if (!name) return [];
          const lng = Number(item?.lng ?? item?.longitude);
          const lat = Number(item?.lat ?? item?.latitude);
          const hasCoordinates = Number.isFinite(lng) && Number.isFinite(lat);
          return [{
          name,
          type: item.type,
          address: `景区二次规划 · 客流${item.crowd || '适中'}`,
          lng: hasCoordinates ? String(lng) : undefined,
          lat: hasCoordinates ? String(lat) : undefined,
          coordinate_status: hasCoordinates ? 'verified' as const : 'unavailable' as const,
          distanceKm: hasCoordinates ? distanceKm(anchor, String(lng), String(lat)) : undefined,
        }];
        });
        setResults(fallbackResults);
        setError(fallbackResults.length ? '实时地图服务暂不可用，已切换为规划引擎建议' : '暂未生成可行的景区路线');
      } catch (fallbackError) {
        setResults([]);
        setError(fallbackError instanceof Error ? fallbackError.message : '景区微路线生成失败');
      }
    } finally {
      setLoading(false);
    }
  }, [anchor, city, need, poi?.name, userId, tripId]);

  if (!open || !poi) return null;

  return (
    <div className="fixed inset-0 z-[140] flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm" onClick={onClose}>
      <section className="w-full max-w-2xl overflow-hidden rounded-2xl border border-white/20 bg-white shadow-2xl" onClick={(event) => event.stopPropagation()} aria-modal="true" role="dialog" aria-labelledby="micro-plan-title">
        <header className="flex items-start justify-between border-b border-slate-100 bg-slate-50 px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white"><Footprints className="h-5 w-5" /></div>
            <div>
              <h3 id="micro-plan-title" className="text-base font-black text-slate-900">{poi.name} · 景区内二次规划</h3>
              <p className="mt-1 text-xs text-slate-500">只规划当前景区及周边步行点，不改变总行程</p>
            </div>
          </div>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-200" aria-label="关闭"><X className="h-4 w-4" /></button>
        </header>

        <div className="p-5">
          <div className="mb-3 flex flex-wrap gap-2">
            {QUICK_NEEDS.map((label) => (
              <button key={label} onClick={() => setNeed((value) => value.includes(label) ? value : `${value}${value ? '，' : ''}${label}`)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-600 hover:border-emerald-300 hover:text-emerald-700">{label}</button>
            ))}
          </div>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
              <textarea value={need} onChange={(event) => setNeed(event.target.value)} rows={2} className="w-full resize-none rounded-xl border border-slate-200 py-2.5 pl-9 pr-3 text-sm outline-none focus:border-emerald-500" placeholder="例如：想看特色店铺，避开高峰，路线尽量短" />
            </div>
            <button onClick={() => void plan()} disabled={loading || !need.trim()} className="flex min-w-28 items-center justify-center gap-1.5 rounded-xl bg-emerald-600 px-4 text-sm font-bold text-white hover:bg-emerald-700 disabled:opacity-50">
              {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{loading ? '规划中' : '生成路线'}
            </button>
          </div>

          {error && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-bold text-amber-800">{error}</div>}

          {results.length > 0 && (
            <div className="mt-5 max-h-[46vh] overflow-y-auto pr-1">
              <div className="mb-3 flex items-center justify-between text-xs text-slate-500">
                <span className="font-bold text-slate-700">推荐步行顺序</span>
                <span>按距景区锚点由近到远 · {results.length} 站</span>
              </div>
              <ol className="space-y-2">
                {results.map((item, index) => {
                  const hasCoordinates = item.coordinate_status !== 'unavailable' && Number.isFinite(Number(item.lng)) && Number.isFinite(Number(item.lat));
                  const destination = hasCoordinates ? `${item.lng},${item.lat},${encodeURIComponent(item.name)}` : '';
                  const href = anchor && hasCoordinates
                    ? `https://uri.amap.com/navigation?from=${anchor[0]},${anchor[1]},${encodeURIComponent(poi.name || '')}&to=${destination}&mode=walk`
                    : `https://www.amap.com/search?query=${encodeURIComponent(`${city} ${item.name}`)}`;
                  return (
                    <li key={`${item.name}-${index}`} className="flex items-center gap-3 rounded-xl border border-slate-100 bg-slate-50 px-3 py-3">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-xs font-black text-white">{index + 1}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2"><strong className="truncate text-sm text-slate-800">{item.name}</strong>{item.distanceKm !== undefined && <span className="shrink-0 text-[10px] font-bold text-emerald-700">约 {item.distanceKm} km</span>}</div>
                        <p className="mt-0.5 truncate text-xs text-slate-500"><MapPin className="mr-1 inline h-3 w-3" />{item.address || item.type || '地点详情以地图为准'}</p>
                      </div>
                      <a href={href} target="_blank" rel="noopener noreferrer" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-white text-emerald-700 hover:bg-emerald-50" title={hasCoordinates ? '打开步行导航' : '在高德中搜索地点'}><Navigation className="h-4 w-4" /></a>
                    </li>
                  );
                })}
              </ol>
              <p className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-400"><Store className="h-3.5 w-3.5" />地点来自实时 POI；“人少”作为检索偏好，实时客流请以地图现场热度为准。</p>
            </div>
          )}

          {searched && !loading && !error && results.length === 0 && <p className="py-8 text-center text-sm text-slate-400">暂无匹配地点</p>}
        </div>
      </section>
    </div>
  );
}
