'use client';
import { useEffect, useMemo, useState } from 'react';
import { X, Download, BookOpen } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// 模块 10：AI 旅行日记展示弹窗（Markdown 文本 + 下载 .md）。
// 复盘默认先看当天那一节；「全部日记」才展示整趟行程，下载始终是完整版本。
export default function TripDiaryModal({ open, onClose, markdown, city, onDownload, todayDay }: {
  open: boolean;
  onClose: () => void;
  markdown: string;
  city: string;
  onDownload: () => void;
  todayDay?: number | null;
}) {
  const [scope, setScope] = useState<'today' | 'all'>('today');
  useEffect(() => { if (open) setScope('today'); }, [open]);

  const todaySection = useMemo(() => {
    if (!todayDay) return null;
    const sections = markdown.split(/\n(?=## Day \d+)/);
    return sections.find((section) => section.startsWith(`## Day ${todayDay}`)) || null;
  }, [markdown, todayDay]);

  const showToday = Boolean(todayDay) && scope === 'today';
  const body = showToday ? (todaySection || '') : markdown;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="trip-diary-modal-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="absolute inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-8"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="bg-white rounded-2xl shadow-2xl max-w-lg w-full flex flex-col max-h-[85vh]"
          >
            <div className="flex items-center justify-between p-4 border-b border-slate-100">
              <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm">
                <BookOpen className="w-4 h-4 text-amber-500" /> AI 旅行日记{city ? ` · ${city}` : ''}
              </h3>
              <button onClick={onClose} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            {todayDay ? (
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2">
                <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5" role="group" aria-label="日记范围" data-testid="diary-scope">
                  <button type="button" data-testid="diary-scope-today" aria-pressed={scope === 'today'} onClick={() => setScope('today')} className={`min-h-8 rounded-md px-3 text-xs font-semibold transition-colors ${scope === 'today' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>今天 · 第 {todayDay} 天</button>
                  <button type="button" data-testid="diary-scope-all" aria-pressed={scope === 'all'} onClick={() => setScope('all')} className={`min-h-8 rounded-md px-3 text-xs font-semibold transition-colors ${scope === 'all' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>全部日记</button>
                </div>
                <span className="text-[11px] text-slate-400">下载始终是完整版本</span>
              </div>
            ) : null}
            <div className="p-5 overflow-y-auto custom-scrollbar">
              {showToday && !todaySection ? (
                <p data-testid="diary-today-empty" className="text-sm text-slate-500">今天（第 {todayDay} 天）还没有安排地点，切到「全部日记」查看整趟行程。</p>
              ) : (
                <pre data-testid="diary-body" className="whitespace-pre-wrap font-sans text-sm text-slate-700 leading-relaxed">{body}</pre>
              )}
            </div>
            <div className="p-4 border-t border-slate-100 flex justify-end">
              <button
                data-testid="download-diary"
                onClick={onDownload}
                className="px-4 py-2 bg-amber-500 text-white rounded-xl font-bold text-xs hover:bg-amber-600 transition-colors flex items-center gap-1.5 shadow-sm cursor-pointer"
              >
                <Download className="w-3.5 h-3.5" /> 下载 .md 日记
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
