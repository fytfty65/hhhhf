'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiJson } from '../lib/apiClient';
import { formatCents, majorToCents, MAX_MONEY_CENTS } from '../lib/money';

interface Summary {
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
}

const CATEGORIES = ['餐饮', '住宿', '交通', '门票', '购物', '其他'];

export default function BudgetPanel({ userId, tripId }: { userId: string; tripId: string }) {
  const [budget, setBudget] = useState('');
  const [currency, setCurrency] = useState('CNY');
  const [amount, setAmount] = useState('');
  const [category, setCategory] = useState('餐饮');
  const [expCurrency, setExpCurrency] = useState('CNY');
  const [note, setNote] = useState('');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    if (!tripId) return;
    setLoading(true);
    setError('');
    try {
      const j = await apiJson<{ summary?: Summary }>(`/api/v1/budget/summary?trip_id=${encodeURIComponent(tripId)}`);
      setSummary(j.summary || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '预算数据暂时不可用');
    } finally {
      setLoading(false);
    }
  }, [tripId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const setBudgetPlan = async () => {
    // 用户输入的是「元」，请求体只发整数分；精度由后端 currency 决定。
    const cents = majorToCents(budget);
    if (!tripId || cents === null || cents <= 0 || cents > MAX_MONEY_CENTS) { setMessage('请输入大于 0 的预算金额'); return; }
    setBusy(true); setError('');
    try {
      const j = await apiJson<{ message?: string }>(`/api/v1/budget`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId, trip_id: tripId, total_budget_cents: cents, currency }) });
      setMessage(j.message || '预算已保存');
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : '预算保存失败'); }
    finally { setBusy(false); }
  };

  const addExpense = async () => {
    const cents = majorToCents(amount);
    if (!tripId || cents === null || cents <= 0 || cents > MAX_MONEY_CENTS) { setMessage('请输入大于 0 的消费金额'); return; }
    setBusy(true); setError('');
    try {
      const j = await apiJson<{ message?: string; summary?: Summary }>(`/api/v1/expense`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId, trip_id: tripId, category, amount_cents: cents, currency: expCurrency, note }) });
      setMessage(j.summary?.level === 'over' ? '已超出预算，请注意控制消费节奏' : (j.message || '消费已记录'));
      setAmount(''); setNote('');
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : '消费记录失败'); }
    finally { setBusy(false); }
  };

  const levelMeta: Record<string, { label: string; cls: string }> = {
    safe: { label: '预算充裕', cls: 'text-emerald-600' },
    warning: { label: '接近上限', cls: 'text-amber-600' },
    over: { label: '已超预算', cls: 'text-rose-600' },
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-lg font-semibold text-slate-800">智能预算管家</h3>
      {!tripId && <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700">加入协作房间后即可记录这次行程的预算。</p>}
      {error && <div className="mb-3 flex items-center justify-between gap-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700"><span>{error}</span><button type="button" onClick={() => void reload()} className="shrink-0 underline">重试</button></div>}

      {/* 预算设置 */}
      <div className="mb-4 grid grid-cols-2 gap-2">
        <input value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="总预算金额" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <select value={currency} onChange={(e) => setCurrency(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
          {['CNY', 'USD', 'EUR', 'JPY', 'HKD', 'GBP', 'SGD', 'THB'].map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button onClick={() => void setBudgetPlan()} disabled={busy || !tripId} className="col-span-2 rounded-lg bg-orange-700 py-2 text-sm text-white hover:bg-orange-800 disabled:cursor-not-allowed disabled:opacity-50">{busy ? '处理中…' : '设置预算'}</button>
      </div>

      {/* 消费记录 */}
      <div className="mb-4 grid grid-cols-2 gap-2">
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
          {CATEGORIES.map((c) => (<option key={c} value={c}>{c}</option>))}
        </select>
        <select value={expCurrency} onChange={(e) => setExpCurrency(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
          {['CNY', 'USD', 'EUR', 'JPY', 'HKD', 'GBP', 'SGD', 'THB'].map((c) => (<option key={c} value={c}>{c}</option>))}
        </select>
        <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="消费金额" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <button onClick={() => void addExpense()} disabled={busy || !tripId} className="col-span-2 rounded-lg bg-slate-800 py-2 text-sm text-white hover:bg-slate-900 disabled:cursor-not-allowed disabled:opacity-50">记一笔</button>
      </div>

      {message && <p className="mb-3 text-xs font-medium text-orange-700">{message}</p>}

      {/* 汇总 */}
      {loading && <p className="mb-3 text-xs font-bold text-slate-400">正在同步预算…</p>}
      {summary && (
        <div className="rounded-xl bg-slate-50 p-4">
          <div className="flex items-end justify-between">
            <div>
              <p className="text-xs text-slate-500">已消费 / 总预算（{summary.currency}）</p>
              <p className="text-2xl font-bold text-slate-800">
                {formatCents(summary.spent_cents, summary.precision)} / {formatCents(summary.total_budget_cents, summary.precision)}
              </p>
            </div>
            <p className={`text-sm font-semibold ${levelMeta[summary.level]?.cls || ''}`}>
              {levelMeta[summary.level]?.label} · 剩余 {formatCents(summary.remaining_cents, summary.precision)}
            </p>
          </div>

          {/* 预算进度条 */}
          <div className="mt-3 h-3 w-full overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full transition-all ${summary.level === 'over' ? 'bg-rose-500' : summary.level === 'warning' ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${Math.min(summary.ratio * 100, 100)}%` }}
            />
          </div>

          {/* 分类占比可视化 */}
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-slate-500">消费分类复盘</p>
            {Object.entries(summary.category_pct).map(([k, v]) => (
              <div key={k} className="mb-1.5">
                <div className="flex justify-between text-xs text-slate-600">
                  <span>{k}</span>
                  <span>{formatCents(summary.by_category_cents[k] ?? 0, summary.precision)} · {v.toFixed(0)}%</span>
                </div>
                <div className="mt-0.5 h-2 w-full rounded-full bg-slate-200">
                  <div className="h-full rounded-full bg-orange-500" style={{ width: `${v}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
