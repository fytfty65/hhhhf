'use client';

import React, { useState } from 'react';
import { MapPin } from 'lucide-react';

interface RealPoiImageProps {
  photoUrl?: string;
  poiName: string;
  className?: string;
  /** 地点类型，用于兜底卡片颜色分类：风景/餐饮/住宿等 */
  type?: string;
}

function normalizeUrl(u?: string): string {
  if (!u || typeof u !== 'string') return '';
  let s = u.trim();
  if (s.startsWith('//')) s = 'https:' + s;
  else if (s.startsWith('http://')) s = 'https://' + s.slice('http://'.length);
  return s.startsWith('https://') ? s : '';
}

/**
 * 智能实景图组件（与 page.tsx 中 PoiImage 视觉对齐版）：
 * - HTTPS 统一 + Referer 防盗链绕过
 * - 骨架屏 + 淡入动画
 * - 加载失败时展示精美渐变地图预览卡片，永不出现灰色"实景暂不可用"占位
 */
export default function RealPoiImage({ photoUrl, poiName, className = '', type }: RealPoiImageProps) {
  const [loaded, setLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  const url = normalizeUrl(photoUrl);
  const showReal = Boolean(url) && !imgError;

  if (!showReal) {
    const typeLabel = type || '目的地';
    const typeColor =
      type?.includes('餐') || type?.includes('食') ? 'from-orange-500 to-rose-500'
      : type?.includes('酒店') || type?.includes('住宿') ? 'from-indigo-500 to-purple-600'
      : 'from-emerald-500 to-teal-600';
    return (
      <div className={`relative overflow-hidden rounded-xl ${className}`}>
        <div className={`absolute inset-0 bg-gradient-to-br ${typeColor}`} />
        <div
          className="absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              'radial-gradient(circle at 2px 2px, rgba(255,255,255,0.3) 1px, transparent 0)',
            backgroundSize: '16px 16px',
          }}
        />
        <div className="relative h-full w-full flex flex-col items-center justify-center gap-2 p-4">
          <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center">
            <MapPin className="w-6 h-6 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <p className="text-white font-bold text-sm leading-tight px-2 line-clamp-2 drop-shadow-md">
              {poiName}
            </p>
            <p className="text-white/80 text-[10px] font-bold mt-1 tracking-wide">
              {typeLabel}·地图预览
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 rounded-xl ${className}`}>
      {!loaded && (
        <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-300 dark:from-slate-700 dark:to-slate-800 animate-pulse" />
      )}
      <img
        src={url}
        alt={poiName}
        onLoad={() => setLoaded(true)}
        onError={() => setImgError(true)}
        className={`w-full h-full object-cover transition-opacity duration-500 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
  );
}
