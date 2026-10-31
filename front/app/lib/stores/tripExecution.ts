'use client';

import { useState } from 'react';
import type { RiskAlertView } from '../riskPerturbation';

// 行程执行 Store：出发后才会变化的状态。
// 与「规划结果」分开，是因为执行阶段会独立刷新（到访标记、风险推送、
// 重规划提案），不应该因为一次节点标记就让整个方案结果重新渲染。
export function useTripExecutionStore() {
  // 已到访节点 key（`天:名称`），本地乐观更新 + 后端 execution 记录
  const [visitedNodes, setVisitedNodes] = useState<string[]>([]);
  // 本机对节点的投票（其他人的票由房间协同 Store 维护）
  const [nodeVotes, setNodeVotes] = useState<Record<string, 'up' | 'down'>>({});
  // 旅中风险告警：只提示与提案，用户确认前不改动现有方案
  const [riskAlert, setRiskAlert] = useState<RiskAlertView | null>(null);
  // 最小扰动重规划提案与提交状态
  const [replanProposal, setReplanProposal] = useState<any | null>(null);
  const [replanBusy, setReplanBusy] = useState(false);
  // 一键换近点 / 换住这家：记住用户点了哪一处（数字=第几天、字符串='住宿'）
  const [swappingDay, setSwappingDay] = useState<number | string | null>(null);

  return {
    visitedNodes, setVisitedNodes,
    nodeVotes, setNodeVotes,
    riskAlert, setRiskAlert,
    replanProposal, setReplanProposal,
    replanBusy, setReplanBusy,
    swappingDay, setSwappingDay,
  };
}
