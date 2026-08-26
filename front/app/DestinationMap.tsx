'use client';

import React, { useState, useEffect, useRef, useMemo } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { X, MapPin, Star, Flame, Utensils, Landmark, Search, Sparkles, Compass, ExternalLink } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { PROVINCE_DATA } from './data/provinceData';

interface DestinationMapProps {
  onClose: () => void;
  onStartPlan: (destination: string) => void;
}

interface Entry {
  name: string;
  info: typeof PROVINCE_DATA[string];
}

export default function DestinationMap({ onClose, onStartPlan }: DestinationMapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const [mapReady, setMapReady] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const entries = useMemo<Entry[]>(() => {
    const q = query.trim().toLowerCase();
    const list = Object.entries(PROVINCE_DATA).map(([name, info]) => ({ name, info }));
    if (!q) return list.sort((a, b) => parseFloat(b.info.summary.score) - parseFloat(a.info.summary.score));
    return list
      .filter(e =>
        e.name.toLowerCase().includes(q) ||
        e.info.summary.tags.some(t => t.toLowerCase().includes(q)) ||
        (e.info.summary.poyPreviews || []).some(p => p.toLowerCase().includes(q)) ||
        e.info.hotspots.some(h => h.name.toLowerCase().includes(q))
      )
      .sort((a, b) => parseFloat(b.info.summary.score) - parseFloat(a.info.summary.score));
  }, [query]);

  // 初始化地图（复用高德瓦片底图 + MapLibre GL）
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {
          'amap-normal': {
            type: 'raster',
            tiles: ['https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}'],
            tileSize: 256,
            maxzoom: 18
          }
        },
        layers: [
          { id: 'layer-normal', type: 'raster', source: 'amap-normal', minzoom: 0, maxzoom: 22 }
        ]
      },
      center: [104.5, 35.5],
      zoom: 3.6,
      pitch: 0,
      bearing: 0
    });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.on('load', () => setMapReady(true));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  const handleSelect = (name: string, lnglat: [number, number]) => {
    setSelected(name);
    if (mapRef.current) {
      mapRef.current.flyTo({ center: lnglat, zoom: 5.5, duration: 1200, pitch: 0, bearing: 0 });
    }
  };

  // 渲染省份标记点
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];

    entries.forEach(({ name, info }) => {
      const first = info.hotspots[0];
      if (!first) return;
      const isSel = selected === name;

      const el = document.createElement('button');
      el.style.cssText = 'background:transparent;border:none;padding:0;cursor:pointer;outline:none;';
      el.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;gap:2px;transform:${isSel ? 'translateY(-6px) scale(1.15)' : 'translateY(0) scale(1)'};transition:transform .2s;filter:drop-shadow(0 2px 6px rgba(15,23,42,.25))">
          <span style="background:${isSel ? '#ea580c' : '#f97316'};color:#fff;font-size:10px;font-weight:800;padding:3px 8px;border-radius:999px;border:2px solid #fff;line-height:1">${first.name.slice(0, 4)}</span>
          <span style="color:#fff;font-size:9px;font-weight:800;background:rgba(15,23,42,.75);padding:2px 6px;border-radius:999px">★ ${info.summary.score}</span>
        </div>`;
      el.onclick = () => handleSelect(name, first.lnglat);

      const marker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat(first.lnglat)
        .addTo(map);
      markersRef.current.push(marker);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, entries, selected]);

  const selectedInfo = selected ? PROVINCE_DATA[selected] : null;

  return (
    <div className="fixed inset-0 z-[130] bg-slate-950 overflow-hidden font-sans">
      <div ref={mapContainer} className="w-full h-full absolute inset-0" />

      {/* 顶部渐变遮罩 + 标题 */}
      <div className="absolute top-0 inset-x-0 bg-gradient-to-b from-slate-950/90 to-transparent p-4 sm:p-6 pointer-events-none z-10">
        <div className="max-w-6xl mx-auto flex items-center justify-between pointer-events-auto">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-orange-500/20 border border-orange-400/30 rounded-2xl text-orange-400">
              <Compass className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-black text-white tracking-tight">灵感目的地图谱</h2>
              <p className="text-[11px] text-slate-300 font-medium">{Object.keys(PROVINCE_DATA).length} 个目的地 · 点击标记或列表查看详情</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2.5 bg-white/10 hover:bg-white/20 border border-white/20 text-white rounded-xl transition-colors cursor-pointer backdrop-blur">
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* 左侧目的地列表 */}
      <div className="absolute left-4 top-24 bottom-4 w-[300px] z-10 flex flex-col gap-3 max-h-[calc(100%-7rem)]">
        <div className="bg-white/95 backdrop-blur-xl rounded-2xl shadow-2xl border border-slate-200 overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100 bg-slate-50/80">
            <Search className="w-4 h-4 text-slate-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索目的地 / 标签 / 景点..."
              className="flex-1 bg-transparent text-xs font-bold text-slate-700 outline-none placeholder:text-slate-400"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar space-y-2 pr-1">
          {entries.map(({ name, info }) => (
            <button
              key={name}
              onClick={() => handleSelect(name, info.hotspots[0]?.lnglat || [104.5, 35.5])}
              className={`w-full text-left rounded-2xl p-3 border transition-all cursor-pointer backdrop-blur ${
                selected === name
                  ? 'bg-white/95 border-orange-400 shadow-xl'
                  : 'bg-white/75 border-slate-200/60 hover:bg-white/95 hover:border-orange-300'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-black text-slate-800">{name}</span>
                <span className="flex items-center gap-0.5 text-[11px] font-black text-orange-600">
                  <Star className="w-3 h-3 fill-orange-400 text-orange-400" /> {info.summary.score}
                </span>
              </div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                {info.summary.tags.slice(0, 3).map(t => (
                  <span key={t} className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded-md font-bold">{t}</span>
                ))}
                <span className="text-[10px] bg-orange-50 text-orange-600 px-1.5 py-0.5 rounded-md font-bold flex items-center gap-0.5">
                  <Flame className="w-2.5 h-2.5" /> {info.summary.hotCount}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* 选中目的地详情卡 */}
      <AnimatePresence>
        {selectedInfo && selected && (
          <motion.div
            key="detail"
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 30 }}
            className="absolute right-4 bottom-4 top-24 w-[340px] z-10 pointer-events-auto"
          >
            <div className="h-full flex flex-col bg-white/95 backdrop-blur-xl rounded-3xl shadow-2xl border border-slate-200 overflow-hidden">
              <div className="p-5 border-b border-slate-100 bg-gradient-to-br from-orange-50 to-white">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xl font-black text-slate-800">{selected}</h3>
                  <span className="text-[11px] font-black text-orange-600 flex items-center gap-1">
                    <Star className="w-3.5 h-3.5 fill-orange-400 text-orange-400" /> {selectedInfo.summary.score}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {selectedInfo.summary.tags.map(t => (
                    <span key={t} className="text-[10px] bg-white border border-slate-200 text-slate-600 px-2 py-0.5 rounded-full font-bold">{t}</span>
                  ))}
                </div>
                <a
                  href={`https://www.mafengwo.cn/search/q.php?q=${encodeURIComponent(selected + ' 旅游攻略')}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2.5 inline-flex items-center gap-1 text-[11px] font-bold text-sky-600 hover:text-sky-700 hover:underline"
                >
                  查看详细攻略 <ExternalLink className="w-3 h-3" />
                </a>
              </div>

              <div className="flex-1 overflow-y-auto custom-scrollbar p-5 space-y-3">
                {selectedInfo.hotspots.map(h => (
                  <div key={h.name} className="flex items-start gap-3 p-3 bg-slate-50 rounded-2xl">
                    <div className={`p-2 rounded-xl shrink-0 ${h.type === 'food' ? 'bg-orange-100 text-orange-600' : 'bg-blue-100 text-blue-600'}`}>
                      {h.type === 'food' ? <Utensils className="w-4 h-4" /> : <Landmark className="w-4 h-4" />}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-xs font-black text-slate-800">{h.name}</p>
                        <span className="text-[9px] bg-slate-200 text-slate-500 px-1.5 py-0.5 rounded font-bold shrink-0">{h.type === 'food' ? '美食' : '景点'}</span>
                      </div>
                      <p className="text-[11px] text-slate-500 leading-relaxed mt-0.5">{h.desc}</p>
                      <p className="text-[10px] text-slate-400 font-bold mt-1 flex items-center gap-1">
                        <Sparkles className="w-2.5 h-2.5 text-orange-400" /> {h.recommend}
                      </p>
                      <a
                        href={`https://www.amap.com/search?query=${encodeURIComponent(h.name)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-flex items-center gap-0.5 text-[10px] font-bold text-sky-600 hover:text-sky-700 hover:underline"
                      >
                        在线详情 <ExternalLink className="w-2.5 h-2.5" />
                      </a>
                    </div>
                  </div>
                ))}
              </div>

              <div className="p-4 border-t border-slate-100 bg-white">
                <button
                  onClick={() => onStartPlan(selected)}
                  className="w-full py-3 bg-gradient-to-r from-orange-500 to-amber-500 text-white rounded-2xl font-bold text-sm flex items-center justify-center gap-2 hover:brightness-110 transition-all shadow-lg shadow-orange-200/50 cursor-pointer"
                >
                  <MapPin className="w-4 h-4" /> 以「{selected}」开启规划
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 核心功能导览 */}
      <div className="absolute bottom-5 left-1/2 -translate-x-1/2 z-10 pointer-events-none">
        <div className="flex items-center gap-2 sm:gap-4 px-4 py-2.5 bg-slate-950/85 backdrop-blur-md border border-white/10 rounded-2xl shadow-2xl">
          <span className="flex items-center gap-1.5 text-[11px] font-bold text-slate-200 whitespace-nowrap"><Search className="w-3.5 h-3.5 text-orange-400" /> 左侧搜索筛选</span>
          <span className="w-px h-4 bg-white/15" />
          <span className="flex items-center gap-1.5 text-[11px] font-bold text-slate-200 whitespace-nowrap"><MapPin className="w-3.5 h-3.5 text-orange-400" /> 点击标记看景点详情</span>
          <span className="w-px h-4 bg-white/15" />
          <span className="flex items-center gap-1.5 text-[11px] font-bold text-slate-200 whitespace-nowrap"><Sparkles className="w-3.5 h-3.5 text-orange-400" /> 一键开启智能规划</span>
        </div>
      </div>
    </div>
  );
}