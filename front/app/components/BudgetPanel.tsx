'use client';

import { useCallback, useEffect, useState } from 'react';

const BASE = 'http://localhost:8080/api/v1';

interface Summary {
  total_budget: number;
  spent: number;
  remaining: number;
  ratio: number;
  level: string;
  currency: string;
  by_category: Record<string, number>;
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

  const reload = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/budget/summary?user_id=${userId}&trip_id=${tripId}`);
      const j = await r.json();
      setSummary(j.summary as Summary);
    } catch (e) {
      console.error(e);
    }
  }, [userId, tripId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const setBudgetPlan = async () => {
    const r = await fetch(`${BASE}/budget`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, trip_id: tripId, total_budget: Number(budget), currency }),
    });
    const j = await r.json();
    setMessage(j.message || '预算已保存');
    reload();
  };

  const addExpense = async () => {
    const r = await fetch(`${BASE}/expense`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, trip_id: tripId, category, amount: Number(amount), currency: expCurrency, note }),
    });
    const j = await r.json();
    setMessage(j.summary?.level === 'over' ? '⚠️ 已超出预算，请注意控制消费！' : '消费已记录');
    setAmount('');
    setNote('');
    reload();
  };

  const levelMeta: Record<string, { label: string; cls: string }> = {
    safe: { label: '预算充裕', cls: 'text-emerald-600' },
    warning: { label: '接近上限', cls: 'text-amber-600' },
    over: { label: '已超预算', cls: 'text-rose-600' },
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-lg font-semibold text-slate-800">智能预算管家</h3>

      {/* 预算设置 */}
      <div className="mb-4 grid grid-cols-2 gap-2">
        <input value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="总预算金额" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        <select value={currency} onChange={(e) => setCurrency(e.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
          {['CNY', 'USD', 'EUR', 'JPY', 'HKD', 'GBP', 'SGD', 'THB'].map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button onClick={setBudgetPlan} className="col-span-2 rounded-lg bg-indigo-600 py-2 text-sm text-white hover:bg-indigo-700">设置预算</button>
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
        <button onClick={addExpense} className="col-span-2 rounded-lg bg-slate-800 py-2 text-sm text-white hover:bg-slate-900">记一笔</button>
      </div>

      {message && <p className="mb-3 text-xs font-medium text-indigo-600">{message}</p>}

      {/* 汇总 */}
      {summary && (
        <div className="rounded-xl bg-slate-50 p-4">
          <div className="flex items-end justify-between">
            <div>
              <p className="text-xs text-slate-500">已消费 / 总预算（{summary.currency}）</p>
              <p className="text-2xl font-bold text-slate-800">
                {summary.spent.toFixed(2)} / {summary.total_budget.toFixed(2)}
              </p>
            </div>
            <p className={`text-sm font-semibold ${levelMeta[summary.level]?.cls || ''}`}>
              {levelMeta[summary.level]?.label} · 剩余 {summary.remaining.toFixed(2)}
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
                  <span>{summary.by_category[k]?.toFixed(2)} · {v.toFixed(0)}%</span>
                </div>
                <div className="mt-0.5 h-2 w-full rounded-full bg-slate-200">
                  <div className="h-full rounded-full bg-indigo-400" style={{ width: `${v}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}