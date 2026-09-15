'use client';

import React, { useState, useRef, useCallback, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ImagePlus, X, Check, AlertCircle, Upload, ZoomIn, ZoomOut, RefreshCw } from 'lucide-react';
import { API_BASE } from '../lib/utils';

const CROP_SIZE = 288; // 裁剪视窗边长（px）
const OUTPUT_SIZE = 512; // 输出头像边长（px）
const MAX_SIZE = 5 * 1024 * 1024; // 5MB

interface AvatarUploaderProps {
  userId: string;
  onClose: () => void;
  onSuccess: (avatarUrl: string) => void;
}

export default function AvatarUploader({ userId, onClose, onSuccess }: AvatarUploaderProps) {
  const [stage, setStage] = useState<'pick' | 'crop' | 'uploading' | 'done'>('pick');
  const [error, setError] = useState('');
  const [src, setSrc] = useState('');
  const [origType, setOrigType] = useState('image/jpeg');
  const [imgW, setImgW] = useState(0);
  const [imgH, setImgH] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [progress, setProgress] = useState(0);
  const [doneUrl, setDoneUrl] = useState('');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; ox: number; oy: number } | null>(null);

  // 图片按比例缩放，使短边刚好铺满裁剪视窗（保证始终覆盖正方形）
  const baseScale = imgW && imgH ? CROP_SIZE / Math.min(imgW, imgH) : 1;
  const dispW = imgW * baseScale * zoom;
  const dispH = imgH * baseScale * zoom;

  // 图片载入后重置缩放与居中位置
  useEffect(() => {
    if (imgW && imgH) {
      setZoom(1);
      const dw = imgW * baseScale;
      const dh = imgH * baseScale;
      setOffset({ x: (CROP_SIZE - dw) / 2, y: (CROP_SIZE - dh) / 2 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imgW, imgH]);

  const validateFile = (file: File): string | null => {
    const okType = file.type === 'image/jpeg' || file.type === 'image/png';
    const name = file.name.toLowerCase();
    const okExt = name.endsWith('.jpg') || name.endsWith('.jpeg') || name.endsWith('.png');
    if (!okType && !okExt) return '仅支持 JPG / PNG 格式图片';
    if (file.size > MAX_SIZE) return '图片大小不能超过 5MB';
    return null;
  };

  const handleSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const err = validateFile(file);
    if (err) {
      setError(err);
      e.target.value = '';
      return;
    }
    setError('');
    setOrigType(file.type === 'image/png' ? 'image/png' : 'image/jpeg');
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      setImgW(img.naturalWidth);
      setImgH(img.naturalHeight);
      setSrc(url);
      setStage('crop');
    };
    img.onerror = () => {
      setError('图片读取失败，请更换后重试');
    };
    img.src = url;
  };

  const clampOffset = useCallback((x: number, y: number) => {
    const dw = imgW * baseScale * zoom;
    const dh = imgH * baseScale * zoom;
    return {
      x: Math.min(0, Math.max(CROP_SIZE - dw, x)),
      y: Math.min(0, Math.max(CROP_SIZE - dh, y)),
    };
  }, [imgW, imgH, baseScale, zoom]);

  const handlePointerDown = (e: React.PointerEvent) => {
    dragRef.current = { startX: e.clientX, startY: e.clientY, ox: offset.x, oy: offset.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const handlePointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setOffset(clampOffset(dragRef.current.ox + dx, dragRef.current.oy + dy));
  };
  const handlePointerUp = () => { dragRef.current = null; };

  const changeZoom = (delta: number) => {
    setZoom((z) => {
      const next = Math.min(3, Math.max(1, z + delta));
      const dw = imgW * baseScale * next;
      const dh = imgH * baseScale * next;
      // 缩放后保持中心点不变
      setOffset((prev) => clampOffset(
        prev.x + (CROP_SIZE - dw) / 2 - (CROP_SIZE - dispW) / 2,
        prev.y + (CROP_SIZE - dh) / 2 - (CROP_SIZE - dispH) / 2,
      ));
      return next;
    });
  };

  const cropAndUpload = () => {
    if (!imgRef.current) return;
    const canvas = document.createElement('canvas');
    canvas.width = OUTPUT_SIZE;
    canvas.height = OUTPUT_SIZE;
    const ctx = canvas.getContext('2d');
    if (!ctx) { setError('当前环境不支持图片处理'); return; }

    const scale = baseScale * zoom;
    const sx = -offset.x / scale;
    const sy = -offset.y / scale;
    const sSize = CROP_SIZE / scale;

    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
    ctx.drawImage(imgRef.current, sx, sy, sSize, sSize, 0, 0, OUTPUT_SIZE, OUTPUT_SIZE);

    const isPng = origType === 'image/png';
    const mime = isPng ? 'image/png' : 'image/jpeg';
    canvas.toBlob((blob) => {
      if (!blob) { setError('裁剪生成失败，请重试'); return; }
      uploadBlob(blob, mime);
    }, mime, 0.92);
  };

  const uploadBlob = async (blob: Blob, mime: string) => {
    setStage('uploading');
    setProgress(0);
    setError('');

    // 👑 前置诊断：先确认后端是否可达
    try {
      const pingRes = await fetch(`${API_BASE}/ping`, { method: 'GET', signal: AbortSignal.timeout(5000) });
      if (!pingRes.ok) {
        setError(`后端服务响应异常 (HTTP ${pingRes.status})，请检查服务状态后重试`);
        setStage('crop');
        return;
      }
    } catch (pingErr: any) {
      const msg = pingErr.name === 'TimeoutError' || pingErr.name === 'AbortError'
        ? '连接后端超时（5秒），请确认网关服务已启动'
        : '无法连接到后端服务，请确认网关已启动、API_BASE 配置正确且防火墙未拦截';
      setError(msg);
      setStage('crop');
      return;
    }

    const ext = mime === 'image/png' ? 'png' : 'jpg';
    const form = new FormData();
    form.append('user_id', userId);
    form.append('file', blob, `avatar.${ext}`);

    // 👑 使用 fetch 替代 XHR：fetch 的 CORS 处理更现代，与前面 /ping 一致
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    try {
      setProgress(30); // 开始上传，给个初始进度
      const res = await fetch(`${API_BASE}/api/user/avatar/upload`, {
        method: 'POST',
        body: form,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      setProgress(90);

      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const url = data.avatar_url || '';
        if (!url) {
          setError('服务器未返回头像地址，请重试');
          setStage('crop');
          return;
        }
        setProgress(100);
        setDoneUrl(url);
        setStage('done');
        onSuccess(url);
      } else {
        // 👑 详细的 HTTP 状态码错误提示
        const d = await res.json().catch(() => ({}));
        let msg = d.error || d.message || '';
        if (!msg) {
          switch (res.status) {
            case 400: msg = '请求参数错误，请检查图片格式与大小'; break;
            case 401: msg = '登录已过期，请重新登录后再试'; break;
            case 403: msg = '没有权限上传头像，请联系管理员'; break;
            case 404: msg = '上传接口不存在，请检查服务器地址'; break;
            case 413: msg = '图片文件过大，请压缩后重试'; break;
            case 415: msg = '图片格式不支持，仅支持 JPG/PNG'; break;
            case 429: msg = '请求过于频繁，请稍后再试'; break;
            case 500: msg = '服务器内部错误，请稍后重试'; break;
            case 502: msg = '网关错误，服务暂时不可用'; break;
            case 503: msg = '服务正在维护中，请稍后重试'; break;
            default: msg = `上传失败 (HTTP ${res.status})，请稍后重试`;
          }
        }
        setError(msg);
        setStage('crop');
      }
    } catch (err: any) {
      clearTimeout(timeoutId);
      console.error('[AvatarUpload] fetch 上传失败:', err);
      if (err.name === 'AbortError') {
        setError('上传超时（30秒），请检查网络后重试');
      } else {
        setError(`上传请求失败：${err.message || '网络异常'}。请确认后端服务已重启并重新尝试。`);
      }
      setStage('crop');
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="fixed inset-0 bg-stone-900/40 backdrop-blur-sm z-[130] flex items-center justify-center p-4"
      >
        <motion.div
          initial={{ scale: 0.94, y: 16 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.94, y: -16 }}
          transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          className="bg-[#fffdf8] rounded-[28px] w-full max-w-md p-7 shadow-2xl border border-amber-900/10 relative"
        >
          <button onClick={onClose} className="absolute top-5 right-5 p-1.5 rounded-full hover:bg-stone-100 text-stone-400 hover:text-stone-600 transition-colors cursor-pointer" title="关闭">
            <X className="w-5 h-5" />
          </button>

          <h3 className="font-serif text-lg font-bold text-stone-900 mb-1">更换头像</h3>
          <p className="text-xs text-stone-400 mb-6">
            {stage === 'crop' ? '拖动调整位置，缩放控制取景范围' : '支持 JPG / PNG，大小不超过 5MB'}
          </p>

          {stage === 'pick' && (
            <button
              onClick={() => fileInputRef.current?.click()}
              className="w-full h-40 rounded-2xl border-2 border-dashed border-amber-200 bg-amber-50/40 hover:bg-amber-50 flex flex-col items-center justify-center gap-3 transition-colors cursor-pointer"
            >
              <div className="w-12 h-12 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center"><ImagePlus className="w-6 h-6" /></div>
              <span className="text-sm font-bold text-amber-700">选择本地图片</span>
              <span className="text-[11px] text-stone-400">点击从相册或文件夹中挑选</span>
            </button>
          )}

          {stage === 'crop' && src && (
            <>
              <div className="flex justify-center">
                <div
                  className="relative overflow-hidden rounded-2xl border border-amber-900/10 bg-stone-100 touch-none select-none cursor-grab active:cursor-grabbing"
                  style={{ width: CROP_SIZE, height: CROP_SIZE }}
                  onPointerDown={handlePointerDown}
                  onPointerMove={handlePointerMove}
                  onPointerUp={handlePointerUp}
                  onPointerLeave={handlePointerUp}
                >
                  <img
                    src={src}
                    alt="待裁剪"
                    draggable={false}
                    className="absolute max-w-none"
                    style={{ width: dispW, height: dispH, left: offset.x, top: offset.y }}
                  />
                  <div className="absolute inset-0 pointer-events-none rounded-2xl" style={{ boxShadow: 'inset 0 0 0 999px rgba(0,0,0,0.25)' }}>
                    <div className="absolute inset-4 border border-white/70 rounded-xl" />
                  </div>
                </div>
              </div>
              <div className="flex items-center justify-center gap-3 mt-5">
                <button onClick={() => changeZoom(-0.25)} className="p-2 bg-stone-100 rounded-lg text-stone-500 hover:bg-stone-200 transition-colors cursor-pointer" title="缩小"><ZoomOut className="w-4 h-4" /></button>
                <input
                  type="range" min={1} max={3} step={0.01} value={zoom}
                  onChange={(e) => changeZoom(Number(e.target.value) - zoom)}
                  className="w-40 accent-amber-600"
                />
                <button onClick={() => changeZoom(0.25)} className="p-2 bg-stone-100 rounded-lg text-stone-500 hover:bg-stone-200 transition-colors cursor-pointer" title="放大"><ZoomIn className="w-4 h-4" /></button>
              </div>
              <div className="flex gap-2 mt-6">
                <button onClick={() => fileInputRef.current?.click()} className="flex-1 py-2.5 rounded-xl border border-stone-200 text-stone-600 text-xs font-bold hover:bg-stone-50 transition-colors cursor-pointer">重新选择</button>
                <button onClick={cropAndUpload} className="flex-1 py-2.5 rounded-xl bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold transition-colors cursor-pointer flex items-center justify-center gap-1.5">
                  <Upload className="w-4 h-4" /> 裁剪并上传
                </button>
              </div>
            </>
          )}

          {stage === 'uploading' && (
            <div className="py-8 flex flex-col items-center gap-4">
              <div className="relative w-16 h-16">
                <div className="absolute inset-0 rounded-full border-4 border-amber-100" />
                <div className="absolute inset-0 rounded-full border-4 border-amber-600 border-t-transparent animate-spin" />
                <span className="absolute inset-0 flex items-center justify-center text-xs font-black text-amber-700">{progress}%</span>
              </div>
              <p className="text-sm font-bold text-stone-600">正在上传头像...</p>
              <div className="w-full h-1.5 bg-stone-100 rounded-full overflow-hidden">
                <div className="h-full bg-amber-500 rounded-full transition-all duration-200" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}

          {stage === 'done' && (
            <div className="py-8 flex flex-col items-center gap-4">
              <div className="w-16 h-16 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center"><Check className="w-8 h-8" /></div>
              <p className="text-sm font-bold text-stone-700">头像上传成功</p>
              {doneUrl && (
                <img src={`${API_BASE}${doneUrl}`} alt="新头像" className="w-20 h-20 rounded-full border-2 border-amber-200 object-cover" />
              )}
              <button onClick={onClose} className="px-6 py-2.5 rounded-xl bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold transition-colors cursor-pointer">完成</button>
            </div>
          )}

          {error && (
            <div className="mt-4 space-y-2">
              <div className="flex items-start gap-2 p-3 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-600 font-bold">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span className="leading-relaxed">{error}</span>
              </div>
              {stage === 'crop' && (
                <div className="flex gap-2">
                  <button onClick={cropAndUpload} className="flex-1 py-2 rounded-xl bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold transition-colors cursor-pointer flex items-center justify-center gap-1.5">
                    <RefreshCw className="w-3.5 h-3.5" /> 重新上传
                  </button>
                  <button
                    onClick={async () => {
                      setError('正在诊断连接...');
                      try {
                        const r = await fetch(`${API_BASE}/ping`, { method: 'GET', signal: AbortSignal.timeout(5000) });
                        if (r.ok) {
                          const d = await r.json().catch(() => ({}));
                          setError(`✅ 后端连接正常！(${d.service || 'OmniRoute'}) 请重新点击「裁剪并上传」尝试。`);
                        } else {
                          setError(`❌ 后端响应异常 (HTTP ${r.status})，服务可能未完全启动。`);
                        }
                      } catch (e: any) {
                        setError('无法连接到网关，请确认服务已启动、API_BASE 配置正确且防火墙未拦截');
                      }
                    }}
                    className="flex-1 py-2 rounded-xl border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-bold transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw className="w-3.5 h-3.5" /> 诊断连接
                  </button>
                </div>
              )}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png"
            className="hidden"
            onChange={handleSelect}
          />
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
