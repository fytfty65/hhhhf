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

export default function DeductionPanel({ latestLog, error, timedOut, elapsedSeconds, onRetry, onCancel, onBack }: DeductionPanelProps) {
  const hasError = Boolean(error || timedOut);
  const displayError = error || (timedOut ? "规划超时：长时间未收到服务响应，可能是网络波动或模型服务繁忙。" : null);
  const waited = Math.max(0, Math.round(elapsedSeconds ?? 0));
  const waitedText = waited >= 60 ? `${Math.floor(waited / 60)} 分 ${waited % 60} 秒` : `${waited} 秒`;

  // 三个阶段是应用自身真实存在的阶段（描述需求 → 生成方案 → 确认行程），
  // 不假装知道后端内部的细分步骤；当前处于哪一步由渲染位置决定。
  const stages = [
    { label: '描述需求', state: 'done' },
    { label: '生成方案', state: 'active' },
    { label: '确认行程', state: 'todo' },
  ] as const;

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
        {hasError ? '暂时无法生成方案' : '正在整理你的行程方案…'}
      </h3>
      
      {/* 阶段条：让用户知道现在卡在哪一步、前面已经完成了什么 */}
      {!hasError && (
        <div className="mt-4 flex items-center gap-1.5" data-testid="deduction-stages" aria-label="规划阶段">
          {stages.map((stage, index) => (
            <div key={stage.label} className="flex items-center gap-1.5">
              {index > 0 && <span className="text-[10px] text-slate-300">→</span>}
              <span
                className={`rounded-lg border px-2 py-1 text-[11px] font-bold ${
                  stage.state === 'active'
                    ? 'border-orange-200 bg-orange-50 text-orange-700'
                    : stage.state === 'done'
                      ? 'border-stone-200 bg-stone-100 text-stone-500'
                      : 'border-slate-100 bg-slate-50 text-slate-400'
                }`}
              >
                {stage.state === 'done' ? '✓ ' : stage.state === 'active' ? '● ' : ''}
                {stage.label}
                {stage.state === 'active' ? '（进行中）' : stage.state === 'done' ? '（已完成）' : '（待开始）'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 实时推演日志卡片 */}
      <div className={`mt-4 p-3.5 rounded-2xl text-xs font-mono max-w-sm w-full border shadow-md ${
        hasError ? 'bg-red-950/90 border-red-800 text-red-100' : 'bg-slate-900 border-slate-800 text-slate-200'
      }`}>
        <div className={`flex items-center gap-1.5 font-bold mb-1 ${hasError ? 'text-red-400' : 'text-orange-400'}`}>
          <span className={`w-2 h-2 rounded-full ${hasError ? 'bg-red-500' : 'bg-orange-500 animate-ping'}`}></span>
          <span>{hasError ? '需要处理:' : '当前进度:'}</span>
        </div>
        <p className={`text-[11px] leading-relaxed line-clamp-3 ${hasError ? 'text-red-200' : 'text-slate-300'}`}>
          {hasError ? displayError : (latestLog || "正在核对目的地、开放时间、交通和预算信息…")}
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
            重新规划
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
          已用时 <span data-testid="deduction-elapsed" className="font-bold text-slate-600">{waitedText}</span>
          ，通常需要 20–60 秒；完成后你可以继续修改任何一天的安排。
        </p>
      )}
      {!hasError && (
        <button
          type="button"
          onClick={onCancel}
          data-testid="deduction-cancel"
          className="mt-3 text-[11px] font-bold text-slate-500 underline decoration-slate-300 underline-offset-2 transition-colors hover:text-slate-700 cursor-pointer"
          title="回到诉求描述页；这次生成可能仍在后台完成，只是不再弹出来"
        >
          停止等待
        </button>
      )}
    </motion.div>
  );
}
