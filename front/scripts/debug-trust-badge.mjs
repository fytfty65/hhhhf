// 调试：trustOf 对"地图器映射后的节点"是否还能读出四态（2026-10-08）。
// 用法：node --experimental-strip-types scripts/debug-trust-badge.mjs

import { trustOf } from '../app/lib/factTrust.ts';

const cases = {
  '带完整 trust（后端直出）': {
    name: '钟楼酒店',
    trust: 'verified',
    trust_label: '已核实',
    source_type: 'rollinggo',
    freshness: 'fresh',
    derived_confidence: 0.75,
  },
  '映射器过滤后（只有空字符串）': {
    name: '钟楼酒店',
    trust: '',
    trust_label: '',
    source_type: '',
    freshness: '',
    derived_confidence: null,
  },
  '老节点（完全没有 trust 字段）': { name: '钟楼酒店', cost_estimate: '¥300' },
};

for (const [label, node] of Object.entries(cases)) {
  console.log(`${label} →`, JSON.stringify(trustOf(node)));
}
