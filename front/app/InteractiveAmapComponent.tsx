'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Layers, ArrowLeft, Map as MapIcon, Box, Compass, AlertCircle, Loader2, LogOut, MessageSquareText } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { PROVINCE_DATA } from './data/provinceData'; 

export interface RoutePoint {
  name: string;
  lnglat: [number, number];
  color: string;
  desc: string;
}

interface MapProps {
  phase: 'drafting' | 'deduction' | 'decision';
  selectedPoiIndex: number | null;
  onPoiSelect: (index: number) => void;
  luoyangRoute?: RoutePoint[]; 
  onExit?: () => void;       
  onWakeAgent?: () => void;  
}

export default function InteractiveAmapComponent({ 
  phase, 
  selectedPoiIndex, 
  onPoiSelect,
  luoyangRoute = [],
  onExit,
  onWakeAgent
}: MapProps) {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const AMapRef = useRef<any>(null);
  
  const polylineRef = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  const interactionPolygonsRef = useRef<any[]>([]); 
  const hotspotMarkersRef = useRef<any[]>([]);  
  const provincePolygonsRef = useRef<Record<string, any>>({});

  const [drilledProvince, setDrilledProvince] = useState<string | null>(null);
  const [hoveredProvince, setHoveredProvince] = useState<string | null>(null);
  const [activeViewMode, setActiveViewMode] = useState<'2D' | '3D' | 'ROAM' | null>('3D');
  const [mapError, setMapError] = useState<string | null>(null);
  const [isMapReady, setIsMapReady] = useState<boolean>(false);
  
  const activeViewModeRef = useRef<'2D' | '3D' | 'ROAM' | null>('3D');
  const drilledProvinceRef = useRef<string | null>(null);
  const animationTimersRef = useRef<NodeJS.Timeout[]>([]); 

  const handleBackToNational = (resetCamera = true) => {
    const map = mapInstanceRef.current;
    if (!map) return;

    setDrilledProvince(null);
    drilledProvinceRef.current = null;
    
    const currentMode = activeViewModeRef.current;
    
    hotspotMarkersRef.current.forEach(m => map.remove(m));
    hotspotMarkersRef.current = [];
    animationTimersRef.current.forEach(clearTimeout);
    map.clearInfoWindow(); 

    if (resetCamera) {
      if (currentMode === '2D') {
        map.setPitch(0, true, 1200);
        map.setRotation(0, true, 1200);
      } else {
        map.setPitch(35, true, 1200);
        map.setRotation(0, true, 1200);
      }
      map.setZoomAndCenter(4.6, [105.195, 36.861], true, 1200);
    }
  };

  const handleViewAngleChange = (mode: '2D' | '3D' | 'ROAM') => {
    const map = mapInstanceRef.current;
    if (!map) return; 
    
    setActiveViewMode(mode);
    activeViewModeRef.current = mode; 
    
    if (drilledProvinceRef.current) {
      handleBackToNational(false); 
    }

    if (mode === '2D') {
      map.setPitch(0, true, 1200);
      map.setRotation(0, true, 1200);
      map.setZoomAndCenter(4.6, [105.195, 36.861], true, 1200);
    } else if (mode === '3D') {
      map.setPitch(35, true, 1200);
      map.setRotation(0, true, 1200);
      map.setZoomAndCenter(4.6, [105.195, 36.861], true, 1200);
    } else if (mode === 'ROAM') {
      map.setPitch(40, true, 1800); 
      map.setRotation(25, true, 1800); 
      map.setZoomAndCenter(6.2, [105.195, 36.861], true, 1800);
    }
  };

  const renderHotspots = (provinceName: string) => {
    const map = mapInstanceRef.current;
    const AMap = AMapRef.current;
    if (!map || !AMap) return;

    hotspotMarkersRef.current.forEach(m => map.remove(m));
    hotspotMarkersRef.current = [];
    animationTimersRef.current.forEach(clearTimeout);
    animationTimersRef.current = [];

    const data = PROVINCE_DATA[provinceName];
    if (!data) return;

    const infoWindow = new AMap.InfoWindow({ offset: new AMap.Pixel(0, -28) });

    const getMinZoomForProvince = (prov: string) => {
      if (['新疆维吾尔自治区', '西藏自治区', '内蒙古自治区', '青海省', '黑龙江省', '四川省', '甘肃省'].includes(prov)) return 3.6;
      if (['北京市', '天津市', '上海市', '香港特别行政区', '澳门特别行政区'].includes(prov)) return 7.5;
      return 5.2; 
    };

    const dynamicMinZoom = getMinZoomForProvince(provinceName);

    data.hotspots.forEach((spot, idx) => {
      const isFood = spot.type === 'food';
      const markerContent = document.createElement('div');
      
      markerContent.style.opacity = '0';
      markerContent.style.transform = 'translateY(40px) scale(0.3)';
      markerContent.style.transition = 'all 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)'; 
      
      markerContent.innerHTML = `
        <div class="bg-white border-2 rounded-2xl px-3 py-1.5 shadow-xl flex items-center gap-2 cursor-pointer relative pointer-events-auto" style="border-color: ${isFood ? '#FF8E53' : '#4FACFE'};">
          <span class="p-1 rounded-lg text-sm flex items-center justify-center" style="background: ${isFood ? '#FFF5F0' : '#E6F4FF'}">
            ${isFood ? '🍜' : '📸'}
          </span>
          <div class="flex flex-col text-left">
            <span class="font-black text-slate-800 text-xs tracking-wide">${spot.name.split(' ')[0]}</span>
            <span class="text-[9px] text-slate-400 font-bold">${isFood ? '地道寻味' : '核心打卡'}</span>
          </div>
        </div>
      `;

      const timer = setTimeout(() => {
        markerContent.style.opacity = '1';
        markerContent.style.transform = 'translateY(0) scale(1)';
        
        const transitionTimer = setTimeout(() => {
          markerContent.style.transform = ''; 
          markerContent.className = 'transform transition-all duration-300 hover:scale-110 hover:-translate-y-2';
        }, 600);
        animationTimersRef.current.push(transitionTimer);
      }, 300 + (idx * 120)); 
      
      animationTimersRef.current.push(timer);

      const marker = new AMap.Marker({
        position: spot.lnglat,
        content: markerContent,
        offset: new AMap.Pixel(-50, -25),
        zIndex: 140,
        zooms: [dynamicMinZoom, 20] 
      });

      marker.on('click', () => {
        map.setZoomAndCenter(15, spot.lnglat, true, 800);
        infoWindow.setContent(`
          <div class="p-3 min-w-[220px] font-sans">
            <h4 class="m-0 mb-2 font-black text-sm text-slate-800 flex items-center gap-1.5">
              ${isFood ? '🍊' : '✨'} ${spot.name}
            </h4>
            <p class="m-0 mb-3 text-xs text-slate-500 leading-relaxed">${spot.desc}</p>
            <div class="bg-slate-50 rounded-lg p-2 text-[11px] text-slate-800 font-bold border-l-2 shadow-sm" style="border-color: ${isFood ? '#FF8E53' : '#4FACFE'};">
              💡 ${spot.recommend}
            </div>
          </div>
        `);
        infoWindow.open(map, marker.getPosition());
      });

      map.add(marker);
      hotspotMarkersRef.current.push(marker);
    });
  };

  const handleDrillDown = (provinceName: string, polygonInstance: any) => {
    const map = mapInstanceRef.current;
    if (!map) return;

    setDrilledProvince(provinceName);
    drilledProvinceRef.current = provinceName;
    setHoveredProvince(null);
    
    const currentMode = activeViewModeRef.current;
    
    if (currentMode === '2D') {
      map.setPitch(0, true, 1800);
      map.setRotation(0, true, 1800);
    } else {
      map.setPitch(50, true, 1800);
      map.setRotation(-8, true, 1800);
    }
    
    map.setFitView([polygonInstance], false, [80, 80, 80, 80], 1800);
    renderHotspots(provinceName);
  };

  const drawChinaInvertedMask = (map: any, AMap: any): Promise<void> => {
    return new Promise((resolve) => {
      const districtSearch = new AMap.DistrictSearch({
        subdistrict: 0,
        extensions: 'all',
        level: 'country'
      });

      districtSearch.search('中国', (status: string, result: any) => {
        if (status === 'complete' && result.districtList && result.districtList.length > 0) {
          const bounds = result.districtList[0].boundaries;
          if (bounds) {
            const outerRing = [
              new AMap.LngLat(-360, 90, true),
              new AMap.LngLat(-360, -90, true),
              new AMap.LngLat(360, -90, true),
              new AMap.LngLat(360, 90, true),
            ];
            const pathArray = [outerRing, ...bounds];

            const maskPolygon = new AMap.Polygon({
              path: pathArray,
              strokeColor: '#cbd5e1', 
              strokeWeight: 1.5,
              fillColor: '#f1f5f9', 
              fillOpacity: 1,
              zIndex: 1, 
              bubble: true 
            });

            map.add(maskPolygon);

            const limitBounds = new AMap.Bounds(
              [73.33, 3.51],  
              [135.05, 53.33] 
            );
            map.setLimitBounds(limitBounds);
          }
        }
        resolve();
      });
    });
  };

  const initProvinceHighlight = async (map: any, AMap: any) => {
    const districtSearch = new AMap.DistrictSearch({
      subdistrict: 0,
      extensions: 'all', 
      level: 'province'
    });

    const searchProvince = (pName: string): Promise<any> => {
      return new Promise((resolve) => {
        districtSearch.search(pName, (status: string, result: any) => resolve({ status, result }));
      });
    };

    for (const provinceName of Object.keys(PROVINCE_DATA)) {
      const { status, result } = await searchProvince(provinceName);
      if (status !== 'complete' || !result.districtList || result.districtList.length === 0) continue;
      
      const bounds = result.districtList[0].boundaries;
      if (!bounds) continue;

      for (let i = 0; i < bounds.length; i++) {
        const provincePolygon = new AMap.Polygon({
          map: map,
          path: bounds[i],
          strokeWeight: 0, 
          strokeColor: 'transparent', 
          fillColor: 'transparent',
          fillOpacity: 0, 
          cursor: 'pointer',
          zIndex: 10
        });

        provincePolygon.on('mouseover', () => {
          if (drilledProvinceRef.current) return; 
          setHoveredProvince(provinceName);
          provincePolygon.setOptions({
            fillColor: '#3b82f6', fillOpacity: 0.25, strokeColor: '#2563eb', strokeWeight: 2, zIndex: 99
          });
        });

        provincePolygon.on('mouseout', () => {
          setHoveredProvince(null);
          provincePolygon.setOptions({
            fillColor: 'transparent', fillOpacity: 0, strokeColor: 'transparent', strokeWeight: 0, zIndex: 10
          });
        });

        provincePolygon.on('click', () => {
          if (drilledProvinceRef.current) return;
          handleDrillDown(provinceName, provincePolygon);
        });

        interactionPolygonsRef.current.push(provincePolygon);
        if (!provincePolygonsRef.current[provinceName]) {
          provincePolygonsRef.current[provinceName] = provincePolygon; 
        }
      }
    }
  };

  useEffect(() => {
    let isMounted = true;

    const initMap = async () => {
      try {
        const AMapLoader = (await import('@amap/amap-jsapi-loader')).default;
        
        (window as any)._AMapSecurityConfig = {
          securityJsCode: '040dccc74a410177c332ce949e385fd4', 
        };

        const AMap = await AMapLoader.load({
          key: '6aa641a76d4176ac26163060f8687673', 
          version: '2.0',
          plugins: ['AMap.ToolBar', 'AMap.Polyline', 'AMap.InfoWindow', 'AMap.Marker', 'AMap.DistrictSearch', 'AMap.Polygon'],
        });

        if (!isMounted || !mapContainerRef.current) return;
        AMapRef.current = AMap;

        const map = new AMap.Map(mapContainerRef.current, {
          zoom: 4.6,
          center: [105.195, 36.861],
          mapStyle: 'amap://styles/light',
          viewMode: '3D',
          pitch: 35, 
          zooms: [3, 18], 
          features: ['bg', 'road', 'building', 'point'] 
        });
        
        mapInstanceRef.current = map;

        map.on('complete', async () => {
          if (!isMounted) return;
          await drawChinaInvertedMask(map, AMap);
          if (isMounted) setIsMapReady(true); 
          initProvinceHighlight(map, AMap); 
        });

      } catch (e: any) {
        console.error('高德地图加载失败:', e);
        setMapError('高德地图引擎加载失败，请检查控制台，或确认 API Key 是否配置正确。');
      }
    };

    initMap();

    return () => {
      isMounted = false;
      if (mapInstanceRef.current) mapInstanceRef.current.destroy();
      animationTimersRef.current.forEach(clearTimeout); 
    };
  }, []);

  useEffect(() => {
    if (!mapInstanceRef.current || !AMapRef.current) return;
    const map = mapInstanceRef.current;

    if (phase === 'drafting') {
      if (drilledProvinceRef.current) {
        handleBackToNational(true);
      }
    } 
    else if (phase === 'deduction') {
      map.setPitch(45, true, 2000);
      map.setZoomAndCenter(11.5, [112.52, 34.65], true, 3500); 
    } 
    else if (phase === 'decision') {
      map.setPitch(0, true, 1500);
    }
  }, [phase]); 

  // 🚨 完美修复：地图气泡重叠与视觉降级
  useEffect(() => {
    if (!mapInstanceRef.current || !AMapRef.current) return;
    const map = mapInstanceRef.current;
    const AMap = AMapRef.current;

    if (phase === 'drafting') {
      if (polylineRef.current) {
        map.remove(polylineRef.current);
        polylineRef.current = null;
      }
      if (markersRef.current && markersRef.current.length > 0) {
        markersRef.current.forEach(m => map.remove(m));
        markersRef.current = [];
      }
    } 
    else if (phase === 'decision') {
      if (polylineRef.current) map.remove(polylineRef.current);
      if (markersRef.current && markersRef.current.length > 0) {
        markersRef.current.forEach(m => map.remove(m));
        markersRef.current = [];
      }

      if (!luoyangRoute || luoyangRoute.length === 0) return;
      const pathCoords = luoyangRoute.map(p => p.lnglat);
      
      const polyline = new AMap.Polyline({
        path: pathCoords,
        isOutline: true,
        outlineColor: '#ffffff',
        borderWeight: 2,
        strokeColor: '#4FACFE', 
        strokeOpacity: 0.9,
        strokeWeight: 7,
        lineJoin: 'round',
        showDir: true
      });
      map.add(polyline);
      polylineRef.current = polyline;

      const infoWindow = new AMap.InfoWindow({ offset: new AMap.Pixel(0, -25) });
      
      luoyangRoute.forEach((point, idx) => {
        // 🔴 判断是否为交通接驳节点
        const isTransport = point.name.includes('交通') || point.name.includes('接驳') || point.name.includes('出行');
        const markerContent = document.createElement('div');
        let markerOffset;

        if (isTransport) {
          // 🔴 交通节点视觉降级：只渲染一个精致的圆点，鼠标移上去才显示文字
          markerContent.innerHTML = `
            <div class="relative flex flex-col items-center group pointer-events-auto">
              <div class="w-3.5 h-3.5 rounded-full border-[3px] border-white shadow-md transition-all duration-300 group-hover:scale-150" style="background-color: ${point.color}"></div>
              <span class="absolute top-5 opacity-0 group-hover:opacity-100 text-[10px] font-bold text-slate-500 bg-white/95 backdrop-blur-sm px-2 py-1 rounded-md shadow-sm whitespace-nowrap transition-opacity pointer-events-none">${point.name}</span>
            </div>
          `;
          markerOffset = new AMap.Pixel(-7, -7);
        } else {
          // 🔴 核心景区/餐饮节点：渲染巨大的高光气泡
          markerContent.innerHTML = `
            <div class="relative flex flex-col items-center group pointer-events-auto">
              <div class="bg-white/95 backdrop-blur-md border-2 rounded-2xl px-3 py-1.5 shadow-lg flex items-center gap-2 whitespace-nowrap transition-all duration-300 group-hover:scale-105 group-hover:-translate-y-1" style="border-color: ${point.color}">
                <div class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] text-white font-black shadow-inner" style="background-color: ${point.color}">
                  ${idx + 1}
                </div>
                <span class="font-extrabold text-xs text-slate-800">${point.name}</span>
              </div>
              <div class="w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[8px] mt-[-1px] group-hover:-translate-y-1 transition-transform duration-300" style="border-top-color: ${point.color}"></div>
            </div>
          `;
          markerOffset = new AMap.Pixel(-30, -40);
        }

        const marker = new AMap.Marker({
          position: point.lnglat,
          content: markerContent,
          offset: markerOffset
        });

        marker.on('click', () => {
          onPoiSelect(idx);
          infoWindow.setContent(`
            <div class="p-2 min-w-[180px]">
              <h4 class="m-0 mb-1 font-bold text-xs text-slate-800">${point.name}</h4>
              <p class="m-0 text-[11px] text-slate-500 leading-relaxed">${point.desc}</p>
            </div>
          `);
          infoWindow.open(map, marker.getPosition());
        });

        map.add(marker);
        markersRef.current.push(marker); 
      });

      map.setFitView([polyline], false, [80, 80, 80, 80], 1200);
    }
  }, [phase, luoyangRoute, onPoiSelect]);

  useEffect(() => {
    if (!mapInstanceRef.current || selectedPoiIndex === null || luoyangRoute.length === 0) return;
    const map = mapInstanceRef.current;
    const targetPoi = luoyangRoute[selectedPoiIndex];
    if (targetPoi) {
      map.setZoomAndCenter(14.5, targetPoi.lnglat, true, 1000);
    }
  }, [selectedPoiIndex, luoyangRoute]);

  const activeHoverData = hoveredProvince ? PROVINCE_DATA[hoveredProvince] : null;

  return (
    <div className="absolute inset-0 w-full h-full overflow-hidden group/map bg-slate-100 flex items-center justify-center">
      
      <AnimatePresence>
        {!isMapReady && !mapError && (
          <motion.div 
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.8, ease: "easeInOut" }}
            className="absolute inset-0 z-[120] bg-slate-50 flex flex-col items-center justify-center backdrop-blur-sm"
          >
            <div className="relative flex flex-col items-center">
              <Loader2 className="w-10 h-10 text-blue-500 animate-spin mb-4" />
              <div className="text-slate-800 font-black tracking-widest text-sm mb-1">正在初始化沙盘引擎</div>
              <div className="text-slate-400 font-bold text-[10px] tracking-wider animate-pulse">
                INITIALIZING GEO-DATA...
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {mapError ? (
        <div className="bg-white border border-rose-200 p-6 rounded-3xl shadow-xl flex flex-col items-center max-w-sm text-center z-[70]">
          <div className="w-12 h-12 bg-rose-50 text-rose-500 rounded-full flex items-center justify-center mb-4">
            <AlertCircle className="w-6 h-6" />
          </div>
          <h3 className="text-lg font-black text-slate-800 mb-2">地图引擎初始化失败</h3>
          <p className="text-sm text-slate-500 font-medium leading-relaxed">
            {mapError}
          </p>
        </div>
      ) : (
        <div ref={mapContainerRef} className="absolute inset-0 w-full h-full" />
      )}

      {isMapReady && (
        <>
          <div className="absolute top-6 left-6 right-6 z-[100] pointer-events-none flex justify-between items-start">
            <button 
              onClick={onExit}
              className="pointer-events-auto bg-white/95 backdrop-blur-md border border-slate-200/80 text-slate-700 px-4 py-2.5 rounded-2xl font-black text-[13px] shadow-[0_8px_20px_rgba(0,0,0,0.06)] flex items-center gap-2 hover:bg-slate-50 hover:text-blue-600 transition-all hover:scale-105"
            >
              <LogOut className="w-4 h-4" />
              返回上一级
            </button>
            <button 
              onClick={onWakeAgent}
              className="pointer-events-auto bg-slate-800 text-white px-5 py-2.5 rounded-2xl font-black text-[13px] shadow-[0_15px_30px_rgba(0,0,0,0.2)] flex items-center gap-2 hover:bg-blue-600 hover:-translate-y-1 transition-all"
            >
              <MessageSquareText className="w-4 h-4" />
              展开智能体面板
            </button>
          </div>

          {!drilledProvince && (
            <div className="absolute left-6 top-24 bottom-24 w-72 z-40 flex flex-col gap-3 overflow-y-auto pointer-events-none custom-scrollbar pb-6 pl-2 -ml-2 pt-2 -mt-2">
              <div className="bg-white/90 backdrop-blur-xl border border-slate-200/80 p-4 rounded-2xl shadow-lg pointer-events-auto">
                <h2 className="text-sm font-black text-slate-800 mb-1">全国探索图鉴</h2>
                <p className="text-[10px] text-slate-500 font-bold">点击卡片下钻至对应省份</p>
              </div>

              {Object.entries(PROVINCE_DATA).map(([provName, provData]) => (
                <div 
                  key={provName}
                  onClick={() => {
                    const polygon = provincePolygonsRef.current[provName];
                    if (polygon) {
                      handleDrillDown(provName, polygon);
                    }
                  }}
                  onMouseEnter={() => setHoveredProvince(provName)} 
                  onMouseLeave={() => setHoveredProvince(null)}
                  className="bg-white/85 backdrop-blur-md border border-slate-200/60 p-4 rounded-2xl shadow-md pointer-events-auto cursor-pointer transform transition-all duration-300 hover:scale-[1.02] hover:bg-white hover:shadow-xl hover:border-blue-300 group"
                >
                  <div className="flex justify-between items-center mb-2">
                    <span className="font-black text-slate-800 text-base">{provName}</span>
                    <span className="bg-blue-50 text-blue-600 px-2 py-0.5 rounded-lg text-xs font-bold font-mono">
                      {provData.summary.score} 分
                    </span>
                  </div>
                  
                  <div className="flex flex-wrap gap-1 mb-3">
                    {provData.summary.tags.map(tag => (
                      <span key={tag} className="text-[9px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">
                        {tag}
                      </span>
                    ))}
                  </div>
                  
                  <div className="text-[10px] text-slate-400 font-bold flex items-center justify-between group-hover:text-blue-500 transition-colors">
                    <span>包含 {provData.summary.hotCount} 个智能体节点</span>
                    <span>探索 →</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {drilledProvince && (
            <motion.div 
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              className="absolute top-24 left-6 z-50 bg-white/95 backdrop-blur-xl border border-slate-200/80 px-5 py-3 rounded-2xl shadow-[0_10px_30px_rgba(0,0,0,0.08)] text-[13px] font-bold text-slate-600 flex items-center gap-4 transition-all"
            >
              <button 
                className="hover:text-blue-600 transition-colors flex items-center gap-1.5 bg-slate-100 hover:bg-blue-50 px-3 py-1.5 rounded-lg" 
                onClick={() => handleBackToNational(true)}
              >
                <ArrowLeft className="w-4 h-4"/> 全国宏观
              </button>
              <div className="w-px h-4 bg-slate-200"></div>
              <span className="text-slate-800 tracking-wide flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse ring-4 ring-blue-500/20"></span>
                {drilledProvince} 空间
              </span>
            </motion.div>
          )}

          <AnimatePresence>
            {activeHoverData && hoveredProvince && (
              <motion.div 
                initial={{ opacity: 0, x: 50, scale: 0.85 }}
                animate={{ opacity: 1, x: 0, scale: 1.05 }} 
                exit={{ opacity: 0, x: 30, scale: 0.95 }}
                transition={{ type: "spring", stiffness: 260, damping: 20 }}
                className="absolute top-24 right-6 z-50 w-72 bg-white/95 backdrop-blur-2xl border border-slate-200/60 rounded-3xl p-6 shadow-[0_20px_50px_rgba(0,0,0,0.12)] pointer-events-none origin-top-right"
              >
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-xl font-black text-slate-800 tracking-tight">{hoveredProvince}</h3>
                    <p className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mt-0.5">Statistical Profiling</p>
                  </div>
                  <div className="bg-orange-50 px-2.5 py-1 rounded-xl text-center shadow-sm">
                    <span className="text-sm font-black text-orange-600 font-mono">{activeHoverData.summary.score}</span>
                    <p className="text-[8px] text-orange-400 font-bold scale-90">推荐度</p>
                  </div>
                </div>

                <div className="flex flex-wrap gap-1.5 mb-4">
                  {activeHoverData.summary.tags.map(t => (
                    <span key={t} className="text-[10px] font-bold px-2 py-0.5 bg-slate-50 border border-slate-200 shadow-sm text-slate-600 rounded-md">{t}</span>
                  ))}
                </div>

                <div className="border-t border-slate-100 pt-4">
                  <div className="flex justify-between items-center mb-2.5">
                    <span className="text-xs font-black text-slate-400 flex items-center gap-1"><Layers className="w-3.5 h-3.5"/> 智能体候选点位</span>
                    <span className="text-xs font-bold text-slate-700 font-mono">{activeHoverData.summary.hotCount} 项缓存</span>
                  </div>
                  <div className="space-y-1.5">
                    {activeHoverData.summary.poyPreviews.map((p) => (
                      <div key={p} className="flex items-center gap-2 text-xs font-bold text-slate-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400 shadow-[0_0_8px_rgba(96,165,250,0.8)]"></span>
                        {p}
                      </div>
                    ))}
                  </div>
                </div>
                
                <div className="mt-5 text-[11px] text-white bg-blue-500 rounded-lg py-2 font-bold flex items-center justify-center gap-1 shadow-md">
                  点击省份下钻区域细节 →
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="absolute bottom-8 right-8 z-40 bg-white/90 backdrop-blur-xl border border-slate-200/80 p-1.5 rounded-2xl shadow-[0_15px_40px_rgba(0,0,0,0.08)] flex gap-1">
            <button 
              onClick={() => handleViewAngleChange('2D')}
              className={`px-4 py-2.5 rounded-xl text-xs font-bold transition-all flex items-center gap-2 ${activeViewMode === '2D' ? 'bg-slate-800 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'}`}
            >
              <MapIcon className="w-4 h-4" /> 2D 平面
            </button>
            <button 
              onClick={() => handleViewAngleChange('3D')}
              className={`px-4 py-2.5 rounded-xl text-xs font-bold transition-all flex items-center gap-2 ${activeViewMode === '3D' ? 'bg-slate-800 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'}`}
            >
              <Box className="w-4 h-4" /> 3D 鸟瞰
            </button>
            <button 
              onClick={() => handleViewAngleChange('ROAM')}
              className={`px-4 py-2.5 rounded-xl text-xs font-bold transition-all flex items-center gap-2 ${activeViewMode === 'ROAM' ? 'bg-slate-800 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100 hover:text-slate-800'}`}
            >
              <Compass className="w-4 h-4" /> 沉浸漫游
            </button>
          </div>
        </>
      )}
    </div>
  );
}