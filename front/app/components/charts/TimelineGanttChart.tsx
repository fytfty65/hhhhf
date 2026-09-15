'use client';

import { useMemo } from 'react';
import { RadarThemeMode, radarPalette, costTierOf, parseCostNumber, RADAR_FONT } from '../../lib/radarTheme';
import { RoutePoint } from '../../InteractiveAmapComponent';

interface TimelineGanttChartProps {
  mode: RadarThemeMode;
  timelineDays: number[];
  routes: RoutePoint[];
  onSelectNode: (index: number) => void;
}

const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n) + '…' : s);

const dayOfRoute = (r: RoutePoint): number => {
  const m = r.time?.match(/Day\s*(\d+)/i) || r.desc?.match(/Day\s*(\d+)/i);
  return m ? Number(m[1]) : 1;
};

/**
 * 行程时间轴甘特图（SVG）：行 = Day，列 = 当天节点。
 * 每节点一个色块，按 costTierOf 取色；悬停显示名称/时间/成本；
 * 点击色块回传原始节点 idx 并切回 2D 导航定位。
 */
export default function TimelineGanttChart({ mode, timelineDays, routes, onSelectNode }: TimelineGanttChartProps) {
  const COLORS = radarPalette(mode);

  const groups = useMemo(
    () =>
      timelineDays.map((day) => ({
        day,
        items: routes
          .map((r, idx) => ({ r, idx }))
          .filter(({ r }) => dayOfRoute(r) === day),
      })),
    [timelineDays, routes],
  );

  const rowH = 58;
  const labelW = 70;
  const padX = 12;
  const padT = 8;
  const W = 640;
  const innerW = W - padX * 2 - labelW;

  if (groups.length === 0 || routes.length === 0) {
    return (
      <div className="h-[120px] flex items-center justify-center text-[11px]" style={{ color: COLORS.muted }}>
        暂无行程节点数据
      </div>
    );
  }

  const H = padT * 2 + groups.length * rowH;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      className="w-full h-auto"
      role="img"
      aria-label="行程时间轴甘特图"
    >
      {groups.map((g, r) => {
        const yTop = padT + r * rowH;
        const cy = yTop + rowH / 2;
        const blockH = 24;
        const dayCost = g.items.reduce((s, it) => s + parseCostNumber(it.r.cost), 0);

        return (
          <g key={`row-${g.day}`}>
            <line x1={padX} y1={yTop + rowH} x2={W - padX} y2={yTop + rowH} stroke={COLORS.divider} strokeWidth="1" />

            {/* Day 标签 + 当日合计 */}
            <text x={padX} y={cy - 2} fontSize="11" fontWeight={800} fill={COLORS.accent} style={{ fontFamily: RADAR_FONT.display }}>
              {`Day ${g.day}`}
            </text>
            <text x={padX} y={cy + 12} fontSize="9" fill={COLORS.muted} style={{ fontFamily: RADAR_FONT.data }}>
              {`¥${Math.round(dayCost)} · ${g.items.length}项`}
            </text>

            {/* 节点色块 */}
            {g.items.map((it, i) => {
              const cost = parseCostNumber(it.r.cost);
              const tier = costTierOf(cost);
              const color = tier === 'high' ? COLORS.danger : tier === 'medium' ? COLORS.warn : COLORS.accent;
              const step = innerW / Math.max(1, g.items.length);
              const blockW = Math.max(10, step - 6);
              const bx = padX + labelW + i * step + step / 2 - blockW / 2;
              const by = cy - blockH / 2;
              const showLabel = blockW >= 40;
              const nameText = showLabel ? truncate(it.r.name, Math.max(2, Math.floor(blockW / 9))) : '';

              return (
                <g key={`node-${g.day}-${i}`} style={{ cursor: 'pointer' }}>
                  <title>
                    {`${it.r.name}${it.r.time ? ` · ${it.r.time}` : ''}${it.r.cost ? ` · ${it.r.cost}` : ''}`}
                  </title>
                  <rect
                    x={bx}
                    y={by}
                    width={blockW}
                    height={blockH}
                    rx="5"
                    fill={color}
                    opacity={0.18}
                    stroke={color}
                    strokeWidth="1"
                    onClick={() => onSelectNode(it.idx)}
                  />
                  {/* 内部填充小条（成本占比） */}
                  <rect
                    x={bx}
                    y={by + blockH - 4}
                    width={Math.max(0, Math.min(blockW, (cost / 300) * blockW))}
                    height="4"
                    rx="2"
                    fill={color}
                    opacity="0.9"
                    onClick={() => onSelectNode(it.idx)}
                  />
                  {nameText && (
                    <text
                      x={bx + blockW / 2}
                      y={by + blockH / 2 + 3}
                      textAnchor="middle"
                      fontSize="9"
                      fontWeight={700}
                      fill={color}
                      style={{ fontFamily: RADAR_FONT.data }}
                      onClick={() => onSelectNode(it.idx)}
                    >
                      {nameText}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}