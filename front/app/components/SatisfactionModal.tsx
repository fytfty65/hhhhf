'use client';
import React, { useState } from 'react';
import { Star, X } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';

export default function SatisfactionModal({ open, onClose, userId, tripId, banditArmId, onSubmitted }: { open: boolean; onClose: () => void; userId: string; tripId: string; banditArmId?: string; onSubmitted?: (score: number) => void }) {
  const [score, setScore] = useState(0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');

  if (!open) return null;

  const submit = async () => {
    if (score < 1) return;
    setSubmitting(true);
    setError('');
    try {
      await apiJson(`${API_BASE}/api/v1/satisfaction`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, trip_id: tripId, score, comment, bandit_arm_id: banditArmId || '' }),
      });
      void apiJson(`${API_BASE}/api/v1/planning/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, event_type: 'satisfaction', value: score, payload: { module: 'trip_review', has_comment: Boolean(comment.trim()) } }),
      }).catch(() => {});
      setDone(true);
      onSubmitted?.(score);
      setTimeout(() => {
        setDone(false);
        setScore(0);
        setComment('');
        onClose();
      }, 1200);
    } catch (e) {
      setError(e instanceof Error ? e.message : '评价提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[min(360px,calc(100vw-32px))] rounded-lg border border-slate-200 bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-base font-black text-slate-800">行程满意度评价</h4>
          <button onClick={onClose} className="inline-flex h-9 w-9 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer" title="关闭评价" aria-label="关闭评价">
            <X className="w-5 h-5" />
          </button>
        </div>

        {done ? (
          <p className="text-center text-emerald-600 font-bold py-6">感谢反馈，评价已提交 ✓</p>
        ) : (
          <>
            <p className="text-xs text-slate-500 mb-3">这次行程体验如何？（1-5 星）</p>
            <div className="flex justify-center gap-2 mb-4">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  onMouseEnter={() => setHover(n)}
                  onMouseLeave={() => setHover(0)}
                  onClick={() => setScore(n)}
                  className="cursor-pointer"
                >
                  <Star
                    className={`w-8 h-8 transition-colors ${n <= (hover || score) ? 'text-amber-400 fill-amber-400' : 'text-slate-300'}`}
                  />
                </button>
              ))}
            </div>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="说说感受（可选）"
              className="mb-3 w-full resize-none rounded-md border border-slate-200 px-3 py-2 text-xs focus:border-orange-400 focus:outline-none focus:ring-2 focus:ring-orange-100"
              rows={3}
            />
            {error && <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">{error}</p>}
            <button
              onClick={submit}
              disabled={submitting || score < 1}
              className="min-h-10 w-full rounded-md bg-orange-500 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? '提交中...' : '提交评价'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
