'use client';

/**
 * PlanFactsPanel —— 「专业诊断」里的**统一事实记录**（闭环第①段的可见形态）。
 *
 * 为什么要有它：协商纪要说的是"谁和谁争了什么"，这一块说的是**大家看的是同一份事实吗**：
 *   - 请求事实（城市/天数/预算/诉求）从哪来；
 *   - 约束里哪些是**用户明说的**、哪些是**系统策略**（不许混为一谈）；
 *   - 供应商侧真实候选有多少、有没有限流/补检索（`verified` 表示来自真实数据源）；
 *   - 决策依据（目标配比、学习画像是否生效、参考路线是否参与）；
 *   - 结果（节点数、按类别构成，标明是**推导值**）；
 *   - **缺口清单**：哪一块没取到、什么状态、为什么。
 *
 * 诚实口径（与 ai-service/core/plan_facts.py 一致，界面照抄）：
 *   - `source=unknown` / `value=null` 一律显示「未取到」，**不许显示成 0**
 *     （"不知道"和"是 0"是两件事）；
 *   - `verified=false` 的条目标「未核实」；
 *   - 唯一的事实版本号（`facts-v1-xxxxxxxx`）放在标题旁：提案是否基于同一版事实，
 *     就是靠它对齐的。
 */

import React from 'react';
import { ClipboardCheck, Database, FileWarning, Lock, Ruler, Sparkles } from 'lucide-react';

type FactEntry = {
  value?: unknown;
  source?: string;
  verified?: boolean;
  note?: string;
};

type Facts = {
  schema_version?: number;
  facts_version?: string;
  request?: Record<string, FactEntry>;
  constraints?: Record<string, FactEntry>;
  suppliers?: Record<string, FactEntry>;
  decisions?: Record<string, FactEntry>;
  outcome?: Record<string, FactEntry>;
  gaps?: { area?: string; status?: string; reason?: string }[];
};

const SOURCE_LABEL: Record<string, string> = {
  user_intent: '你说的',
  user_input: '你给的',
  policy: '系统策略',
  amap: '高德实时数据',
  derived: '系统推导',
  learned_profile: '学习到的你的偏好',
  provider: '供应商',
  web: '联网检索',
  unknown: '未取到',
};

const LABELS: Record<string, string> = {
  city: '城市',
  trip_days: '天数',
  intent_chars: '诉求字数',
  budget_total: '预算',
  requirements: '你提的要求',
  exclude_cultural: '排除人文',
  distance_tolerance: '距离容忍度',
  wants_food: '想吃美食',
  wants_scenic: '想看自然',
  park_cap: '城市公园上限',
  amap_attractions: '真实景点候选',
  amap_foods: '真实餐饮候选',
  natural_landmarks: '其中的自然景区',
  supply_status: '候选供给状态',
  out_of_town_scenic: '周边县市景区',
  widened: '候选太薄时补检索',
  composition_targets: '目标配比',
  profile_applied: '学习画像是否生效',
  reference_profile_used: '参考路线是否参与',
  nodes: '节点数',
  by_category: '按类别构成',
  days: '日程天数',
  travel_violations: '日内现实性违规',
};

function labelOf(key: string): string {
  return LABELS[key] || key;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.length ? value.map((item) => String(item)).join('、') : '（无）';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined && item !== '')
      .map(([key, item]) => `${labelOf(key)}=${formatValue(item)}`)
      .join('｜');
  }
  return String(value);
}

function FactRow({ factKey, entry }: { factKey: string; entry: FactEntry }) {
  const missing = entry?.source === 'unknown' || entry?.value === null || entry?.value === undefined;
  const text = missing ? '未取到' : formatValue(entry.value);
  return (
    <li className="text-[11.5px] leading-relaxed text-slate-600" data-testid={`facts-row-${factKey}`}>
      <span className="text-slate-500">{labelOf(factKey)}：</span>
      <span className={missing ? 'text-slate-400' : 'font-medium text-slate-700'}>{text}</span>
      <span className="ml-1 text-[10px] text-slate-400">
        [{SOURCE_LABEL[entry?.source || ''] || entry?.source || '来源未标注'}]
      </span>
      {entry?.verified === false && !missing ? <span className="ml-1 text-[10px] text-amber-700">未核实</span> : null}
      {entry?.note ? <span className="ml-1 text-[10px] text-slate-400">（{entry.note}）</span> : null}
    </li>
  );
}

function FactSection({
  title,
  icon,
  entries,
  testId,
}: {
  title: string;
  icon: React.ReactNode;
  entries?: Record<string, FactEntry>;
  testId: string;
}) {
  const keys = Object.keys(entries || {});
  if (!keys.length) return null;
  return (
    <div className="rounded-lg bg-stone-50 px-2.5 py-2" data-testid={testId}>
      <p className="flex items-center gap-1.5 text-[11px] font-black text-slate-500">
        {icon}
        {title}
      </p>
      <ul className="mt-1 space-y-0.5">
        {keys.map((key) => (
          <FactRow key={key} factKey={key} entry={(entries || {})[key]} />
        ))}
      </ul>
    </div>
  );
}

export default function PlanFactsPanel({ facts }: { facts?: Facts | null }) {
  if (!facts || typeof facts !== 'object') return null;
  const gaps = facts.gaps || [];
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-3" data-testid="plan-facts-panel">
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[11px] font-black text-slate-500">
          <ClipboardCheck className="h-3 w-3" />
          这轮方案基于的统一事实
        </p>
        <span className="font-mono text-[10px] tabular-nums text-slate-400" data-testid="facts-panel-version">
          {facts.facts_version || '（无版本号）'}
          {facts.schema_version ? `｜schema v${facts.schema_version}` : ''}
        </span>
      </div>

      <div className="mt-2 space-y-2">
        <FactSection title="请求事实（依据你的原话）" icon={<Ruler className="h-3 w-3" />} entries={facts.request} testId="facts-request" />
        <FactSection title="约束（分清哪些是你要的、哪些是系统策略）" icon={<Lock className="h-3 w-3" />} entries={facts.constraints} testId="facts-constraints" />
        <FactSection title="供应商数据（真实候选有多少）" icon={<Database className="h-3 w-3" />} entries={facts.suppliers} testId="facts-suppliers" />
        <FactSection title="定了什么（依据与来源）" icon={<Sparkles className="h-3 w-3" />} entries={facts.decisions} testId="facts-decisions" />
        <FactSection title="排出来是什么样（推导值）" icon={<ClipboardCheck className="h-3 w-3" />} entries={facts.outcome} testId="facts-outcome" />

        <div className="rounded-lg bg-stone-50 px-2.5 py-2" data-testid="facts-gaps">
          <p className="flex items-center gap-1.5 text-[11px] font-black text-slate-500">
            <FileWarning className="h-3 w-3" />
            缺口清单（{gaps.length} 条）
          </p>
          {gaps.length ? (
            <ul className="mt-1 space-y-0.5">
              {gaps.map((gap, index) => (
                <li key={`${gap.area}-${index}`} className="text-[11.5px] leading-relaxed text-slate-600">
                  · {gap.area}
                  <span className="text-slate-400">［{gap.status}］</span>
                  {gap.reason ? `：${gap.reason}` : ''}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
              这次没有登记缺口（不是"没检查"，是逐项检查后确实没缺）。
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
