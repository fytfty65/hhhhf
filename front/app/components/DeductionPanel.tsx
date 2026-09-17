'use client';

/**
 * DeductionPanel — 多智能体推演进行中/失败面板（props 类型来自 ../types）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 3)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import { motion } from 'framer-motion';
import type { DeductionPanelProps } from '../types';
import { AlertCircle, RefreshCw, RotateCw, ArrowLeft } from 'lucide-react';

export default function DeductionPanel({ latestLog, error, timedOut, onRetry, onBack }: DeductionPanelProps) {
  const hasError = Boolean(error || timedOut);
  const displayError = error || (timedOut ? "推演超时：长时间未收到智能体响应，可能是网络波动或模型服务繁忙。" : null);

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-20 h-full text-center px-8">
      {/* 状态图标 */}
      <div className="relative w-24 h-24 mb-6">
        {hasError ? (
          <>
            <div className="absolute inset-0 flex items-center justify-center">
              <AlertCircle className="w-12 h-12 text-red-500" />
            </div>
            <motion.div animate={{ scale: [1, 1.15, 1] }} transition={{ duration: 1.5, repeat: Infinity }} className="absolute inset-0 border-2 border-red-400/60 rounded-full" />
          </>
        ) : (
          <>
            <motion.div animate={{ rotate: 360 }} transition={{ duration: 3, repeat: Infinity, ease: "linear" }} className="absolute inset-0 border-2 border-dashed border-orange-400 rounded-full" />
            <div className="absolute inset-0 flex items-center justify-center">
              <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
            </div>
          </>
        )}
      </div>

      <h3 className={`text-base font-black ${hasError ? 'text-red-600' : 'text-slate-800'}`}>
        {hasError ? '推演遇到问题' : '多智能体正在博弈调和众口诉求...'}
      </h3>
      
      {/* 实时推演日志卡片 */}
      <div className={`mt-4 p-3.5 rounded-2xl text-xs font-mono max-w-sm w-full border shadow-md ${
        hasError ? 'bg-red-950/90 border-red-800 text-red-100' : 'bg-slate-900 border-slate-800 text-slate-200'
      }`}>
        <div className={`flex items-center gap-1.5 font-bold mb-1 ${hasError ? 'text-red-400' : 'text-orange-400'}`}>
          <span className={`w-2 h-2 rounded-full ${hasError ? 'bg-red-500' : 'bg-orange-500 animate-ping'}`}></span>
          <span>{hasError ? '系统提示:' : '智能体实时推演中:'}</span>
        </div>
        <p className={`text-[11px] leading-relaxed line-clamp-3 ${hasError ? 'text-red-200' : 'text-slate-300'}`}>
          {hasError ? displayError : (latestLog || "全息雷达数据已捕获，多智能体正在计算拓扑并交织生成行程...")}
        </p>
      </div>

      {/* 错误/超时状态下的操作按钮 */}
      {hasError && (
        <div className="mt-5 flex gap-3">
          <button
            onClick={onRetry}
            className="px-5 py-2.5 bg-orange-500 hover:bg-orange-600 text-white text-sm font-bold rounded-xl shadow-lg hover:shadow-xl transition-all hover:scale-105 flex items-center gap-2 cursor-pointer"
          >
            <RotateCw className="w-4 h-4" />
            重新推演
          </button>
          <button
            onClick={onBack}
            className="px-5 py-2.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-bold rounded-xl shadow hover:shadow-md transition-all cursor-pointer flex items-center gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            返回修改
          </button>
        </div>
      )}

      {/* 正常推演时显示提示 */}
      {!hasError && (
        <p className="mt-3 text-[11px] text-slate-400">
          通常需要 20-60 秒，正在协调地理/风控/知识/调度多智能体共识...
        </p>
      )}
    </motion.div>
  );
}
