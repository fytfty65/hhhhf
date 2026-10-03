'use client';

/**
 * RiskPerturbationPanel —— 旅中风险告警的「最小扰动范围」说明。
 *
 * 设计取向（与 PlanGovernancePanel 一致，刻意"去 AI 化"）：
 * - 报表排印：细边框、等宽数字（tabular-nums）、小字号标签，不用渐变/发光/"智能"措辞；
 * - 只用既有色板（stone / amber / emerald / rose）；
 * - 说人话："这次会动 1 个节点""其余保留 3 个""确认后才会改"，不写"AI 已为你优化"。
 *
 * 诚实口径：算不出来时（服务不可达 / 范围不可判定 / 缺行程上下文）只显示"没算出来 + 原因"，
 * **不显示**任何受影响节点清单 —— 数据来自 `lib/riskPerturbation` 的纯解析层，本组件不自己推断。
 */

import React from 'react';
import { AlertTriangle } from 'lucide-react';
import type { RiskAlertView } from '../lib/riskPerturbation';

export default function RiskPerturbationPanel({ alert }: { alert?: RiskAlertView | null }) {
  if (!alert) return null;
  const perturbation = alert.perturbation;

  return (
    <section
      className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 p-4 text-left sm:p-5"
      aria-label="旅中风险告警"
      data-testid="risk-perturbation"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <AlertTriangle className="h-4 w-4 text-amber-600" />
            旅中风险
          </h3>
          <p className="mt-1 text-xs text-stone-500">
            {alert.city ? `${alert.city} · ` : ''}风险变化先给出影响范围；确认前不会改动你现在的方案。
          </p>
        </div>
        {perturbation?.status === 'ready' && (
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
              perturbation.keepsEnough === false ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'
            }`}
          >
            {perturbation.keepsEnough === false ? '改动较大' : '最小扰动'}
          </span>
        )}
      </div>

      {alert.changes.length > 0 && (
        <ul className="mt-3 space-y-1">
          {alert.changes.slice(0, 5).map((line, index) => (
            <li key={`${line}-${index}`} className="text-[12.5px] leading-relaxed text-stone-600">
              · {line}
            </li>
          ))}
        </ul>
      )}

      {!perturbation && (
        <p className="mt-3 rounded-lg border border-stone-200 bg-white px-3 py-2 text-[12.5px] leading-relaxed text-stone-500">
          这次告警没有附带重规划范围（只提示风险变化），所以不会自动改方案。
        </p>
      )}

      {perturbation?.status === 'ready' && (
        <div className="mt-3 rounded-lg border border-stone-200 bg-white p-3" data-testid="risk-perturbation-scope">
          <p className="font-mono text-[12.5px] font-bold tabular-nums text-stone-800">{perturbation.headline}</p>
          {perturbation.riskLabel && <p className="mt-1 text-[11px] text-stone-500">{perturbation.riskLabel}</p>}
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-stone-50 px-3 py-2">
              <p className="text-[10px] text-stone-400">这次会动</p>
              <p className="mt-0.5 text-[12.5px] font-bold text-stone-800">
                {perturbation.affected.length > 0 ? `${perturbation.affected.length} 个节点` : '未点名具体节点'}
              </p>
              {perturbation.affected.length > 0 && (
                <p className="mt-1 text-[11px] leading-relaxed text-stone-500">{perturbation.affected.join('、')}</p>
              )}
            </div>
            <div className="rounded-lg bg-stone-50 px-3 py-2">
              <p className="text-[10px] text-stone-400">其余保留</p>
              <p className="mt-0.5 text-[12.5px] font-bold text-stone-800">
                {perturbation.preservedCount != null ? `${perturbation.preservedCount} 个节点` : '未给出'}
              </p>
            </div>
          </div>
          {perturbation.actionLabel && (
            <p className="mt-2 text-[12.5px] leading-relaxed text-stone-600">怎么改：{perturbation.actionLabel}</p>
          )}
          <p className="mt-2 text-[12px] leading-relaxed text-stone-500">{perturbation.disclosure}</p>
          {perturbation.needsConfirmation && (
            <p className="mt-2 text-[12px] font-bold text-emerald-700">· 需要你确认后才会改，现在不改。</p>
          )}
        </div>
      )}

      {perturbation && perturbation.status !== 'ready' && (
        <div
          className={`mt-3 rounded-lg border bg-white px-3 py-2 ${
            perturbation.status === 'degraded' ? 'border-rose-200' : 'border-amber-300'
          }`}
          data-testid="risk-perturbation-unavailable"
        >
          <p className="text-[12.5px] font-bold text-stone-800">{perturbation.headline}</p>
          {perturbation.reason && <p className="mt-1 text-[11px] text-stone-500">原因：{perturbation.reason}</p>}
          <p className="mt-1.5 text-[12px] leading-relaxed text-stone-500">{perturbation.disclosure}</p>
        </div>
      )}

      {perturbation?.correlationId && (
        <p className="mt-2 font-mono text-[10px] text-stone-400">行程关联：{perturbation.correlationId}</p>
      )}
      {/* 只提示"要确认"是不够的：不写清楚在哪确认，用户会以为这块面板点不动
          （"看板没用"就是这么来的）。真实入口有两个，都写上，别让人猜。 */}
      {perturbation && (
        <p className="mt-2 text-[12px] leading-relaxed text-stone-500">
          确认入口：本层下方的「应急重规划提案」里选一个候选点「应用」（右上角「旅中风险推送中心」里是同一条提案）。
        </p>
      )}
    </section>
  );
}
