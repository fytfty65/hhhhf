'use client';

import { useState } from 'react';
import { RadarThemeMode, radarPalette, costTierOf, RADAR_FONT } from '../../lib/radarTheme';

export interface CostBarNode {
  name: string;
  cost: string;
}

interface CostBarChartProps {
  mode: RadarThemeMode;
  nodes: CostBarNode[];
  maxNodeCost: number;
  onSelectNode: (name: string) => void;
}

const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

/**
 * 节点成本条形图（SVG）：横向条按成本占比绘制，
 * 颜色统一走 costTierOf 语义（高=danger / 中=warn / 普=accent）。
 * 点击条块高亮并联动 2D 导航定位。
 */
export default function CostBarChart({ mode, nodes, maxNodeCost, onSelectNode }: CostBarChartProps) {
  const COLORS = radarPalette(mode);
  const [hover, setHover] = useState<number | null>(null);

  const labelW = 128;
  const valueW = 62;
  const rowH = 36;
  const padX = 12;
  const padY = 6;
  const W = 640;
  const barMax = W - padX * 2 - labelW - valueW - 12;

  if (nodes.length === 0) {
    return (
      <div className="h-[120px] flex items-center justify-center text-[11px]" style={{ color: COLORS.muted }}>
        暂无节点成本数据
      </div>
    );
  }

  const H = padY * 2 + nodes.length * rowH;

  const costOf = (n: CostBarNode) => {
    const match = String(n.cost || '').match(/[\d]+(?:[,.]\d+)*/);
    return match ? parseFloat(match[0].replace(/,/g, '')) : 0;
  };

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      className="w-full h-auto"
      onMouseLeave={() => setHover(null)}
      role="img"
      aria-label="节点成本分布条形图"
    >
      {nodes.map((n, i) => {
        const cost = costOf(n);
        const tier = costTierOf(cost);
        const color = tier === 'high' ? COLORS.danger : tier === 'medium' ? COLORS.warn : COLORS.accent;
        const w = Math.max(4, Math.round((cost / (maxNodeCost || 1)) * barMax));
        const cy = padY + i * rowH + rowH / 2;
        const isActive = hover === i;

        return (
          <g
            key={`bar-${i}`}
            style={{ cursor: 'pointer' }}
            onMouseEnter={() => setHover(i)}
            onClick={() => onSelectNode(n.name)}
          >
            {/* 透明点击命中区（整行） */}
            <rect x={0} y={padY + i * rowH} width={W} height={rowH} fill="transparent" />
            {/* 名称 */}
            <text
              x={padX}
              y={cy}
              fontSize="10"
              fontWeight={600}
              fill={isActive ? color : COLORS.muted}
              style={{ fontFamily: RADAR_FONT.data }}
            >
              {truncate(n.name || '—', 9)}
            </text>
            {/* 轨道 */}
            <rect x={padX + labelW} y={cy - 7} width={barMax} height={14} rx="7" fill={COLORS.divider} />
            {/* 数值条 */}
            <rect
              x={padX + labelW}
              y={cy - 7}
              width={w}
              height={14}
              rx="7"
              fill={color}
              opacity={isActive ? 1 : 0.85}
            />
            {/* 数值 */}
            <text
              x={padX + labelW + barMax + 8}
              y={cy + 3}
              fontSize="10"
              fontWeight={700}
              fill={color}
              style={{ fontFamily: RADAR_FONT.data }}
            >
              {n.cost || '—'}
            </text>
          </g>
        );
      })}
    </svg>
  );
}