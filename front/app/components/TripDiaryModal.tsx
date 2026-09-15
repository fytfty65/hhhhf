'use client';
import { X, Download, BookOpen } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// 模块 10：AI 旅行日记展示弹窗（Markdown 文本 + 下载 .md）。
export default function TripDiaryModal({ open, onClose, markdown, city, onDownload }: {
  open: boolean;
  onClose: () => void;
  markdown: string;
  city: string;
  onDownload: () => void;
}) {
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
            <div className="p-5 overflow-y-auto custom-scrollbar">
              <pre className="whitespace-pre-wrap font-sans text-sm text-slate-700 leading-relaxed">{markdown}</pre>
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
