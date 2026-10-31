'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { X, Camera, PieChart } from 'lucide-react';
import { apiJson } from '../lib/apiClient';
import { formatCents, majorToCents, MAX_MONEY_CENTS } from '../lib/money';

const CATEGORY_COLORS: Record<string, string> = {
  餐饮: '#f97316',
  住宿: '#6366f1',
  交通: '#0ea5e9',
  门票: '#22c55e',
  购物: '#eab308',
  其他: '#94a3b8',
};

const CATEGORIES = ['餐饮', '住宿', '交通', '门票', '购物', '其他'];

type Summary = {
  total_budget_cents: number;
  spent_cents: number;
  remaining_cents: number;
  ratio: number;
  level: string;
  currency: string;
  precision: number;
  by_category_cents: Record<string, number>;
  category_pct: Record<string, number>;
  expense_count: number;
};

type Expense = {
  id: string;
  category: string;
  amount_cents: number;
  currency: string;
  note: string;
  created_at: string;
};

/** 按本机日期判断是否“今天”。解析失败的记录不计入今天，避免凑数。 */
function isSameLocalDay(value: string, reference: Date): boolean {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  return date.getFullYear() === reference.getFullYear() && date.getMonth() === reference.getMonth() && date.getDate() === reference.getDate();
}

export default function ExpenseReportModal({ open, onClose, userId, tripId }: { open: boolean; onClose: () => void; userId: string; tripId: string }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [ocrText, setOcrText] = useState('');
  const [amount, setAmount] = useState('');
  const [category, setCategory] = useState('餐饮');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  // 复盘默认先看今天：整趟数据仍在，只是列表与合计先按当天口径展示。
  const [scope, setScope] = useState<'today' | 'all'>('today');

  const reload = useCallback(async () => {
    if (!tripId) return;
    try {
      const j = await apiJson<{ summary?: Summary; expenses?: Expense[] }>(`/api/v1/budget/review?trip_id=${encodeURIComponent(tripId)}`);
      setSummary(j.summary || null);
      setExpenses(Array.isArray(j.expenses) ? j.expenses : []);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '消费数据暂时不可用');
    }
  }, [tripId]);

  useEffect(() => {
    if (open) {
      reload();
      setScope('today');
    }
  }, [open, reload]);

  const pieGradient = useMemo(() => {
    if (!summary || Object.keys(summary.category_pct).length === 0) return 'conic-gradient(#e2e8f0 0% 100%)';
    let acc = 0;
    const stops = Object.entries(summary.category_pct).map(([k, v]) => {
      const start = acc;
      acc += v;
      const color = CATEGORY_COLORS[k] || '#94a3b8';
      return `${color} ${start}% ${acc}%`;
    });
    return `conic-gradient(${stops.join(', ')})`;
  }, [summary]);

  const todayExpenses = useMemo(() => {
    const now = new Date();
    return expenses.filter((expense) => isSameLocalDay(expense.created_at, now));
  }, [expenses]);
  const visibleExpenses = scope === 'today' ? todayExpenses : expenses;
  const todayCents = todayExpenses.reduce((sum, expense) => sum + (Number.isFinite(expense.amount_cents) ? expense.amount_cents : 0), 0);
  const displayCurrency = summary?.currency || 'CNY';
  const displayPrecision = summary?.precision ?? 2;

  if (!open) return null;

  const submitOcr = async () => {
    if (!ocrText.trim()) return;
    setBusy(true);
    setMessage('');
    try {
      const j = await apiJson<{ message?: string; error?: string; summary?: Summary }>('/api/v1/expense/ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, trip_id: tripId, ocr_text: ocrText }),
      });
      setMessage(j.message || j.error || '已记账');
      if (j.summary) {
        setSummary(j.summary);
        setOcrText('');
        reload();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'OCR 记账失败');
    } finally {
      setBusy(false);
    }
  };

  const submitManual = async () => {
    // 输入是「元」，只把整数分发给后端。
    const cents = majorToCents(amount);
    if (cents === null || cents <= 0 || cents > MAX_MONEY_CENTS) {
      setMessage('请输入有效的金额');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      const j = await apiJson<{ message?: string; error?: string }>('/api/v1/expense', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, trip_id: tripId, category, amount_cents: cents, currency: 'CNY' }),
      });
      setMessage(j.message || j.error || '已记账');
      setAmount('');
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '消费记录失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <button type="button" aria-label="关闭弹窗" onClick={onClose} className="absolute inset-0 cursor-default" />
      <div className="relative z-10 bg-white rounded-2xl shadow-2xl w-[420px] max-h-[86vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <h4 className="text-base font-black text-slate-800 flex items-center gap-1.5">
            <PieChart className="w-4 h-4 text-orange-700" /> 花费报告
          </h4>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 cursor-pointer">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5" role="group" aria-label="复盘范围" data-testid="expense-scope">
            <button type="button" data-testid="expense-scope-today" aria-pressed={scope === 'today'} onClick={() => setScope('today')} className={`min-h-8 rounded-md px-3 text-xs font-semibold transition-colors ${scope === 'today' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>今天</button>
            <button type="button" data-testid="expense-scope-all" aria-pressed={scope === 'all'} onClick={() => setScope('all')} className={`min-h-8 rounded-md px-3 text-xs font-semibold transition-colors ${scope === 'all' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>全部</button>
          </div>
          <span className="text-[11px] text-slate-400">预算对比始终按整趟行程</span>
        </div>
        {scope === 'today' && (todayExpenses.length > 0
          ? <p className="mb-3 text-xs font-bold text-slate-700" data-testid="expense-today-summary">今天已记账 {todayExpenses.length} 笔 · 合计 ¥{formatCents(todayCents, displayPrecision)} {displayCurrency}</p>
          : <p className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500" data-testid="expense-today-empty">今天还没有记账（按本机日期统计；切到「全部」查看整趟行程）</p>)}

        {error && <div className="mb-3 flex items-center justify-between gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700"><span>{error}</span><button type="button" onClick={() => void reload()} className="shrink-0 underline">重试</button></div>}

        {summary && (
          <>
            {/* 饼图 + 对比预算 */}
            <div className="flex items-center gap-4 mb-4">
              <div className="relative w-28 h-28 shrink-0 rounded-full" style={{ background: pieGradient }}>
                <div className="absolute inset-2 rounded-full bg-white flex flex-col items-center justify-center">
                  <span className="text-[10px] text-slate-400">已用</span>
                  <span className="text-xs font-black text-slate-700">{summary.ratio > 0 ? `${(summary.ratio * 100).toFixed(0)}%` : '0%'}</span>
                </div>
              </div>
              <div className="flex-1 space-y-2">
                <div className="text-xs text-slate-500">
                  本次行程已消费 <span className="font-black text-slate-800">{formatCents(summary.spent_cents, summary.precision)}</span> / 预算{' '}
                  <span className="font-black text-slate-800">{formatCents(summary.total_budget_cents, summary.precision)}</span> {summary.currency}
                </div>
                <p className={`text-xs font-bold ${summary.level === 'over' ? 'text-rose-600' : summary.level === 'warning' ? 'text-amber-600' : 'text-emerald-600'}`}>
                  {summary.level === 'over' ? '已超预算' : summary.level === 'warning' ? '接近预算上限' : '预算控制良好'} · 剩余 {formatCents(summary.remaining_cents, summary.precision)}
                </p>
                <div className="space-y-1">
                  {Object.entries(summary.category_pct).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-1.5 text-[11px]">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: CATEGORY_COLORS[k] || '#94a3b8' }} />
                      <span className="text-slate-600">{k}</span>
                      <span className="ml-auto text-slate-400">{formatCents(summary.by_category_cents[k] ?? 0, summary.precision)} · {v.toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </>
        )}

        {message && <p className="mb-3 text-xs font-medium text-orange-700">{message}</p>}

        {/* 拍照记账（OCR） */}
        <div className="mb-4 p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="text-xs font-bold text-slate-600 flex items-center gap-1.5 mb-2">
            <Camera className="w-3.5 h-3.5" /> 拍照记账（粘贴识别文本）
          </p>
          <textarea
            value={ocrText}
            onChange={(e) => setOcrText(e.target.value)}
            placeholder={'例如：川西坝子火锅\n合计：128.50元'}
            className="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:border-orange-500 mb-2 resize-none"
            rows={3}
          />
          <button onClick={submitOcr} disabled={busy || !ocrText.trim()} className="w-full py-2 bg-orange-700 hover:bg-orange-800 disabled:opacity-50 text-white rounded-lg text-xs font-bold cursor-pointer">
            识别金额并记账
          </button>
        </div>

        {/* 手动记一笔 */}
        <div className="mb-4 p-3 rounded-xl bg-slate-50 border border-slate-100">
          <p className="text-xs font-bold text-slate-600 mb-2">手动记一笔</p>
          <div className="flex gap-2">
            <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="金额" className="flex-1 px-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none" />
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="px-2 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none bg-white">
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <button onClick={submitManual} disabled={busy || !amount} className="px-3 py-2 bg-slate-800 hover:bg-slate-900 disabled:opacity-50 text-white rounded-lg text-xs font-bold cursor-pointer">
              记录
            </button>
          </div>
        </div>

        {/* 消费明细 */}
        <div>
          <p className="text-xs font-bold text-slate-600 mb-2">消费明细{scope === 'today' ? ' · 今天' : ' · 整趟行程'}</p>
          {visibleExpenses.length === 0 ? (
            <p className="text-xs text-slate-400 py-3 text-center">{scope === 'today' ? '今天还没有消费记录' : '暂无消费记录'}</p>
          ) : (
            <ul className="space-y-1.5">
              {visibleExpenses.map((e) => (
                <li key={e.id} data-testid="expense-item" className="flex items-center justify-between text-xs py-1.5 px-2 rounded-lg bg-slate-50">
                  <span className="font-bold text-slate-600">{e.category}</span>
                  <span className="text-slate-400 truncate mx-2 flex-1">{e.note}</span>
                  <span className="font-black text-slate-800">{formatCents(e.amount_cents)} {e.currency}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
