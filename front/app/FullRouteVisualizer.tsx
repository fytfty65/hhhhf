'use client';

import React, { useState, useRef, useEffect, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import dynamic from 'next/dynamic';
import InteractiveAmapComponent, { RoutePoint } from './InteractiveAmapComponent';

const Globe = dynamic(() => import('react-globe.gl'), { ssr: false }) as any;

export default function FullRouteVisualizer({
  phase, 
  routes = [], 
  actualPath = [], 
  selectedPoiIndex, 
  onPoiSelect, 
  onWakeAgent, 
  onExit, 
  targetCityInfo
}: any) {
  // 利用父组件传来的 phase 自动控制宏观 (3D地球) / 微观 (2D高德) 视界
  const [viewState, setViewState] = useState<'MACRO' | 'MICRO'>('MACRO');
  const [windowReady, setWindowReady] = useState(false);
  
  // 👑 关键修复：动态尺寸监听，保证 3D 地球 100% 居中！
  const containerRef = useRef<HTMLDivElement>(null);
  const [globeDimensions, setGlobeDimensions] = useState({ width: 800, height: 600 });
  const globeEl = useRef<any>(null);

  useEffect(() => {
    setWindowReady(true);
  }, []);

  // 监听父容器实时尺寸，彻底解决 Three.js Canvas 偏右问题
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

  // 1. 当地球收到后端发来的“目标城市坐标”时，控制 3D 镜头平滑飞越过去
  useEffect(() => {
    if (targetCityInfo && globeEl.current && (phase === 'deduction' || phase === 'drafting')) {
      globeEl.current.pointOfView({ 
        lat: targetCityInfo.lnglat[1], 
        lng: targetCityInfo.lnglat[0], 
        altitude: 1.5 // 完美地球视角弧度
      }, 2000);
    }
  }, [targetCityInfo, phase]);

  // 2. 当推演完成并返回了真实路线数据，自动平滑下钻到 2D 高德地图
  useEffect(() => {
    if (phase === 'decision' && routes.length > 0) {
      setViewState('MICRO');
    } else {
      setViewState('MACRO');
    }
  }, [phase, routes]);

  // 大交通橙黄科技飞线
  const macroArcs = [
    { startLat: 39.90, startLng: 116.40, endLat: targetCityInfo?.lnglat[1] || 20.03, endLng: targetCityInfo?.lnglat[0] || 110.34, color: ['#ffffff', '#f97316'] },
    { startLat: 31.23, startLng: 121.47, endLat: targetCityInfo?.lnglat[1] || 20.03, endLng: targetCityInfo?.lnglat[0] || 110.34, color: ['#f97316', '#eab308'] } 
  ];

  // 3. 👑 动态生成地球上的马蜂窝风格发光标点
  const elementsData = targetCityInfo ? [{
    name: targetCityInfo.name,
    desc: '全域雷达正在扫描当地时空拓扑...',
    lat: targetCityInfo.lnglat[1],
    lng: targetCityInfo.lnglat[0]
  }] : [];

  if (!windowReady) return null;

  return (
    <div ref={containerRef} className="relative w-full h-full bg-slate-900 overflow-hidden flex items-center justify-center">
      
      {/* 适配深色/浅色混搭的科技感网格背景 */}
      <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCI+PGRlZnM+PHBhdHRlcm4gaWQ9ImdyaWQiIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCIgcGF0dGVyblVuaXRzPSJ1c2VyU3BhY2VPblVzZSI+PHBhdGggZD0iTSAwIDEwIEwgNDAgMTAgTSAxMCAwIEwgMTAgNDAiIGZpbGw9Im5vbmUiIHN0cm9rZT0icmdiYSgyNTUsMjU1LDI1NSwwLjA1KSIgc3Ryb2tlLXdpZHRoPSIxIi8+PC9wYXR0ZXJuPjwvZGVmcz48cmVjdCB3aWR0aD0iMTAwJSIgaGVpZ2h0PSIxMDAlIiBmaWxsPSJ1cmwoI2dyaWQpIi8+PC9zdmc+')] pointer-events-none"></div>

      <AnimatePresence mode="wait">
        {viewState === 'MACRO' && (
          <motion.div 
            key="macro-view-globe"
            initial={{ opacity: 0 }} 
            animate={{ opacity: 1 }} 
            exit={{ opacity: 0, scale: 1.4 }}
            transition={{ duration: 0.6 }}
            className="absolute inset-0 w-full h-full flex items-center justify-center cursor-grab active:cursor-grabbing"
          >
            <Globe
              ref={globeEl}
              globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
              bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
              backgroundColor="rgba(0,0,0,0)"
              
              // 👑 传给 Canvas 准确的组件居中宽高
              width={globeDimensions.width}
              height={globeDimensions.height}
              
              showAtmosphere={true}
              atmosphereColor="#f97316" // 暖橙色大气晕辉
              atmosphereAltitude={0.18}

              arcsData={macroArcs}
              arcStartLat="startLat" arcStartLng="startLng" 
              arcEndLat="endLat" arcEndLng="endLng"
              arcColor="color" 
              arcDashLength={0.5} arcDashGap={0.2} arcDashAnimateTime={2000}
              
              // 👑 动态 HTML 地标渲染（完美对齐马蜂窝橙色风）
              htmlElementsData={elementsData}
              htmlElement={(d: any) => {
                 const el = document.createElement('div');
                 el.style.pointerEvents = 'auto'; 
                 el.innerHTML = `
                  <div class="flex flex-col items-center group cursor-pointer">
                    <div class="bg-white/95 backdrop-blur-md p-3.5 rounded-2xl shadow-[0_10px_35px_rgba(249,115,22,0.4)] border border-orange-200 w-56 transform transition-all duration-300 hover:scale-105">
                      <div class="flex justify-between items-start mb-1">
                        <span class="text-[10px] font-black text-orange-600 bg-orange-50 border border-orange-200 px-2 py-0.5 rounded-full uppercase tracking-wider">🎯 目标坐标锁定</span>
                      </div>
                      <h3 class="text-lg font-black text-slate-800 tracking-tight">${d.name}</h3>
                      <p class="text-[11px] text-orange-500 font-bold mt-0.5 animate-pulse">${d.desc}</p>
                    </div>
                    {/* 连接柱与发光卡槽 */}
                    <div class="w-0.5 h-6 bg-gradient-to-b from-orange-400 to-transparent opacity-80"></div>
                    <div class="w-4 h-4 bg-orange-500 rounded-full ring-4 ring-orange-200/80 shadow-[0_0_20px_#f97316] animate-ping"></div>
                  </div>
                 `;
                 return el;
              }}
            />
            
            {/* 左上角状态浮窗 */}
            <div className="absolute top-8 left-8 z-10 pointer-events-none">
              <div className="bg-white/90 backdrop-blur-md border border-slate-200/80 shadow-2xl p-5 rounded-3xl max-w-sm">
                <h2 className="text-xl font-black text-slate-800 tracking-tight flex items-center gap-2.5">
                  <div className={`w-3 h-3 rounded-full ${phase === 'deduction' ? 'bg-orange-500 animate-pulse' : 'bg-orange-500'}`}></div> 
                  全域时空沙盘
                </h2>
                <p className="text-slate-500 mt-1.5 text-xs font-medium leading-relaxed">
                  {phase === 'deduction' 
                    ? `多智能体正在全球版图中定位【${targetCityInfo?.name || '目标城市'}】并计算最优时空拓扑...` 
                    : '请在左侧输入您的旅行想法，唤醒多智能体协同规划。'}
                </p>
              </div>
            </div>
          </motion.div>
        )}

        {viewState === 'MICRO' && (
          <motion.div 
            key="micro-view-amap"
            initial={{ opacity: 0, scale: 0.98 }} 
            animate={{ opacity: 1, scale: 1 }} 
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6 }}
            className="absolute inset-0 bg-slate-100"
          >
            {/* 动态传入后端生成的真实路书和高德路网轨迹 */}
            <InteractiveAmapComponent 
              phase={phase}
              selectedPoiIndex={selectedPoiIndex}
              onPoiSelect={onPoiSelect}
              luoyangRoute={routes} 
              actualPath={actualPath}
              onExit={onExit} 
              onWakeAgent={onWakeAgent}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}