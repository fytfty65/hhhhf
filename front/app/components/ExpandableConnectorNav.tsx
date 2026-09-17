'use client';

/**
 * ExpandableConnectorNav — 两个行程节点之间的多模态出行导航卡片（驾车/公交/步行 + 高德实时导航入口）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 2)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowDown, Car, Navigation, ChevronUp, ChevronDown, MapPin } from 'lucide-react';

export default function ExpandableConnectorNav({ pt, nextPt, travelDetails }: { pt: any; nextPt: any; travelDetails: any }) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'driving' | 'transit' | 'walking'>('driving');

  const pairKey = `${pt.name}|${nextPt?.name}`;
  const reversePairKey = `${nextPt?.name}|${pt.name}`;
  const details = travelDetails?.[pairKey] 
                || travelDetails?.[reversePairKey] 
                || travelDetails?.[pt.name] 
                || travelDetails?.[nextPt?.name] 
                || {};

  const currentModeData = details[activeTab] || details[Object.keys(details)[0]] || {};

  const label = currentModeData?.label || '出行';
  // 绝不使用硬编码默认值：拿不到真实时长/距离就留空，由界面引导打开高德实景导航
  const durMin = currentModeData?.duration_min;
  const distKm = currentModeData?.distance_km;

  let steps: string[] = [];
  if (currentModeData?.steps && Array.isArray(currentModeData.steps) && currentModeData.steps.length > 0) {
    steps = currentModeData.steps
      .map((s: any) => {
        if (typeof s === 'string') return s.replace(/<\/?[^>]+(>|$)/g, '').trim();
        const inst = s?.instruction || s?.road || '';
        return inst.replace(/<\/?[^>]+(>|$)/g, '').trim();
      })
      .filter((s: string) => Boolean(s && s.length > 0));
  }

  // 👑 是否拿到高德真实导航数据；拿不到时绝不编造时长/距离，改为引导打开真实导航
  const hasRealNav = Object.keys(details || {}).length > 0;
  const fromLnglat = Array.isArray(pt?.lnglat) ? pt.lnglat : null;
  const toLnglat = Array.isArray(nextPt?.lnglat) ? nextPt.lnglat : null;
  let navHref = '';
  if (fromLnglat && fromLnglat.length >= 2 && toLnglat && toLnglat.length >= 2) {
    navHref = `https://uri.amap.com/navigation?from=${fromLnglat[0]},${fromLnglat[1]},${encodeURIComponent(pt.name)}&to=${toLnglat[0]},${toLnglat[1]},${encodeURIComponent(nextPt.name)}&mode=car`;
  } else if (nextPt?.amap_url) {
    navHref = nextPt.amap_url;
  }

  return (
    <div className="mt-8 mb-4 relative">
      <div 
        onClick={() => setIsOpen(!isOpen)}
        className="absolute -left-[27px] top-2.5 w-5 h-5 bg-white border border-slate-300 rounded-full flex items-center justify-center z-10 text-slate-400 hover:border-orange-500 hover:text-orange-500 cursor-pointer shadow-2xs transition-colors"
      >
        <ArrowDown className="w-3 h-3" />
      </div>

      <button
        onClick={() => setIsOpen(!isOpen)}
        className="ml-1 w-full flex items-center justify-between py-2.5 px-4 bg-slate-50 hover:bg-orange-50/80 rounded-2xl border border-slate-200/80 hover:border-orange-200 text-xs transition-all cursor-pointer shadow-2xs group"
      >
        <div className="flex items-center gap-2 text-slate-600">
          <span className="text-slate-400 font-medium">推荐前往【{nextPt.name}】：</span>
          {hasRealNav ? (
            <>
              <span className="font-bold text-slate-700 flex items-center gap-1">
                <Car className="w-3.5 h-3.5 text-orange-500 group-hover:scale-110 transition-transform"/> {label}
              </span>
              {(durMin != null || distKm != null) && (
                <>
                  <span className="text-slate-300">·</span>
                  <span className="font-bold text-slate-600">
                    {durMin != null ? `约 ${durMin} 分钟` : ''}{distKm != null ? ` (${distKm}km)` : ''}
                  </span>
                </>
              )}
            </>
          ) : (
            <span className="font-bold text-orange-600 flex items-center gap-1">
              <Navigation className="w-3.5 h-3.5" /> 点击查看高德实景导航
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 text-[11px] font-bold text-orange-500">
          <span>{isOpen ? '收起路线' : (hasRealNav ? '展开多模态高德逐字导航' : '查看导航')}</span>
          {isOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden mt-2 ml-1 bg-slate-900 text-slate-200 p-3.5 rounded-2xl text-xs space-y-3 border border-slate-800 shadow-lg"
          >
            <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
              {details.driving && (
                <button 
                  onClick={() => setActiveTab('driving')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'driving' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚗 驾车 ({details.driving.duration_min}分)
                </button>
              )}
              {details.transit && (
                <button 
                  onClick={() => setActiveTab('transit')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'transit' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚌 公交/地铁 ({details.transit.duration_min}分)
                </button>
              )}
              {details.walking && (
                <button 
                  onClick={() => setActiveTab('walking')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'walking' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚶 步行 ({details.walking.duration_min}分)
                </button>
              )}
            </div>

            <div className="font-bold text-orange-400 flex items-center gap-1.5 pt-0.5">
              <MapPin className="w-3.5 h-3.5 shrink-0" />
              <span>高德地图实测【{currentModeData.label || '出行'}】线路指引（{pt.name} → {nextPt.name}）：</span>
            </div>

            {steps.length > 0 ? (
              <div className="space-y-1.5 pt-1">
                {steps.map((step, idx) => (
                  <div key={idx} className="text-[11px] text-slate-300 flex items-start gap-2 leading-relaxed">
                    <span className="text-orange-400 font-bold shrink-0">{idx + 1}.</span>
                    <span>{step}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-300 leading-relaxed pt-1">
                该路段暂未获取到逐字导航指引。
              </div>
            )}
            {/* 👑 无论有无逐字指引，始终显示高德实时导航按钮 */}
            <div className="pt-2 border-t border-slate-800">
              {navHref ? (
                <a
                  href={navHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-500/90 hover:bg-orange-500 text-white font-bold transition-colors"
                >
                  <Navigation className="w-3.5 h-3.5" /> 打开高德实时导航
                </a>
              ) : (
                <a
                  href={`https://www.amap.com/search?query=${encodeURIComponent(nextPt?.name || '')}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-500/90 hover:bg-orange-500 text-white font-bold transition-colors"
                >
                  <Navigation className="w-3.5 h-3.5" /> 打开高德搜索导航
                </a>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
