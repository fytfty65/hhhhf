'use client';

/**
 * BottomMapCarousel — 工作区底部「行程节点」横向卡片条（选中项自动滚动到视口中央）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 2)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { useEffect, useRef } from 'react';
import { ChevronLeft, ChevronRight, Star } from 'lucide-react';
import RealPoiImage from './RealPoiImage';

export default function BottomMapCarousel({ selectedIndex, onSelect, routes }: any) {
  if (!routes || routes.length === 0) return null;
  
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (scrollContainerRef.current && selectedIndex !== null) {
      const container = scrollContainerRef.current;
      const child = container.children[selectedIndex] as HTMLElement;
      if (child) {
        const scrollLeft = child.offsetLeft - container.offsetWidth / 2 + child.offsetWidth / 2;
        container.scrollTo({ left: scrollLeft, behavior: 'smooth' });
      }
    }
  }, [selectedIndex]);

  return (
    <div className="w-full bg-gradient-to-t from-slate-950/95 via-slate-900/60 to-transparent pt-16 pb-6 px-8 backdrop-blur-sm">
      <div className="max-w-6xl mx-auto mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-1 h-4 bg-gradient-to-b from-orange-400 to-rose-500 rounded-full" />
          <span className="text-white/90 text-xs font-bold tracking-wider">行程节点</span>
          <span className="text-white/40 text-[10px] font-mono">{routes.length} POIs</span>
        </div>
        <div className="flex items-center gap-1 text-white/30 text-[10px]">
          <ChevronLeft className="w-3 h-3" />
          <span>横向滑动浏览</span>
          <ChevronRight className="w-3 h-3" />
        </div>
      </div>
      <div ref={scrollContainerRef} className="flex gap-3 overflow-x-auto custom-scrollbar pb-3 snap-x snap-mandatory max-w-6xl mx-auto" style={{scrollbarWidth:'thin'}}>
        {routes.map((r: any, idx: number) => {
          const isSelected = selectedIndex === idx;
          const typeLabel = r.tags?.[0] || (r.type?.includes('餐') ? '餐饮美食' : r.type?.includes('酒店') ? '品质住宿' : '风景名胜');
          const isFood = typeLabel.includes('餐') || typeLabel.includes('食');
          const isHotel = typeLabel.includes('宿') || typeLabel.includes('酒店');

          return (
            <div
              key={`carousel-card-item-${r.name}-${idx}`}
              onClick={() => onSelect(idx)}
              className={`snap-center shrink-0 w-[210px] h-[130px] rounded-2xl overflow-hidden relative cursor-pointer transition-all duration-500 transform ${
                isSelected
                  ? 'ring-2 ring-orange-400 ring-offset-2 ring-offset-slate-950 scale-110 shadow-[0_0_30px_rgba(251,146,60,0.4)] z-20'
                  : 'opacity-70 hover:opacity-100 hover:scale-105 shadow-lg hover:shadow-xl'
              }`}
            >
              <RealPoiImage
                photoUrl={r.photos?.[0] || ''}
                poiName={r.name}
                type={typeLabel}
                className="absolute inset-0 w-full h-full"
              />
              
              {/* 精致渐变遮罩 */}
              <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent pointer-events-none" />
              {isSelected && (
                <div className="absolute inset-0 ring-1 ring-inset ring-white/20 rounded-2xl pointer-events-none" />
              )}
              
              {/* 序号标记 */}
              <div className={`absolute top-2 left-2 w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-black shadow-lg backdrop-blur-sm transition-all ${
                isSelected ? 'bg-gradient-to-br from-orange-400 to-rose-500 text-white scale-110' : 'bg-black/40 text-white/90'
              }`}>
                {idx + 1}
              </div>
              
              {/* 类型标签 */}
              <div className={`absolute top-2 right-2 px-2 py-0.5 rounded-full text-[9px] font-black backdrop-blur-md ${
                isFood ? 'bg-orange-500/80 text-white' : isHotel ? 'bg-indigo-500/80 text-white' : 'bg-emerald-500/80 text-white'
              }`}>
                {typeLabel.length > 4 ? typeLabel.slice(0, 4) : typeLabel}
              </div>
              
              {/* 名称与信息 */}
              <div className="absolute bottom-0 left-0 right-0 p-3 pointer-events-none">
                <h4 className="text-white font-bold text-[13px] leading-tight truncate drop-shadow-lg">{r.name}</h4>
                <div className="flex items-center gap-2 mt-1">
                  {r.rating && (
                    <span className="flex items-center gap-0.5 text-amber-300 text-[10px] font-bold">
                      <Star className="w-2.5 h-2.5 fill-amber-300" />
                      {r.rating}
                    </span>
                  )}
                  {r.cost && (
                    <span className="text-white/60 text-[10px] font-medium truncate">{typeof r.cost === 'string' ? r.cost.replace(/^预估花费：/, '') : ''}</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
