'use client';
import React, { useState } from 'react';
import { Camera, ExternalLink } from 'lucide-react';
import type { PoiImageProps } from '../types';

import {
  API_BASE, assetUrl, apiFetch, apiJson, apiRequest, getAuthToken, ApiRequestError,
  setAccessToken, clearAccessToken, refreshAccessToken, fetchWsTicket,
} from './apiClient.ts';
import { resolveWsBase } from './gatewayBase.ts';

// API 基址、认证、超时、错误码、重试与请求追踪都收敛在 lib/apiClient。
// 这里只保留 WebSocket 基址（由同一个 API 基址推导，https → wss）。
export {
  assetUrl, apiFetch, apiJson, apiRequest, getAuthToken, ApiRequestError,
  setAccessToken, clearAccessToken, refreshAccessToken, fetchWsTicket,
};
export const WS_BASE = resolveWsBase(API_BASE, process.env.NEXT_PUBLIC_WS_BASE);

// 精简行政区名称
export function shortenRegionName(full: string) {
  return full.replace(/(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|省|市)$/, '');
}

export const predictBudget = async (city: string, days: number) => {
    try {
        const data = await apiRequest<{ estimated_cost?: number }>('/api/v1/budget/predict', {
            query: { city, days },
        });
        return data.estimated_cost;
    } catch {
        return null;
    }
};

// 系统图标
export function OmniLogo({ className = "w-8 h-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
      <defs>
        <linearGradient id="warmGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FF8E53" /> 
          <stop offset="100%" stopColor="#FF6B6B" />
        </linearGradient>
        <linearGradient id="coolGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#4FACFE" /> 
          <stop offset="100%" stopColor="#00F2FE" />
        </linearGradient>
      </defs>
      <path d="M22 8C14.268 8 8 14.268 8 22C8 33.5 22 44 22 44C22 44 36 33.5 36 22C36 14.268 29.732 8 22 8Z" fill="url(#warmGrad)" fillOpacity="0.9" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="22" cy="22" r="5" fill="#FFFFFF" />
      <path d="M30 14C24.477 14 20 18.477 20 24C20 32 30 40 30 40C30 40 40 32 40 24C40 18.477 35.523 14 30 14Z" fill="url(#coolGrad)" fillOpacity="0.95" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="30" cy="24" r="3" fill="#FFFFFF" />
    </svg>
  );
}

// JSON 提取
export function tryExtractJson(text: string) {
  if (!text) return null;
  let jsonCandidate = "";

  if (text.includes("[FINAL_JSON]")) {
    jsonCandidate = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonCandidate = text.split("```json")[1];
  } else {
    const firstBrace = text.search(/\{\s*"status"|\{\s*"route"/);
    if (firstBrace !== -1) {
      jsonCandidate = text.substring(firstBrace);
    }
  }

  if (!jsonCandidate) return null;
  jsonCandidate = jsonCandidate.replace(/```json/g, "").replace(/```/g, "").trim();

  const lastBraceIdx = jsonCandidate.lastIndexOf("}");
  if (lastBraceIdx === -1) return null;

  const validSubstring = jsonCandidate.substring(0, lastBraceIdx + 1);
  try {
    return JSON.parse(validSubstring);
  } catch (e) {
    return null;
  }
}

// 坐标归一化
export function normalizeLnglat(v: any): [number, number] | null {
  if (Array.isArray(v) && v.length >= 2) {
    const lng = Number(v[0]);
    const lat = Number(v[1]);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    return null;
  }
  if (typeof v === 'string') {
    const parts = v.split(',');
    if (parts.length >= 2) {
      const lng = Number(parts[0]);
      const lat = Number(parts[1]);
      if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    }
    return null;
  }
  if (v && typeof v === 'object') {
    const lng = Number((v as any).lng ?? (v as any).lon ?? (v as any).longitude);
    const lat = Number((v as any).lat ?? (v as any).latitude);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
  }
  return null;
}

// 流式提取路线
export function extractStreamingRoutes(text: string): any[] {
  if (!text) return [];
  let jsonPart = text;
  if (text.includes("[FINAL_JSON]")) {
    jsonPart = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonPart = text.split("```json")[1];
  }

  const objectMatches = jsonPart.match(/\{\s*"day"\s*:\s*\d+[\s\S]*?\}/g);
  if (!objectMatches) return [];

  const parsedItems: any[] = [];
  for (const matchStr of objectMatches) {
    try {
      const item = JSON.parse(matchStr);
      if (item && (item.location || item.name)) {
        parsedItems.push({
          day: item.day || 1,
          name: item.location || item.name,
          lnglat: normalizeLnglat(item.lnglat),
          coordinate_status: normalizeLnglat(item.lnglat) ? 'verified' : 'missing',
          color: item.tags?.includes("寻味") || item.type === "food" ? "#f97316" : "#3b82f6",
          desc: item.desc || item.action || "",
          time: item.time || "",
          time_reason: item.time_reason || "",
          transport: item.transport || "",
          tags: item.tags || [],
          cost: item.cost_estimate || item.cost || "暂无供应商数据",
          photos: item.photos || [],
          trust_reason: item.trust_reason || "核心地标推荐",
          amap_url: item.amap_url || "",
          hotel_candidates: item.hotel_candidates || [],
          split_info: item.split_info || "",
          merge_point: Boolean(item.merge_point),
          is_hotel: Boolean(item.is_hotel || item.tags?.includes("住宿"))
        });
      }
    } catch (e) {}
  }
  return parsedItems;
}

// 图片 URL 清理
export function getCleanPhotoUrl(photoUrl?: string) {
  if (!photoUrl || typeof photoUrl !== 'string') return '';
  let u = photoUrl.trim();
  if (u.startsWith('//')) u = 'https:' + u;
  else if (u.startsWith('http://')) u = 'https://' + u.slice('http://'.length);
  return u.startsWith('https://') ? u : '';
}

// POI 图片组件
export function PoiImage({ photos = [], amapUrl = '', name, type = '', className = '', index, onPhotoClick }: PoiImageProps) {
  const queue = React.useMemo(() => {
    const urls: string[] = [];
    (Array.isArray(photos) ? photos : []).forEach(p => {
      const u = getCleanPhotoUrl(p);
      if (u && !urls.includes(u)) urls.push(u);
    });
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

  if (allFailed || !currentUrl) {
    const typeLabel = type || '目的地';
    const typeColor = type?.includes('餐') || type?.includes('食') ? 'from-orange-400 to-rose-400'
      : type?.includes('酒店') || type?.includes('住宿') ? 'from-orange-300 to-orange-500'
      : 'from-stone-300 to-stone-400';
    return (
      <div
        className={`relative overflow-hidden cursor-pointer group ${className}`}
        role="button"
        tabIndex={0}
        aria-label={`搜索 ${name} 的实景照片`}
        onClick={onPhotoClick}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onPhotoClick?.();
          }
        }}
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
