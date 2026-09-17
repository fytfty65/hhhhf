'use client';

/**
 * PoiImage — 智能 POI 图片组件（实景图轮询 → 静态地图 → 兜底卡片的 5 级兜底链）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 5)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import React, { useState } from 'react';
import { Camera, ExternalLink } from 'lucide-react';
import { getCleanPhotoUrl } from '../lib/lobbyUtils';
import type { PoiImageProps } from '../types';

// 👑 智能POI图片组件：5级兜底链保证100%视觉覆盖，永不空白
// 实景图轮询[0→1→2] → 静态坐标地图 → 精美地图预览卡片（带跳转）

export default function PoiImage({ photos = [], mapImage = '', amapUrl = '', name, type = '', className = '', index, onPhotoClick }: PoiImageProps) {
  // 👑 只使用真实实景照片，绝不用高德静态地图充数（静态地图≠实景照片，用户无法了解地点实况）
  const queue = React.useMemo(() => {
    const urls: string[] = [];
    (Array.isArray(photos) ? photos : []).forEach(p => {
      const u = getCleanPhotoUrl(p);
      if (u && !urls.includes(u)) urls.push(u);
    });
    // mapImage（高德静态坐标地图）不再加入图片队列——它只是一张带红点的地图截图，
    // 无法让用户看到地点的实际面貌。没有实景照片时直接展示兜底卡片。
    return urls;
  }, [photos]);

  const [currentIdx, setCurrentIdx] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [allFailed, setAllFailed] = useState(queue.length === 0);

  const currentUrl = !allFailed && currentIdx < queue.length ? queue[currentIdx] : '';

  const handleError = React.useCallback(() => {
    setLoaded(false);
    if (currentIdx + 1 < queue.length) {
      setCurrentIdx(currentIdx + 1);
    } else {
      setAllFailed(true);
    }
  }, [currentIdx, queue.length]);

  // 全部失败时：显示精美地点卡片（无实景照片时绝不用地图截图充数）
  if (allFailed || !currentUrl) {
    const typeLabel = type || '目的地';
    const typeColor = type?.includes('餐') || type?.includes('食') ? 'from-orange-400 to-rose-400'
      : type?.includes('酒店') || type?.includes('住宿') ? 'from-indigo-400 to-purple-400'
      : 'from-teal-400 to-emerald-400';
    return (
      <div
        className={`relative overflow-hidden cursor-pointer group ${className}`}
        onClick={onPhotoClick}
        title="点击搜索该地点实景照片"
      >
        <div className={`absolute inset-0 bg-gradient-to-br ${typeColor} transition-transform duration-300 group-hover:scale-105`} />
        <div className="absolute inset-0 opacity-[0.15]" style={{backgroundImage: 'radial-gradient(circle at 2px 2px, rgba(255,255,255,0.4) 1px, transparent 0)', backgroundSize: '20px 20px'}} />
        <div className="relative h-full w-full flex flex-col items-center justify-center gap-2 p-4">
          {typeof index === 'number' && (
            <div className="absolute top-2 left-2 w-6 h-6 rounded-full bg-white/25 backdrop-blur-sm flex items-center justify-center text-white text-[11px] font-black">{index + 1}</div>
          )}
          <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center group-hover:bg-white/30 transition-colors">
            <Camera className="w-6 h-6 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <p className="text-white font-bold text-sm leading-tight px-2 line-clamp-2 drop-shadow-md">{name}</p>
            <p className="text-white/80 text-[10px] font-bold mt-1 tracking-wide">{typeLabel} · 暂无实景照片</p>
          </div>
          {onPhotoClick && (
            <div className="mt-1 px-3 py-1 rounded-full bg-white/20 backdrop-blur-sm text-white text-[10px] font-bold group-hover:bg-white/35 transition-colors flex items-center gap-1">
              <ExternalLink className="w-3 h-3" /> 去搜实景照片
            </div>
          )}
          {!onPhotoClick && amapUrl && (
            <a
              href={amapUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="mt-1 px-3 py-1 rounded-full bg-white/25 backdrop-blur-sm text-white text-[10px] font-bold hover:bg-white/40 transition-colors flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" /> 在高德地图中查看
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 ${className}`}>
      {!loaded && (
        <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-300 dark:from-slate-700 dark:to-slate-800 animate-pulse" />
      )}
      <img
        src={currentUrl}
        alt={name}
        onLoad={() => setLoaded(true)}
        onError={handleError}
        className={`w-full h-full object-cover transition-opacity duration-500 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}
