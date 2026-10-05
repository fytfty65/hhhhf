'use client';

/**
 * NegotiationDiagnostics —— 「专业诊断」里的**协商纪要 + 取价出处**。
 *
 * 为什么单独放这里（而不是塞进行程核对面板）：
 *   - 行程核对面板回答"这一趟排得合不合适"（给人读的结论）；
 *   - 专业诊断回答"系统到底怎么想的、依据从哪来"（给要追根究底的人看）。
 *   "谁参与了协商、谁和谁冲突、裁决凭什么、协商了几轮、每条价格从哪个页面来"属于后者。
 *
 * 诚实口径（与后端一致，界面照抄）：
 *   - 没参与博弈的智能体**如实列出**（含原因），不假装协同过；
 *   - 没有分歧时明说"这次确实一致"，而不是留空让人猜；
 *   - 价格一律标"估算"，带来源链接与日期，拿不到就写"未取到"。
 */

import React from 'react';
import { GitCompare, Link as LinkIcon, MessagesSquare, Scale, Users } from 'lucide-react';

type Negotiation = {
  facts_version?: string;
  agents?: { agent_id?: string; domain?: string; prefers?: string; candidates?: number; status?: string }[];
  inactive?: { agent_id?: string; reason?: string }[];
  conflicts?: { code?: string; between?: string[]; detail?: string; severity?: string }[];
  arbitration?: { code?: string; decision?: string; mechanism?: string; why?: string }[];
  revision?: {
    round?: number;
    rounds?: { round?: number; conflicts_before?: string[]; conflicts_after?: string[]; resolved?: string[] }[];
    revised_agents?: string[];
    conflicts_before?: string[];
    conflicts_after?: string[];
    resolved?: string[];
    changed?: { agent_id?: string; applied?: Record<string, { limit?: number; dropped_count?: number }>; candidates_after?: number }[];
    convergence?: { code?: string; reason?: string };
  };
  notes?: string[];
};

type PriceAudit = {
  provider_reachable?: boolean | null;
  provider_status?: { error?: string };
  looked_up?: number;
  filled_from_web?: number;
  not_looked_up?: number;
  budget_seconds?: number;
  timed_out?: number;
  skipped_reason?: string;
  error?: string;
  pending?: { name?: string; kind?: string; reason?: string; url?: string; estimated?: boolean; value?: number | null }[];
};

function SectionTitle({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-1.5 text-[11px] font-black text-slate-500">
      {icon}
      {children}
    </p>
  );
}

export default function NegotiationDiagnostics({
  negotiation,
  priceAudit,
}: {
  negotiation?: Negotiation | null;
  priceAudit?: PriceAudit | null;
}) {
  const hasNegotiation = Boolean(
    negotiation && ((negotiation.agents?.length ?? 0) > 0 || (negotiation.conflicts?.length ?? 0) > 0),
  );
  const pending = priceAudit?.pending || [];
  const hasPrice = Boolean(priceAudit);
  if (!hasNegotiation && !hasPrice) return null;

  const revision = negotiation?.revision;
  const rounds = revision?.rounds || [];

  return (
    <div className="mt-3 space-y-3" data-testid="negotiation-diagnostics">
      {hasNegotiation && (
        <div className="rounded-xl border border-stone-200 bg-white p-3">
          <div className="flex items-start justify-between gap-2">
            <SectionTitle icon={<Users className="h-3 w-3" />}>这轮是怎么协商出来的</SectionTitle>
            {negotiation?.facts_version && (
              <span className="font-mono text-[10px] tabular-nums text-slate-400" data-testid="diagnostics-facts-version">
                {negotiation.facts_version}
              </span>
            )}
          </div>

          {/* 立场：谁提了什么、站在哪一边 */}
          {(negotiation?.agents?.length ?? 0) > 0 && (
            <ul className="mt-2 space-y-0.5" data-testid="diagnostics-stances">
              {(negotiation?.agents || []).map((agent) => (
                <li key={`${agent.agent_id}-${agent.domain}`} className="text-[11.5px] leading-relaxed text-slate-600">
                  · {agent.agent_id}
                  {agent.prefers ? `：${agent.prefers}` : ''}
                  {typeof agent.candidates === 'number' ? `（${agent.candidates} 个候选）` : ''}
                  {agent.status && agent.status !== 'ok' ? `［${agent.status}］` : ''}
                </li>
              ))}
            </ul>
          )}

          {/* 分歧 */}
          {(negotiation?.conflicts?.length ?? 0) > 0 ? (
            <ul className="mt-2 space-y-1" data-testid="diagnostics-conflicts">
              {(negotiation?.conflicts || []).map((conflict) => (
                <li key={conflict.code} className="text-[11.5px] leading-relaxed text-amber-800">
                  ⚔ {conflict.severity === 'hard' ? '硬冲突' : '可协商'}：{conflict.detail || conflict.code}
                  {(conflict.between?.length ?? 0) > 0 ? `（${conflict.between?.join(' ↔ ')}）` : ''}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[11.5px] leading-relaxed text-slate-500" data-testid="diagnostics-no-conflicts">
              这次各智能体没有显式分歧（是逐条检查过且一致，不是没看）。
            </p>
          )}

          {/* 裁决：决定 + 凭什么 + 哪一层执行 */}
          {(negotiation?.arbitration?.length ?? 0) > 0 && (
            <ul className="mt-2 space-y-1" data-testid="diagnostics-arbitration">
              {(negotiation?.arbitration || []).map((item) => (
                <li key={`${item.code}-${item.decision}`} className="text-[11.5px] leading-relaxed text-slate-600">
                  <Scale className="mr-1 inline h-3 w-3 text-slate-400" />
                  {item.decision}
                  {item.why ? `（${item.why}）` : ''}
                  {item.mechanism ? <span className="text-slate-400">　执行层：{item.mechanism}</span> : null}
                </li>
              ))}
            </ul>
          )}

          {/* 协商轮次与收敛理由 */}
          {revision && (
            <div className="mt-2 rounded-lg bg-stone-50 px-2.5 py-2" data-testid="diagnostics-revision">
              <p className="text-[11.5px] leading-relaxed text-slate-600">
                <GitCompare className="mr-1 inline h-3 w-3 text-slate-400" />
                协商 {rounds.length || revision.round || 1} 轮
                {(revision.revised_agents?.length ?? 0) > 0 ? `｜重提：${revision.revised_agents?.join('、')}` : ''}
                {revision.convergence?.reason ? `｜${revision.convergence.reason}` : ''}
              </p>
              {rounds.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {rounds.map((round) => (
                    <li key={round.round} className="text-[11px] leading-relaxed text-slate-500">
                      第 {round.round} 轮：分歧 {(round.conflicts_before?.length ?? 0)} 条 →{' '}
                      {(round.conflicts_after?.length ?? 0)} 条
                      {(round.resolved?.length ?? 0) > 0 ? `（已解决 ${round.resolved?.join('、')}）` : ''}
                    </li>
                  ))}
                </ul>
              )}
              {(revision.changed?.length ?? 0) > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {(revision.changed || []).map((item) => (
                    <li key={item.agent_id} className="text-[11px] leading-relaxed text-slate-500">
                      · {item.agent_id} 按裁决去掉：
                      {Object.entries(item.applied || {})
                        .map(([key, value]) => `${key} ${value?.dropped_count ?? 0} 条`)
                        .join('；')}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* 没参与的：如实列出 */}
          {(negotiation?.inactive?.length ?? 0) > 0 && (
            <p className="mt-2 text-[11px] leading-relaxed text-slate-400" data-testid="diagnostics-inactive">
              这轮没参与博弈：{(negotiation?.inactive || []).map((item) => `${item.agent_id}（${item.reason}）`).join('；')}
            </p>
          )}
        </div>
      )}

      {hasPrice && (
        <div className="rounded-xl border border-stone-200 bg-white p-3" data-testid="diagnostics-price-provenance">
          <SectionTitle icon={<LinkIcon className="h-3 w-3" />}>价格是从哪来的</SectionTitle>
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-600">
            {priceAudit?.skipped_reason
              ? `没有补价：${priceAudit.skipped_reason}`
              : `联网查了 ${priceAudit?.looked_up ?? 0} 个缺价节点，补到 ${priceAudit?.filled_from_web ?? 0} 条` +
                (priceAudit?.not_looked_up ? `；因时间预算还有 ${priceAudit.not_looked_up} 个没查` : '') +
                (priceAudit?.timed_out ? `；其中 ${priceAudit.timed_out} 个是取价预算用完没轮到（不是搜不到）` : '') +
                (priceAudit?.budget_seconds ? `（预算 ${priceAudit.budget_seconds}s）` : '')}
          </p>
          {priceAudit?.provider_reachable === false && (
            <p className="mt-1 text-[11.5px] leading-relaxed text-amber-800">
              取价源不可达：{priceAudit.provider_status?.error || '未知原因'}
            </p>
          )}
          {priceAudit?.error && (
            <p className="mt-1 text-[11.5px] leading-relaxed text-amber-800">补价报错：{priceAudit.error}</p>
          )}
          {pending.length > 0 ? (
            <ul className="mt-2 space-y-1" data-testid="diagnostics-pending-links">
              {pending.slice(0, 6).map((item) => (
                <li key={`${item.name}-${item.url}`} className="text-[11.5px] leading-relaxed text-slate-600">
                  <MessagesSquare className="mr-1 inline h-3 w-3 text-slate-300" />
                  {item.name}
                  {item.value != null ? `：¥${item.value}` : ''}
                  {item.estimated ? '（估算）' : ''}
                  {item.reason ? ` — ${item.reason}` : ''}
                  {item.url ? (
                    <>
                      {' '}
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-orange-700 underline decoration-dotted"
                      >
                        打开来源核对
                      </a>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[11.5px] leading-relaxed text-slate-500">没有需要你确认的价格。</p>
          )}
        </div>
      )}
    </div>
  );
}
