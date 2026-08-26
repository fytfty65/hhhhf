'use client';

import React, { useState } from 'react';

interface RealPoiImageProps {
  photoUrl?: string;
  poiName: string;
  className?: string;
}

// 仅展示真实 POI 实景照片；无有效照片时展示明确占位，绝不使用与地点无关的虚假图片
export default function RealPoiImage({ photoUrl, poiName, className = '' }: RealPoiImageProps) {
  const [imgError, setImgError] = useState(false);

  // 统一升级为 https 并兼容协议相对 `//`，避免混合内容被浏览器拦截
  const normalizedUrl = (photoUrl && typeof photoUrl === 'string')
    ? (photoUrl.startsWith('//') ? 'https:' + photoUrl : photoUrl.startsWith('http://') ? 'https://' + photoUrl.slice(7) : photoUrl)
    : '';

  const isValidUrl = Boolean(normalizedUrl.startsWith('https://'));
  const showReal = isValidUrl && !imgError;

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 ${className}`}>
      {showReal ? (
        <img
          src={normalizedUrl}
          alt={poiName}
          onError={() => setImgError(true)}
          className="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
          loading="lazy"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className="flex flex-col items-center justify-center gap-1 w-full h-full">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-8 h-8 text-slate-300 dark:text-slate-600">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <circle cx="8.5" cy="10" r="1.5" />
            <path d="m21 15-5-5L5 21" />
          </svg>
          <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 text-center px-2">{poiName}·实景暂不可用</span>
        </div>
      )}
    </div>
  );
}