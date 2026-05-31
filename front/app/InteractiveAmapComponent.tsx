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
  time?: string;
  transport?: string;
  tags?: string[];
  cost?: string;
  trust_reason?: string;
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
        setMapError('高德地图引擎加载失败，请确认 API Key 是否配置正确。');
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
    } 
    else if (phase === 'decision') {
      map.setPitch(25, true, 1500);
    }
  }, [phase]); 

// 🚨 国一级别：动态环形避让（Spiderfy）与毛玻璃拟态图标引擎
  // 🏆 国一商业级引擎：空间碰撞环形展开 (Spiderfy) 与毛玻璃拟态 UI
  useEffect(() => {
    if (!mapInstanceRef.current || !AMapRef.current) return;
    const map = mapInstanceRef.current;
    const AMap = AMapRef.current;

    // 清理旧图层
    if (polylineRef.current) map.remove(polylineRef.current);
    markersRef.current.forEach(m => map.remove(m));
    markersRef.current = [];

    if (!luoyangRoute || luoyangRoute.length === 0) return;

      // 过滤掉坐标无效的路由点，防止高德地图崩溃
      const validRoute = luoyangRoute.filter(p => {
        const lnglat = p?.lnglat;
        return Array.isArray(lnglat) && lnglat.length === 2
          && typeof lnglat[0] === 'number' && isFinite(lnglat[0])
          && typeof lnglat[1] === 'number' && isFinite(lnglat[1])
          && lnglat[0] >= -180 && lnglat[0] <= 180
          && lnglat[1] >= -90 && lnglat[1] <= 90;
      });

      if (validRoute.length === 0) {
        console.warn('⚠️ 所有路由节点坐标均无效，跳过地图渲染');
        return;
      }

      // 1. 绘制流光路线 (Polyline)
      const pathCoords = validRoute.map(p => p.lnglat);
    const polyline = new AMap.Polyline({
      path: pathCoords,
      isOutline: true,
      outlineColor: '#ffffff',
      borderWeight: 3,
      strokeColor: '#3b82f6', 
      strokeOpacity: 0.8,
      strokeWeight: 6,
      lineJoin: 'round',
      lineCap: 'round',
      showDir: true,
      dirColor: '#ffffff'
    });
    map.add(polyline);
    polylineRef.current = polyline;

    // 2. 核心空间算法：多节点碰撞检测与聚类分组
    const overlapGroups: { [key: string]: number[] } = {};
    const processed = new Set<number>();
    
    for (let i = 0; i < validRoute.length; i++) {
      if (processed.has(i)) continue;
      const group = [i];
      for (let j = i + 1; j < validRoute.length; j++) {
        // 计算经纬度差值，0.0015 约等于 150米。如果距离极近，判定为重叠！
        const distLat = Math.abs(validRoute[i].lnglat[1] - validRoute[j].lnglat[1]);
        const distLng = Math.abs(validRoute[i].lnglat[0] - validRoute[j].lnglat[0]);
        if (distLat < 0.0015 && distLng < 0.0015) {
          group.push(j);
          processed.add(j);
        }
      }
      overlapGroups[i] = group;
      processed.add(i);
    }

    // 3. 渲染高颜值拟态 Marker
    Object.values(overlapGroups).forEach(groupIndices => {
      const groupSize = groupIndices.length;
      // 提取该组的中心绝对物理坐标
      const centerLngLat = validRoute[groupIndices[0]].lnglat;
      
      // 如果触发碰撞，在中心点生成一个动态脉冲光核（Hub）
      if (groupSize > 1) {
        const hubContent = document.createElement('div');
        hubContent.innerHTML = `<div class="w-5 h-5 rounded-full bg-blue-500/20 border-2 border-blue-500 flex items-center justify-center animate-pulse" style="transform: translate(-50%, -50%);"><div class="w-2 h-2 bg-blue-600 rounded-full shadow-[0_0_8px_#3b82f6]"></div></div>`;
        const hubMarker = new AMap.Marker({
          position: centerLngLat,
          content: hubContent,
          offset: new AMap.Pixel(0, 0),
          zIndex: 50
        });
        map.add(hubMarker);
        markersRef.current.push(hubMarker);
      }

      // 渲染节点
      groupIndices.forEach((idx, orderInGroup) => {
        const point = validRoute[idx];
        const isTransport = point.name.includes('交通') || point.name.includes('接驳') || point.name.includes('出行');
        const isFood = point.color === "#f97316" || (point.tags && point.tags.includes("寻味")); 
        
        let scatterX = 0;
        let scatterY = 0;
        
        // 🌟 物理引擎：Spiderfy 环形引力场散开计算
        if (groupSize > 1) {
          const angle = (Math.PI * 2 * orderInGroup) / groupSize;
          const radius = 55; // 散开半径（像素）
          scatterX = Math.cos(angle) * radius;
          scatterY = Math.sin(angle) * radius;
        }

        const markerContent = document.createElement('div');
        markerContent.style.position = 'absolute'; // 释放 AMap 的绝对定位限制
        
        const themeColor = isFood ? 'from-orange-500 to-rose-500' : 'from-blue-500 to-indigo-500';
        
        if (isTransport) {
          // 交通极简发光粒子
          markerContent.innerHTML = `
            <div class="relative flex flex-col items-center group cursor-pointer" style="transform: translate(calc(-50% + ${scatterX}px), calc(-50% + ${scatterY}px)); z-index: ${selectedPoiIndex === idx ? 999 : 100};">
              <div class="w-4 h-4 rounded-full bg-slate-800 border-2 border-white shadow-md transition-all duration-300 group-hover:scale-125 group-hover:bg-blue-500"></div>
              <span class="absolute top-5 bg-slate-900/90 text-white text-[10px] font-bold px-2 py-1 rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-[1000] pointer-events-none">
                ${point.name}
              </span>
            </div>
          `;
        } else {
          // 💎 Airbnb级 现代玻璃拟态胶囊（纯内联规避框架拦截）
          markerContent.innerHTML = `
            <div class="relative group cursor-pointer transition-all duration-300" style="transform: translate(calc(-50% + ${scatterX}px), calc(-100% + ${scatterY}px)); z-index: ${selectedPoiIndex === idx ? 999 : 100};">
              <div class="bg-white/95 backdrop-blur-md border border-slate-200 shadow-[0_10px_40px_rgba(0,0,0,0.12)] rounded-full p-1.5 flex items-center gap-2.5 transition-all duration-300 group-hover:scale-105 group-hover:-translate-y-1 group-hover:shadow-2xl">
                 <div class="w-8 h-8 rounded-full bg-gradient-to-tr ${themeColor} shadow-inner flex items-center justify-center text-white font-black text-sm border border-white/50">
                    ${idx + 1}
                 </div>
                 <div class="flex flex-col pr-3 pb-0.5">
                    <span class="font-extrabold text-slate-800 text-[13px] tracking-tight whitespace-nowrap">${point.name.substring(0, 10)}</span>
                    <span class="text-[9px] text-slate-500 font-bold leading-none mt-0.5">${isFood ? '🍊 必吃打卡' : '✨ 核心体验'}</span>
                 </div>
              </div>
              <div class="absolute left-1/2 -bottom-[6px] -translate-x-1/2 w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[8px] border-t-white/95 drop-shadow-sm transition-all duration-300 group-hover:-translate-y-1"></div>
            </div>
          `;
        }

        const marker = new AMap.Marker({
          position: centerLngLat, // 必须将锚点统一绑定在真实的中心坐标，防止轨道错乱！
          content: markerContent,
          offset: new AMap.Pixel(0, 0), // 依靠 CSS 完美执行居中运算
          zIndex: selectedPoiIndex === idx ? 999 : (100 - idx),
          extData: { index: idx }
        });

        // 图层焦点跃迁引擎
        markerContent.addEventListener('mouseenter', () => marker.setzIndex(1000));
        markerContent.addEventListener('mouseleave', () => marker.setzIndex(selectedPoiIndex === idx ? 999 : (100 - idx)));

        // 高端信息卡片弹窗
        marker.on('click', () => {
          onPoiSelect(idx);
          map.clearInfoWindow(); // 清除之前的实例，防止残留
          
          const windowHtml = `
            <div class="bg-white/95 backdrop-blur-2xl rounded-3xl p-5 shadow-[0_20px_60px_rgba(0,0,0,0.12)] border border-slate-100 min-w-[260px] max-w-[300px] font-sans relative overflow-hidden">
              <div class="absolute top-0 left-0 w-full h-1.5 bg-gradient-to-r ${themeColor}"></div>
              <div class="flex justify-between items-start mb-3 mt-1">
                <h4 class="m-0 font-black text-base text-slate-800 leading-snug">${point.name}</h4>
                ${point.time ? `<span class="bg-slate-100 text-slate-600 text-[10px] font-black px-2 py-1 rounded-lg whitespace-nowrap ml-3">${point.time.split('|')[1] || point.time}</span>` : ''}
              </div>
              <p class="m-0 text-[13px] text-slate-500 font-medium leading-relaxed mb-4">${point.desc}</p>
              ${point.trust_reason ? `
                <div class="bg-slate-50/80 rounded-xl p-3 border border-slate-100/80">
                  <div class="text-[10px] font-black text-blue-600 uppercase tracking-wider mb-1 flex items-center gap-1">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path></svg>
                    推演依据
                  </div>
                  <div class="text-xs text-slate-600 font-medium leading-snug">${point.trust_reason}</div>
                </div>
              ` : ''}
            </div>
          `;
          
          const dynamicInfoWindow = new AMap.InfoWindow({
             isCustom: true,
             autoMove: true,
             content: windowHtml,
             // 动态计算气泡散开后的绝对偏移量，保证弹窗能精确贴合散开后的胶囊！
             offset: new AMap.Pixel(scatterX, scatterY - 50)
          });
          dynamicInfoWindow.open(map, marker.getPosition());
        });

        map.add(marker);
        markersRef.current.push(marker); 
      });
    });

    // 智能视口缩放：确保散开点完全处于屏幕视野
    map.setFitView([polyline, ...markersRef.current], false, [100, 100, 100, 100], 1200);

  }, [phase, luoyangRoute, selectedPoiIndex, onPoiSelect]);

  useEffect(() => {
    if (!mapInstanceRef.current || selectedPoiIndex === null || luoyangRoute.length === 0) return;
    const map = mapInstanceRef.current;
    const targetPoi = luoyangRoute[selectedPoiIndex];
    if (targetPoi) {
      const lnglat = targetPoi.lnglat;
      if (Array.isArray(lnglat) && lnglat.length === 2
        && typeof lnglat[0] === 'number' && isFinite(lnglat[0])
        && typeof lnglat[1] === 'number' && isFinite(lnglat[1])) {
        map.setZoomAndCenter(15, lnglat, true, 1000);
      }
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
          <p className="text-sm text-slate-500 font-medium leading-relaxed">{mapError}</p>
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
                <p className="text-[10px] text-slate-500 font-bold">点击省份下钻宏观热点</p>
              </div>

              {Object.entries(PROVINCE_DATA).map(([provName, provData]) => (
                <div 
                  key={provName}
                  onClick={() => {
                    const polygon = provincePolygonsRef.current[provName];
                    if (polygon) handleDrillDown(provName, polygon);
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

          <div className="absolute bottom-8 right-8 z-40 bg-white/90 backdrop-blur-xl border border-slate-200/80 p-1.5 rounded-2xl shadow-[0_15px_40px_rgba(0,0,0,0.08)] flex gap-1">
            <button onClick={() => handleViewAngleChange('2D')} className={`px-4 py-2.5 rounded-xl text-xs font-bold transition-all ${activeViewMode === '2D' ? 'bg-slate-800 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100'}`}><MapIcon className="w-4 h-4 inline mr-1" /> 2D 平面</button>
            <button onClick={() => handleViewAngleChange('3D')} className={`px-4 py-2.5 rounded-xl text-xs font-bold transition-all ${activeViewMode === '3D' ? 'bg-slate-800 text-white shadow-md' : 'text-slate-500 hover:bg-slate-100'}`}><Box className="w-4 h-4 inline mr-1" /> 3D 鸟瞰</button>
          </div>
        </>
      )}
    </div>
  );
}