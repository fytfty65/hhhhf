'use client';

import React, { useState, useEffect, useRef } from 'react';
import { createRoot, Root } from 'react-dom/client';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { Layers, ArrowLeft, Map as MapIcon, Compass, AlertCircle, Loader2, MessageSquareText, MapPin, Navigation } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

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

interface MapProps {
  phase: 'drafting' | 'deduction' | 'decision';
  selectedPoiIndex: number | null;
  onPoiSelect: (index: number) => void;
  luoyangRoute?: RoutePoint[];
  actualPath?: [number, number][]; 
  onExit?: () => void;
  onWakeAgent?: () => void;
}

export default function InteractiveAmapComponent({
  phase,
  selectedPoiIndex,
  onPoiSelect,
  luoyangRoute = [],
  actualPath = [], 
  onExit,
  onWakeAgent
}: MapProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<{ marker: maplibregl.Marker; root: Root }[]>([]);

  const [mapStyle, setMapStyle] = useState<'normal' | 'satellite'>('normal');
  const [showTraffic, setShowTraffic] = useState(false);
  const [showPoiInfo, setShowPoiInfo] = useState<number | null>(null);
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

    // 👑 自动居中定位逻辑：单景点精细定位，多景点 Bounds 全揽 (全部采用 pitch: 0)
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
      luoyangRoute.forEach(pt => bounds.extend(pt.lnglat));
      if (actualPath.length > 0) {
        actualPath.forEach(pt => bounds.extend(pt as [number, number]));
      }
      map.fitBounds(bounds, { padding: 90, maxZoom: 15, pitch: 0, bearing: 0, duration: 2000 });
    }

    if (luoyangRoute.length < 2) return;

    // 优先使用高德真实弯道路网坐标，无路网时使用景点坐标连线
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
        pitch: 0,   // 👑 2D 视角平移
        bearing: 0, // 👑 正北朝向
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

    // 清理旧标记，防止 unmount 冲突
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
          {/* Hover 悬浮预览提示框 */}
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

          {/* 马蜂窝风格橙色序号图钉 */}
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

  return (
    <div className="w-full h-full relative bg-slate-900 overflow-hidden">
      <div ref={mapContainer} className="w-full h-full absolute inset-0" />

      {/* 👑 清爽整洁的地图底图控制小组件 (包含实时路况颜色图例) */}
      <div className="absolute top-6 left-16 flex items-center gap-2.5 z-10 pointer-events-auto">
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

        {/* 👑 实时路况开启时的官方颜色图例标注卡片 */}
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
    </div>
  );
}