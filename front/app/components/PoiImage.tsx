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
import { clearUserPhoto, fileToDataUrl, readUserPhoto, saveUserPhoto } from '../lib/userPhotos';
import type { PoiImageProps } from '../types';

// 👑 智能POI图片组件：5级兜底链保证100%视觉覆盖，永不空白
// 实景图轮询[0→1→2] → 静态坐标地图 → 精美地图预览卡片（带跳转）

export default function PoiImage({ photos = [], mapImage = '', amapUrl = '', name, type = '', className = '', index, onPhotoClick, photoFallback }: PoiImageProps & { photoFallback?: { url?: string; caption?: string; kind?: string; attribution?: string } | null }) {
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
  // ⑥ 用户自己的实拍：真归属的图片来源。只存在这台设备（localStorage），不上传服务器。
  const [userPhoto, setUserPhoto] = useState(() => readUserPhoto(name));
  const [photoNotice, setPhotoNotice] = useState('');
  const photoInputRef = React.useRef<HTMLInputElement | null>(null);

  const handlePickPhoto = React.useCallback(async (file?: File) => {
    const dataUrl = await fileToDataUrl(file as File);
    if (!dataUrl) {
      setPhotoNotice('只支持图片文件');
      return;
    }
    const result = saveUserPhoto(name, dataUrl);
    if (result.ok !== true) {
      setPhotoNotice((result as { reason?: string }).reason || '保存失败，请重试');
      return;
    }
    setUserPhoto(dataUrl);
    setPhotoNotice('已存为「我的实拍」（仅本机保存）');
  }, [name]);

  const renderPicker = (label: string) => (
    <span className="inline-flex items-center gap-2">
      <input
        ref={photoInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        data-testid="poi-photo-input"
        onChange={(event) => void handlePickPhoto(event.target.files?.[0])}
      />
      <button
        type="button"
        data-testid="poi-photo-upload"
        onClick={(event) => {
          event.stopPropagation();
          photoInputRef.current?.click();
        }}
        className="rounded-full bg-white/25 px-2.5 py-1 text-[10px] font-bold text-white backdrop-blur-sm hover:bg-white/40"
      >
        {label}
      </button>
    </span>
  );

  const currentUrl = !allFailed && currentIdx < queue.length ? queue[currentIdx] : '';

  const handleError = React.useCallback(() => {
    setLoaded(false);
    if (currentIdx + 1 < queue.length) {
      setCurrentIdx(currentIdx + 1);
    } else {
      setAllFailed(true);
    }
  }, [currentIdx, queue.length]);

  // ⑥ 用户自己上传的实拍优先展示（这是"这个地点"最可信的图：你自己拍的）
  if (userPhoto) {
    return (
      <div className={`relative overflow-hidden group ${className}`}>
        <img src={userPhoto} alt={`${name}（我的实拍）`} className="absolute inset-0 h-full w-full object-cover" data-testid="poi-user-photo" />
        {typeof index === 'number' && (
          <div className="absolute top-2 left-2 w-6 h-6 rounded-full bg-black/45 backdrop-blur-sm flex items-center justify-center text-white text-[11px] font-black">{index + 1}</div>
        )}
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-4">
          <p className="text-white text-[11px] font-bold leading-tight line-clamp-1 drop-shadow">{name}</p>
          <div className="mt-0.5 flex items-center justify-between gap-2">
            <span className="text-white/85 text-[9px] font-bold" data-testid="poi-user-photo-credit">我的实拍 · 仅本机保存</span>
            <span className="flex items-center gap-2">
              {renderPicker('换一张')}
              <button
                type="button"
                data-testid="poi-user-photo-clear"
                onClick={(event) => {
                  event.stopPropagation();
                  clearUserPhoto(name);
                  setUserPhoto('');
                  setPhotoNotice('已删除我的实拍');
                }}
                className="rounded-full bg-white/25 px-2.5 py-1 text-[10px] font-bold text-white backdrop-blur-sm hover:bg-white/40"
              >
                删除
              </button>
            </span>
          </div>
          {photoNotice && <p className="mt-0.5 text-white/80 text-[9px]">{photoNotice}</p>}
        </div>
      </div>
    );
  }

  // 全部失败时：有**真实地理影像**兜底就显示它（并如实标注"位置示意，非实景照片"）；
  // 没有才显示地点卡片（无实景照片时绝不用别的景点照片/通用图库图冒充）
  if (allFailed || !currentUrl) {
    // 后端给的兜底影像优先（卫星影像/街道图）；没有就用 mapImage（高德静态图，带标记点）
    const resolvedFallback =
      (photoFallback && photoFallback.url
        ? photoFallback
        : mapImage
          ? { url: mapImage, caption: '位置示意：街道地图（非实景照片）', kind: 'street_map' }
          : null);
    const fallbackUrl = String(resolvedFallback?.url || '');
    if (fallbackUrl) {
      return (
        <div className={`relative overflow-hidden group ${className}`} onClick={onPhotoClick} title={String(resolvedFallback?.caption || '位置示意')}>
          <img
            src={fallbackUrl}
            alt={name}
            className="absolute inset-0 h-full w-full object-cover"
            loading="lazy"
            data-testid="poi-photo-fallback"
          />
          {/* 中心定位点：卫星影像本身没有标记，这里叠一个，明确"就是这个位置" */}
          <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
            <div className="h-3.5 w-3.5 rounded-full border-2 border-white bg-orange-500 shadow" />
          </div>
          {typeof index === 'number' && (
            <div className="absolute top-2 left-2 w-6 h-6 rounded-full bg-black/45 backdrop-blur-sm flex items-center justify-center text-white text-[11px] font-black">{index + 1}</div>
          )}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-4">
            <p className="text-white text-[11px] font-bold leading-tight line-clamp-1 drop-shadow">{name}</p>
            <p className="text-white/85 text-[9px] font-bold leading-tight">{String(resolvedFallback?.caption || '位置示意，非实景照片')}</p>
            {onPhotoClick && (
              <span className="mt-0.5 inline-flex items-center gap-1 text-[9px] font-bold text-white/90 underline underline-offset-2">
                <ExternalLink className="w-2.5 h-2.5" /> 去搜实景照片
              </span>
            )}
          </div>
        </div>
      );
    }
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
          {/* ⑥ 官方/网络都没有这个地点的照片时，让用户放一张自己拍的：真归属、真有用 */}
          <div className="mt-1" onClick={(event) => event.stopPropagation()}>
            {renderPicker('传我的实拍')}
          </div>
          {photoNotice && <p className="text-white/85 text-[9px] text-center">{photoNotice}</p>}
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
