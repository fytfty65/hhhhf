'use client';

import { useState } from 'react';
import { Check, PiggyBank, Plus, TrendingDown } from 'lucide-react';
import { MAX_COMBINED_OPTIONS, assessOverageOptions, buildOverageOptions } from '../lib/overageAlternatives';
import type { BudgetChangeSummary, CombinedSummary, OverageOption, OverageResult } from '../lib/overageAlternatives';
import type { ScopedRouteNode } from '../lib/scopedReplan';

const money = (value: number) => `¥${Math.round(value).toLocaleString()}`;

/**
 * 「超支了怎么办」：把缺口变成可执行的替代选项。
 *
 * 只列真实报价里能先动的项（住宿单独一档，其余按报价降序），
 * "最多可省"不超过该节点当前报价，界面写明实际以重排后的报价为准；
 * 一个报价都没有时只留"按预算重排"，不编造省钱清单。
 *
 * 支持多步替代叠加：勾选 2~3 项合并成一句增量一次重排，回来后逐项核对
 * 哪一项真的落实、哪一项没省到；预算改动则走另一条核对（改没改、还要不要动）。
 */
export default function OverageAlternativesPanel({
  nodes,
  budget,
  shortfall,
  pending,
  result,
  budgetPending,
  budgetResult,
  combinedPending,
  combinedResult,
  combinedRetry,
  busy,
  onChoose,
  onChangeBudget,
  onCombine,
  onRetry,
}: {
  nodes: ScopedRouteNode[];
  budget: number | null;
  shortfall: number;
  pending: OverageOption | null;
  result: OverageResult | null;
  budgetPending: number | null;
  budgetResult: BudgetChangeSummary | null;
  combinedPending: OverageOption[] | null;
  combinedResult: CombinedSummary | null;
  combinedRetry: OverageOption[] | null;
  busy: boolean;
  onChoose: (option: OverageOption) => void;
  onChangeBudget: (amount: number) => void;
  onCombine: (options: OverageOption[]) => void;
  onRetry: (options: OverageOption[]) => void;
}) {
  const [budgetInput, setBudgetInput] = useState('');
  const [selected, setSelected] = useState<string[]>([]);

  const overrun = shortfall > 0;
  const options = overrun ? buildOverageOptions({ nodes, budget, shortfall, limit: 3 }) : [];
  const assessed = overrun ? assessOverageOptions(options, shortfall) : [];
  const combinable = assessed.filter((item) => item.option.kind !== 'budget');
  const selectedOptions = combinable
    .filter((item) => selected.includes(item.option.id))
    .map((item) => item.option)
    .slice(0, MAX_COMBINED_OPTIONS);
  const parsedBudget = Math.round(Number(budgetInput));
  const budgetValid = Number.isFinite(parsedBudget) && parsedBudget > 0;

  const toggleSelect = (id: string) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= MAX_COMBINED_OPTIONS) return current;
      return [...current, id];
    });
  };

  return (
    <section
      data-testid="overage-alternatives"
      aria-label="超支替代方案"
      className="mt-3 rounded-2xl border border-rose-200 bg-rose-50/40 p-3 shadow-[0_8px_30px_rgba(67,45,25,0.06)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-1.5 text-sm font-black text-rose-800">
            <TrendingDown className="h-4 w-4" /> 超支了，可以先动这几处
          </h3>
          <p className="mt-0.5 text-[11px] leading-relaxed text-stone-600">
            {overrun
              ? `${budget ? `当前超出 ${money(shortfall)}（预算 ${money(budget)}）` : `当前超出 ${money(shortfall)}`}。下面每一项都只是候选，实际省多少以重排后的报价为准。`
              : '上一轮调整已完成，结果见下方。'}
          </p>
        </div>
      </div>

      {overrun && (
        <div className="mt-2.5 grid gap-1.5">
          {assessed.map(({ option, status, remaining }) => {
            const canCombine = option.kind !== 'budget';
            const isSelected = selected.includes(option.id);
            return (
              <div key={option.id} className="flex items-stretch gap-1.5">
                {canCombine && (
                  <button
                    type="button"
                    data-testid={`overage-select-${option.id}`}
                    aria-pressed={isSelected}
                    aria-label={`选择${option.label}`}
                    disabled={busy}
                    onClick={() => toggleSelect(option.id)}
                    className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      isSelected
                        ? 'border-orange-400 bg-orange-500 text-white'
                        : 'border-stone-300 bg-white text-stone-500 hover:bg-stone-50'
                    }`}
                  >
                    {isSelected ? <Check className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
                  </button>
                )}
                <button
                  type="button"
                  data-testid={`overage-option-${option.id}`}
                  disabled={busy}
                  onClick={() => onChoose(option)}
                  className="flex min-h-9 flex-1 items-center justify-between gap-2 rounded-xl border border-stone-200 bg-white px-2.5 py-1.5 text-left transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[11px] font-black text-stone-800">{option.label}</span>
                    <span className="mt-0.5 block text-[10px] leading-relaxed text-stone-500">{option.detail}</span>
                    {status === 'partial' && (
                      <span className="mt-0.5 block text-[10px] font-bold text-amber-700">
                        单独换它还不够，还差 {money(remaining)}
                      </span>
                    )}
                    {status === 'enough' && option.kind !== 'budget' && (
                      <span className="mt-0.5 block text-[10px] font-bold text-emerald-700">单独换它就能补齐缺口</span>
                    )}
                  </span>
                  {option.maxSaving ? (
                    <span className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">
                      <PiggyBank className="h-3 w-3" />最多省 {money(option.maxSaving)}
                    </span>
                  ) : (
                    <span className="shrink-0 text-[10px] font-bold text-stone-400">按预算压</span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {overrun && combinable.length >= 2 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            data-testid="overage-combine"
            disabled={busy || selectedOptions.length < 2}
            onClick={() => onCombine(selectedOptions)}
            className="min-h-8 rounded-lg border border-orange-300 bg-orange-50 px-2.5 text-[11px] font-black text-orange-800 transition-colors hover:bg-orange-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {selectedOptions.length >= 2
              ? `合并重排这 ${selectedOptions.length} 项`
              : `勾选 2 项可一次重排${selectedOptions.length > 0 ? `（已选 ${selectedOptions.length}）` : ''}`}
          </button>
          <span className="text-[10px] text-stone-400">合并后会在回来后逐项核对哪项落实了</span>
        </div>
      )}

      {overrun && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="overage-budget-editor">
          <span className="text-[10px] font-bold text-stone-400">预算不够？改成</span>
          <input
            data-testid="overage-budget-input"
            type="number"
            min={1}
            inputMode="numeric"
            value={budgetInput}
            onChange={(event) => setBudgetInput(event.target.value)}
            placeholder={budget ? money(budget) : '金额'}
            className="h-8 w-24 rounded-lg border border-stone-300 bg-white px-2 text-[11px] font-bold text-stone-700 outline-none focus:border-orange-400"
          />
          <button
            type="button"
            data-testid="overage-budget-apply"
            disabled={busy || !budgetValid}
            onClick={() => onChangeBudget(parsedBudget)}
            className="min-h-8 rounded-lg border border-stone-300 bg-white px-2.5 text-[11px] font-bold text-stone-700 transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            按新预算核对
          </button>
        </div>
      )}

      {pending && (
        <p data-testid="overage-pending" className="mt-2 text-[11px] font-bold text-orange-700">
          正在重排：{pending.label}，其它安排尽量保持原样…
        </p>
      )}
      {budgetPending != null && (
        <p data-testid="overage-budget-pending" className="mt-2 text-[11px] font-bold text-orange-700">
          正在按新预算 {money(budgetPending)} 重排，其它安排尽量保持原样…
        </p>
      )}
      {combinedPending && combinedPending.length > 0 && (
        <p data-testid="overage-combined-pending" className="mt-2 text-[11px] font-bold text-orange-700">
          {combinedPending.length > 1
            ? `正在合并重排：${combinedPending.map((option) => option.label).join(' + ')}…`
            : `正在补做：${combinedPending.map((option) => option.label).join(' + ')}…`}
        </p>
      )}
      {!pending && result && (
        <p
          data-testid="overage-result"
          data-tone={result.tone}
          className={`mt-2 text-[11px] font-bold leading-relaxed ${result.tone === 'ok' ? 'text-emerald-700' : 'text-amber-700'}`}
        >
          {result.text}
        </p>
      )}
      {budgetPending == null && budgetResult && (
        <p
          data-testid="overage-budget-result"
          data-tone={budgetResult.tone}
          className={`mt-2 text-[11px] font-bold leading-relaxed ${budgetResult.tone === 'ok' ? 'text-emerald-700' : 'text-amber-700'}`}
        >
          {budgetResult.text}
        </p>
      )}
      {combinedPending == null && combinedResult && (
        <div className="mt-2">
          <p
            data-testid="overage-combined-result"
            data-tone={combinedResult.tone}
            className={`text-[11px] font-bold leading-relaxed ${combinedResult.tone === 'ok' ? 'text-emerald-700' : 'text-amber-700'}`}
          >
            {combinedResult.text}
          </p>
          {combinedRetry && combinedRetry.length > 0 && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                data-testid="overage-retry-unapplied"
                disabled={busy}
                onClick={() => onRetry(combinedRetry)}
                className="min-h-8 rounded-lg border border-orange-300 bg-orange-50 px-2.5 text-[11px] font-black text-orange-800 transition-colors hover:bg-orange-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                只补做没落实的 {combinedRetry.length} 项
              </button>
              <span className="text-[10px] text-stone-400">已经换好的项不会再动</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
