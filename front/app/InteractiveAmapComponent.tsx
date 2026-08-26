'use client';

import React, { useState, useEffect, useRef } from 'react';
import { createRoot, Root } from 'react-dom/client';
import dynamic from 'next/dynamic';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { 
  ArrowLeft, 
  Map as MapIcon, 
  Compass, 
  MessageSquareText, 
  Navigation,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Info,
  Activity,
  Globe2
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// 👑 SSR 安全导入：动态加载 3D 地球组件，彻底解决 Next.js Window 对象未定义报错
const WorldSafetyGlobe = dynamic(() => import('./components/WorldSafetyGlobe'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 z-[100] bg-slate-950/90 backdrop-blur-md text-white flex flex-col items-center justify-center gap-3">
      <div className="w-8 h-8 border-4 border-orange-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-xs font-bold text-slate-300">正在载入 WorldMonitor 3D 态势感知引擎...</span>
    </div>
  )
});

export interface RoutePoint {
  name: string;
  lnglat: [number, number];
  color: string;
  desc: string;
  time?: string;
  transport?: string;
  tags?: string[];
  cost?: string;
  trust_reason?: string;
  photos?: string[];
}

export interface SafetyAlert {
  type: string;
  level: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  detail: string;
  position?: [number, number];
}

export interface SafetyInfo {
  city?: string;
  cii_score?: number;
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH';
  active_alerts?: SafetyAlert[];
  safety_advice?: string;
}

interface MapProps {
  phase: 'drafting' | 'deduction' | 'decision';
  selectedPoiIndex: number | null;
  onPoiSelect: (index: number) => void;
  luoyangRoute?: RoutePoint[];
  actualPath?: [number, number][]; 
  safetyInfo?: SafetyInfo; // 👑 WorldMonitor 实时安全风控数据
  weatherInfo?: any; // 👑 高德/心知实时气象
  trafficInfo?: any; // 👑 高德实时路况
  onExit?: () => void;
  onWakeAgent?: () => void;
}

export default function InteractiveAmapComponent({
  phase,
  selectedPoiIndex,
  onPoiSelect,
  luoyangRoute = [],
  actualPath = [], 
  safetyInfo,
  weatherInfo,
  trafficInfo,
  onExit,
  onWakeAgent
}: MapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<{ marker: maplibregl.Marker; root: Root }[]>([]);

  const [mapStyle, setMapStyle] = useState<'normal' | 'satellite'>('normal');
  const [showTraffic, setShowTraffic] = useState(false);
  const [showPoiInfo, setShowPoiInfo] = useState<number | null>(null);
  const [showSafetyCard, setShowSafetyCard] = useState(false);
  const [show3DGlobe, setShow3DGlobe] = useState(false); // 👑 3D 态势大屏模态框开关
  const [mapLoaded, setMapLoaded] = useState(false);

  // ================= 1. 初始化 2D 地图引擎 (高德Tile底图 + MapLibre GL) =================
  useEffect(() => {
    if (!mapContainer.current || mapInstance.current) return;

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
          },
          'amap-satellite': {
            type: 'raster',
            tiles: ['https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}'],
            tileSize: 256,
            maxzoom: 18
          },
          'amap-labels': {
            type: 'raster',
            tiles: ['https://webst01.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}'],
            tileSize: 256,
            maxzoom: 18
          }
        },
        layers: [
          { id: 'layer-normal', type: 'raster', source: 'amap-normal', minzoom: 0, maxzoom: 22, layout: { visibility: 'visible' } },
          { id: 'layer-satellite', type: 'raster', source: 'amap-satellite', minzoom: 0, maxzoom: 22, layout: { visibility: 'none' } },
          { id: 'layer-satellite-labels', type: 'raster', source: 'amap-labels', minzoom: 0, maxzoom: 22, layout: { visibility: 'none' } }
        ]
      },
      center: luoyangRoute.length > 0 ? luoyangRoute[0].lnglat : [104.0665, 30.5722],
      zoom: 12,
      pitch: 0,   // 👑 强制 2D 视角，绝不倾斜
      bearing: 0  // 👑 正北方向，无旋转角度
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-left');
    
    map.on('load', () => {
      setMapLoaded(true);
    });

    mapInstance.current = map;

    return () => {
      map.remove();
      mapInstance.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); 

  // ================= 2. 动态切换图层 (卫星/路况/标准) =================
  useEffect(() => {
    if (!mapLoaded || !mapInstance.current) return;
    const map = mapInstance.current;

    if (mapStyle === 'satellite') {
      if (map.getLayer('layer-normal')) map.setLayoutProperty('layer-normal', 'visibility', 'none');
      if (map.getLayer('layer-satellite')) map.setLayoutProperty('layer-satellite', 'visibility', 'visible');
      if (map.getLayer('layer-satellite-labels')) map.setLayoutProperty('layer-satellite-labels', 'visibility', 'visible');
    } else {
      if (map.getLayer('layer-satellite')) map.setLayoutProperty('layer-satellite', 'visibility', 'none');
      if (map.getLayer('layer-satellite-labels')) map.setLayoutProperty('layer-satellite-labels', 'visibility', 'none');
      if (map.getLayer('layer-normal')) map.setLayoutProperty('layer-normal', 'visibility', 'visible');
    }

    if (showTraffic && !map.getLayer('traffic-layer')) {
      map.addSource('amap-traffic', {
        type: 'raster',
        tiles: ['/amap-traffic?v=1.0&t=1&x={x}&y={y}&z={z}'],
        tileSize: 256,
        maxzoom: 18
      });
      map.addLayer({
        id: 'traffic-layer',
        type: 'raster',
        source: 'amap-traffic',
        minzoom: 5,
        maxzoom: 22,
        paint: { 'raster-opacity': 0.85 }
      });
    } else if (!showTraffic && map.getLayer('traffic-layer')) {
      map.removeLayer('traffic-layer');
      map.removeSource('amap-traffic');
    }
  }, [mapStyle, showTraffic, mapLoaded]);

  // 把复杂变长数组转为唯一稳定引用字符串，阻止 React 重渲染深度死循环
  const routeDataStr = JSON.stringify(luoyangRoute.map(pt => pt.lnglat));
  const actualPathStr = JSON.stringify(actualPath);
  const markersDataStr = JSON.stringify(luoyangRoute);

  // ================= 3. 渲染专业级 2D 橙色导航路线与多节点视角缩放 =================
  useEffect(() => {
    if (!mapLoaded || !mapInstance.current || luoyangRoute.length === 0) return;
    const map = mapInstance.current;

    if (luoyangRoute.length === 1) {
      map.flyTo({
        center: luoyangRoute[0].lnglat,
        zoom: 14,
        pitch: 0,
        bearing: 0,
        duration: 1500
      });
    } else {
      const bounds = new maplibregl.LngLatBounds();
      luoyangRoute.forEach(pt => {
        // 👑 防御性校验：仅对合法 [lng,lat] 数组扩展视界，杜绝后端/LLM 输出非法坐标导致 LngLatLike 崩溃
        if (pt && Array.isArray(pt.lnglat) && pt.lnglat.length >= 2) {
          const lng = Number(pt.lnglat[0]);
          const lat = Number(pt.lnglat[1]);
          if (Number.isFinite(lng) && Number.isFinite(lat)) {
            bounds.extend([lng, lat]);
          }
        }
      });
      if (actualPath.length > 0) {
        actualPath.forEach(pt => bounds.extend(pt as [number, number]));
      }
      map.fitBounds(bounds, { padding: 90, maxZoom: 15, pitch: 0, bearing: 0, duration: 2000 });
    }

    if (luoyangRoute.length < 2) return;

    const finalCoordinates = actualPath && actualPath.length > 0 
      ? JSON.parse(actualPathStr) 
      : JSON.parse(routeDataStr);

    const routeGeoJSON: GeoJSON.Feature<GeoJSON.LineString> = {
      type: 'Feature',
      properties: {},
      geometry: { type: 'LineString', coordinates: finalCoordinates }
    };

    if (map.getSource('route-source')) {
      (map.getSource('route-source') as maplibregl.GeoJSONSource).setData(routeGeoJSON);
    } else {
      map.addSource('route-source', { type: 'geojson', data: routeGeoJSON });
      
      // 1. 底层马路包边（暗橙色）
      map.addLayer({
        id: 'route-layer-casing',
        type: 'line',
        source: 'route-source',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': phase === 'deduction' ? '#b45309' : '#c2410c',
          'line-width': phase === 'deduction' ? 6 : 10,
        }
      });

      // 2. 亮色高线核心（活力橙）
      map.addLayer({
        id: 'route-layer-inner',
        type: 'line',
        source: 'route-source',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': phase === 'deduction' ? '#f59e0b' : '#f97316',
          'line-width': phase === 'deduction' ? 3 : 6,
          'line-dasharray': phase === 'drafting' ? [2, 2] : [1]
        }
      });

      // 3. 沿着轨迹自适应旋转指示箭头
      map.addLayer({
        id: 'route-layer-arrows',
        type: 'symbol',
        source: 'route-source',
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 60,
          'text-field': '▶', 
          'text-size': 12,
          'text-keep-upright': false 
        },
        paint: {
          'text-color': '#ffffff',
          'text-halo-color': '#f97316',
          'text-halo-width': 1.5
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeDataStr, actualPathStr, phase, mapLoaded]);

  // ================= 4. 点击景点 2D 视角智能平移 (FlyTo) =================
  useEffect(() => {
    if (!mapLoaded || !mapInstance.current || selectedPoiIndex === null) return;
    
    const parsedRoutes = JSON.parse(routeDataStr);
    const targetLngLat = parsedRoutes[selectedPoiIndex];
    if (targetLngLat) {
      mapInstance.current.flyTo({
        center: targetLngLat,
        zoom: 15,
        pitch: 0,   
        bearing: 0, 
        speed: 1.2,
        curve: 1.4
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPoiIndex, mapLoaded, routeDataStr]); 

  // ================= 5. Marker 图标与气泡注入 =================
  useEffect(() => {
    if (!mapLoaded || !mapInstance.current) return;
    const map = mapInstance.current;

    markersRef.current.forEach(item => {
      setTimeout(() => {
        try { item.root.unmount(); } catch(e) {}
      }, 0);
      item.marker.remove();
    });
    markersRef.current = [];

    const currentRoute = JSON.parse(markersDataStr) as RoutePoint[];

    currentRoute.forEach((poi, index) => {
      const el = document.createElement('div');
      const root = createRoot(el);
      
      const isSelected = selectedPoiIndex === index;
      const isHovered = showPoiInfo === index;

      root.render(
        <div 
          className="relative cursor-pointer group flex flex-col items-center"
          onClick={(e) => {
            e.stopPropagation();
            onPoiSelect(index);
            setShowPoiInfo(prev => prev === index ? null : index);
          }}
          onMouseEnter={() => setShowPoiInfo(index)}
          onMouseLeave={() => setShowPoiInfo(null)}
        >
          <AnimatePresence>
            {isHovered && (
              <motion.div
                key={`marker-hover-${index}`}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}
                className="absolute bottom-full mb-3 bg-slate-900/95 backdrop-blur text-white p-3 rounded-xl shadow-2xl border border-slate-700 w-56 z-50 pointer-events-none"
              >
                <div className="font-bold text-sm mb-1 text-orange-400 truncate">{poi.name}</div>
                <div className="text-xs text-slate-300 line-clamp-2 leading-relaxed">{poi.desc}</div>
                {poi.transport && (
                  <div className="mt-2 text-[11px] text-orange-300 font-bold flex items-center gap-1 border-t border-slate-800 pt-1.5">
                    <Navigation className="w-3 h-3"/> {poi.transport}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <div 
            className={`w-9 h-9 rounded-full border-2 flex items-center justify-center shadow-xl transition-all duration-300 ${isSelected ? 'bg-orange-500 border-white text-white ring-4 ring-orange-300/80 scale-125 z-30' : 'bg-white border-orange-500 text-orange-600 hover:scale-110'}`}
          >
            <span className="text-xs font-black">{index + 1}</span>
          </div>
        </div>
      );

      const marker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat(poi.lnglat)
        .addTo(map);

      markersRef.current.push({ marker, root });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPoiIndex, showPoiInfo, mapLoaded, markersDataStr, onPoiSelect]);

  const activeAlertCount = safetyInfo?.active_alerts?.length || 0;
  const isHighRisk = safetyInfo?.risk_level === 'HIGH' || (safetyInfo?.cii_score && safetyInfo.cii_score > 30);
  const targetCityName = safetyInfo?.city || (luoyangRoute.length > 0 ? '目标目的地' : '海口');
  const cityCoords: [number, number] = luoyangRoute.length > 0 ? luoyangRoute[0].lnglat : [104.0665, 30.5722];
  // 👑 实时气象与路况摘要：从 SSE 真实数据派生，传入 3D 雷达而非使用写死占位
  const weatherCondition = weatherInfo?.condition ? String(weatherInfo.condition) : '实时气象获取中';
  const trafficSummary = (trafficInfo?.description || trafficInfo?.advice)
    ? String(trafficInfo.description || trafficInfo.advice)
    : '实时路况良好，整体畅通';

  return (
    <div className="w-full h-full relative bg-slate-900 overflow-hidden">
      <div ref={mapContainer} className="w-full h-full absolute inset-0" />

      {/* 👑 顶部控制面板（包含图层、路况、WorldMonitor 状态卡与 3D 态势雷达切换按钮） */}
      <div className="absolute top-6 left-16 flex items-center gap-2.5 z-10 pointer-events-auto flex-wrap">
        <button 
          onClick={() => setMapStyle(prev => prev === 'normal' ? 'satellite' : 'normal')} 
          className="bg-white/95 backdrop-blur-md px-3.5 py-2 rounded-xl border border-slate-200 text-slate-700 hover:text-orange-500 shadow-md flex items-center gap-1.5 text-xs font-bold transition-all hover:scale-105 cursor-pointer"
        >
          <MapIcon className="w-3.5 h-3.5" />
          <span>{mapStyle === 'normal' ? '卫星图' : '标准图'}</span>
        </button>

        <button 
          onClick={() => setShowTraffic(!showTraffic)} 
          className={`backdrop-blur-md px-3.5 py-2 rounded-xl border shadow-md flex items-center gap-1.5 text-xs font-bold transition-all hover:scale-105 cursor-pointer ${showTraffic ? 'bg-orange-500 border-orange-500 text-white' : 'bg-white/95 border-slate-200 text-slate-700 hover:text-orange-500'}`}
        >
          <Compass className="w-3.5 h-3.5" />
          <span>{showTraffic ? '关闭路况' : '实时路况'}</span>
        </button>

        {/* 👑 方案 A 核心：3D 态势/安全雷达视角切换按钮 */}
        <button
          onClick={() => setShow3DGlobe(true)}
          className="bg-gradient-to-r from-blue-600 via-indigo-600 to-violet-600 text-white px-3.5 py-2 rounded-xl shadow-lg border border-blue-400/30 flex items-center gap-1.5 text-xs font-bold hover:brightness-110 transition-all cursor-pointer hover:scale-105"
        >
          <Globe2 className="w-3.5 h-3.5 text-cyan-300 animate-spin-slow" />
          <span>3D 态势雷达</span>
        </button>

        {/* 👑 WorldMonitor 实时安全风控信息提示按钮 */}
        {safetyInfo && (
          <div className="relative">
            <button
              onClick={() => setShowSafetyCard(!showSafetyCard)}
              className={`backdrop-blur-md px-3.5 py-2 rounded-xl border shadow-md flex items-center gap-1.5 text-xs font-bold transition-all hover:scale-105 cursor-pointer ${
                isHighRisk 
                  ? 'bg-rose-500 border-rose-500 text-white animate-pulse' 
                  : activeAlertCount > 0 
                    ? 'bg-amber-500 border-amber-500 text-white' 
                    : 'bg-emerald-600 border-emerald-600 text-white'
              }`}
            >
              {isHighRisk ? <ShieldAlert className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
              <span>WorldMonitor {safetyInfo.cii_score !== undefined ? `CII:${safetyInfo.cii_score}` : '风控网'}</span>
              {activeAlertCount > 0 && (
                <span className="bg-white/20 text-white px-1.5 py-0.5 rounded-full text-[10px]">
                  {activeAlertCount}
                </span>
              )}
            </button>

            {/* 安全风控事件下钻面板 */}
            <AnimatePresence>
              {showSafetyCard && (
                <motion.div
                  initial={{ opacity: 0, y: 10, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 10, scale: 0.95 }}
                  className="absolute top-full mt-2 left-0 w-80 bg-slate-900/95 backdrop-blur-md text-white p-4 rounded-2xl shadow-2xl border border-slate-700 z-50"
                >
                  <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                    <div className="flex items-center gap-2">
                      <Activity className="w-4 h-4 text-orange-400" />
                      <span className="font-bold text-sm text-orange-400">WorldMonitor 安全情报</span>
                    </div>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
                      isHighRisk ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    }`}>
                      {safetyInfo.risk_level || 'SAFE'}
                    </span>
                  </div>

                  <div className="my-3 space-y-2 max-h-48 overflow-y-auto pr-1">
                    {safetyInfo.active_alerts && safetyInfo.active_alerts.length > 0 ? (
                      safetyInfo.active_alerts.map((alert, idx) => (
                        <div key={idx} className="bg-slate-800/80 p-2.5 rounded-xl border border-slate-700/80 text-xs">
                          <div className="flex items-center gap-1.5 font-bold text-amber-400 mb-1">
                            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                            <span>{alert.title}</span>
                          </div>
                          <p className="text-[11px] text-slate-300 leading-relaxed">{alert.detail}</p>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-slate-400 py-2 text-center flex items-center justify-center gap-1.5">
                        <ShieldCheck className="w-4 h-4 text-emerald-400" />
                        <span>当前未检测到突发自然灾害或管制风险</span>
                      </div>
                    )}
                  </div>

                  {safetyInfo.safety_advice && (
                    <div className="pt-2 border-t border-slate-800 text-[11px] text-slate-300 flex items-start gap-1.5">
                      <Info className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" />
                      <span>{safetyInfo.safety_advice}</span>
                    </div>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* 实时路况开启时的官方颜色图例 */}
        <AnimatePresence>
          {showTraffic && (
            <motion.div 
              key="traffic-legend-card"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -10 }}
              className="bg-white/95 backdrop-blur-md px-3 py-2 rounded-xl border border-slate-200 shadow-md flex items-center gap-3 text-[11px] font-bold text-slate-600"
            >
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-xs"></span>畅通</span>
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-amber-500 shadow-xs"></span>缓行</span>
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-rose-500 shadow-xs"></span>拥堵</span>
            </motion.div>
          )}
        </AnimatePresence>

        {onWakeAgent && (
          <button 
            onClick={onWakeAgent} 
            className="bg-gradient-to-r from-orange-500 to-amber-500 text-white px-3.5 py-2 rounded-xl shadow-md border border-orange-400/30 flex items-center gap-1.5 text-xs font-bold hover:brightness-110 transition-all cursor-pointer"
          >
            <MessageSquareText className="w-3.5 h-3.5" />
            <span>唤醒决策体</span>
          </button>
        )}
      </div>

      {onExit && (
        <button 
          onClick={onExit} 
          className="absolute top-6 left-6 bg-white/95 backdrop-blur-md p-2.5 rounded-xl border border-slate-200 text-slate-700 hover:text-orange-500 shadow-md flex items-center justify-center z-10 pointer-events-auto transition-all hover:scale-105 cursor-pointer"
          title="返回上一页"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
      )}

      {/* 👑 方案 A 模态框：3D 态势/安全雷达沉浸式大屏 */}
      {show3DGlobe && (
        <WorldSafetyGlobe
          targetCity={targetCityName}
          cityCoords={cityCoords}
          routePoints={luoyangRoute}
          safetyInfo={safetyInfo}
          weatherCondition={weatherCondition}
          trafficSummary={trafficSummary}
          onClose={() => setShow3DGlobe(false)}
        />
      )}
    </div>
  );
}